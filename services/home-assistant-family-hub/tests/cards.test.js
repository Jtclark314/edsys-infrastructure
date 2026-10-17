import test from 'node:test';
import assert from 'node:assert/strict';
import {dateParts, nextDay, wallTimeInstant, buildCalendarEvent, calendarEventMatches,
  EventSession, ShoppingSession, parseTodoResponse, draftLines, available} from '../family-hub-card.js';
const eventDraft = (more = {}) => ({summary:'Family dinner', date:'2026-10-18', allDay:false, time:'18:00', duration:'60', description:'', location:'', repeat:'none', ...more});
const zone = 'America/New_York';
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => { resolve=yes; reject=no; }); return {promise,resolve,reject}; };
const hass = () => ({connected:true, connection:{connected:true}, config:{time_zone:zone}, states:{
  'calendar.family':{state:'off',attributes:{supported_features:7}},
  'todo.family':{state:'0',attributes:{supported_features:15}},
  'todo.other':{state:'0',attributes:{supported_features:15}}
}, callWS:async()=>{}, callApi:async()=>[]});
const response = (items = [], entity = 'todo.family') => ({context:{id:'synthetic'},response:{[entity]:{items}}});
const item = (uid, summary, status = 'needs_action') => ({uid,summary,status});
const returnedEvent = event => ({...event,start:event.dtstart,end:event.dtend,uid:'synthetic-new'});

test('calendar dates validate actual days and exclusive all-day end over year/leap boundaries', () => {
  assert.equal(nextDay('2026-12-31'),'2027-01-01'); assert.equal(nextDay('2028-02-28'),'2028-02-29');
  assert.equal(nextDay('2028-02-29'),'2028-03-01');
  for (const date of ['2026-02-29','2026-04-31','2026-13-01','2026-00-01','2026-01-00','18/10/2026','1969-12-31']) assert.throws(()=>dateParts(date));
  const event=buildCalendarEvent(eventDraft({allDay:true,date:'2028-02-29'}),zone);
  assert.equal(event.dtstart,'2028-02-29');assert.equal(event.dtend,'2028-03-01');
});
test('floating home-zone payload is independent of browser/host timezone', () => {
  const old=process.env.TZ;
  try {
    process.env.TZ='Pacific/Honolulu'; const first=buildCalendarEvent(eventDraft(),zone);
    process.env.TZ='Asia/Tokyo'; assert.deepEqual(buildCalendarEvent(eventDraft(),zone),first);
    assert.equal(first.dtstart,'2026-10-18T18:00:00');assert.equal(first.dtend,'2026-10-18T19:00:00');
  } finally { if(old===undefined)delete process.env.TZ;else process.env.TZ=old; }
});
test('DST gaps and folds at either endpoint are rejected; elapsed durations cross spring correctly', () => {
  assert.throws(()=>wallTimeInstant('2027-03-14','02:30',zone),/skipped/);
  assert.throws(()=>wallTimeInstant('2026-11-01','01:30',zone),/twice/);
  assert.throws(()=>buildCalendarEvent(eventDraft({date:'2026-11-01',time:'00:30',duration:60}),zone),/twice/);
  const spring=buildCalendarEvent(eventDraft({date:'2027-03-14',time:'01:30',duration:120}),zone);
  assert.equal(spring.dtstart,'2027-03-14T01:30:00');assert.equal(spring.dtend,'2027-03-14T04:30:00');
  const fall=buildCalendarEvent(eventDraft({date:'2026-11-01',time:'00:30',duration:180}),zone);
  assert.equal(fall.dtend,'2026-11-01T02:30:00');
});
test('blank/default duration, overnight events, bad fields and bounded opt-in recurrence', () => {
  assert.equal(buildCalendarEvent(eventDraft({time:'23:30',duration:''}),zone).dtend,'2026-10-19T00:30:00');
  for(const draft of [{summary:''},{time:'25:00'},{duration:0},{duration:1441},{location:'x'.repeat(201)},{repeat:'WEEKLY',count:4}]) assert.throws(()=>buildCalendarEvent(eventDraft(draft),zone));
  assert.equal(buildCalendarEvent(eventDraft({allDay:true,repeat:'WEEKLY',count:4}),zone,true).rrule,'FREQ=WEEKLY;COUNT=4');
  for(const count of [1,53,2.5]) assert.throws(()=>buildCalendarEvent(eventDraft({repeat:'DAILY',count}),zone,true));
  assert.throws(()=>buildCalendarEvent(eventDraft(),undefined),/time zone/);
});

test('monthly/yearly recurrence preserves calendar dates and finite count for birthdays', () => {
  const monthly=buildCalendarEvent(eventDraft({allDay:true,date:'2027-01-31',repeat:'MONTHLY',count:6}),zone,true);
  assert.deepEqual({start:monthly.dtstart,end:monthly.dtend,rule:monthly.rrule},{start:'2027-01-31',end:'2027-02-01',rule:'FREQ=MONTHLY;COUNT=6'});
  const birthday=buildCalendarEvent(eventDraft({allDay:true,date:'2028-02-29',repeat:'YEARLY',count:4}),zone,true);
  assert.equal(birthday.rrule,'FREQ=YEARLY;COUNT=4');assert.equal(birthday.dtend,'2028-03-01');
  assert.throws(()=>buildCalendarEvent(eventDraft({allDay:true,repeat:'YEARLY',count:0}),zone,true));
});
test('an uncertain persisted event can be manually reconciled by readback without a second write', async () => {
  const ha=hass();let writes=0,rows=[];
  ha.callApi=async()=>rows;
  ha.callWS=async payload=>{writes++;rows=[returnedEvent(payload.event)];throw Error('synthetic lost acknowledgement');};
  const s=new EventSession('calendar.family');assert.equal(await s.submit(ha,eventDraft(),false),false);
  assert.equal(s.outcome,'uncertain');assert.ok(s.event);assert.deepEqual(s.before,[]);
  assert.equal(await s.check(ha),true);assert.equal(s.outcome,'verified');assert.equal(writes,1);
});

test('timed recurring first occurrences cannot silently establish a different wall-clock duration across DST', () => {
  for (const repeat of ['DAILY','WEEKLY','MONTHLY','YEARLY']) {
    assert.throws(()=>buildCalendarEvent(eventDraft({date:'2027-03-14',time:'01:30',duration:60,repeat,count:4}),zone,true),/repeating event crosses/);
    assert.throws(()=>buildCalendarEvent(eventDraft({date:'2026-11-01',time:'00:30',duration:180,repeat,count:4}),zone,true),/repeating event crosses/);
  }
  const ordinary=buildCalendarEvent(eventDraft({date:'2027-03-14',time:'09:00',duration:60,repeat:'WEEKLY',count:4}),zone,true);
  assert.equal(ordinary.rrule,'FREQ=WEEKLY;COUNT=4');assert.equal(ordinary.dtend,'2027-03-14T10:00:00');
});
test('calendar readback handles native date/dateTime envelopes without changing XSS-like content', () => {
  const event=buildCalendarEvent(eventDraft({summary:'<img src=x onerror=alert(1)>',location:'<b>Home</b>',description:'<script>bad</script>'}),zone);
  assert.equal(event.summary,'<img src=x onerror=alert(1)>');
  const row={summary:event.summary,description:event.description,location:event.location,start:{dateTime:'2026-10-18T22:00:00+00:00'},end:{dateTime:'2026-10-18T23:00:00Z'}};
  assert.equal(calendarEventMatches(row,event,zone),true);
  const allDay=buildCalendarEvent(eventDraft({allDay:true}),zone);
  assert.equal(calendarEventMatches({summary:allDay.summary,start:{date:allDay.dtstart},end:{date:allDay.dtend}},allDay,zone),true);
  assert.equal(calendarEventMatches({...row,summary:'other'},event,zone),false);
});
test('event creation uses exact fixed native WS envelope once and verifies newly visible event', async () => {
  const ha=hass(),calls=[];let persisted;
  ha.callWS=async payload=>{calls.push(payload);persisted=payload.event;};
  ha.callApi=async(method,path)=>{assert.equal(method,'GET');assert.match(path,/^calendars\/calendar.family\?/);return persisted?[returnedEvent(persisted)]:[];};
  const session=new EventSession('calendar.family');
  assert.equal(await session.submit(ha,eventDraft(),false),true);
  assert.deepEqual(calls,[{type:'calendar/event/create',entity_id:'calendar.family',event:buildCalendarEvent(eventDraft(),zone)}]);
  assert.equal(session.outcome,'verified');
});
test('event double submit, uncertain write and reconnect never replay', async () => {
  const ha=hass(),write=deferred();let calls=0;
  ha.callWS=()=>{calls++;return write.promise;};
  const session=new EventSession('calendar.family');
  const pending=session.submit(ha,eventDraft(),false);await Promise.resolve();
  assert.equal(await session.submit(ha,eventDraft(),false),false);assert.equal(calls,1);
  write.reject(Error('synthetic transport failure'));assert.equal(await pending,false);
  assert.equal(session.outcome,'uncertain');ha.connected=false;ha.connected=true;
  await session.submit(ha,eventDraft(),false);assert.equal(calls,1);
  session.allowEditing();assert.equal(session.outcome,'idle');assert.equal(calls,1);
});
test('acknowledged calendar create with failed readback keeps outcome and explicit check only reads', async () => {
  const ha=hass();let creates=0,reads=0,event;
  ha.callWS=async payload=>{creates++;event=payload.event;};
  ha.callApi=async()=>{reads++;if(reads>1)throw Error('synthetic read failure');return [];};
  const session=new EventSession('calendar.family');
  assert.equal(await session.submit(ha,eventDraft(),false),false);assert.equal(session.outcome,'accepted');
  await session.submit(ha,eventDraft(),false);assert.equal(creates,1);
  ha.callApi=async()=>[returnedEvent(event)];assert.equal(await session.check(ha),true);assert.equal(creates,1);
});
test('an existing identical calendar event is not mistaken for proof of a new write', async () => {
  const ha=hass(),event=buildCalendarEvent(eventDraft(),zone),row=returnedEvent(event);
  ha.callApi=async()=>[row];const session=new EventSession('calendar.family');
  assert.equal(await session.submit(ha,eventDraft(),false),false);assert.equal(session.outcome,'accepted');
});
test('unavailable/disconnected and unsupported entities cannot mutate', async () => {
  const ha=hass();let writes=0;ha.callWS=async()=>{writes++;};
  ha.connection.connected=false;assert.equal(available(ha,'calendar.family'),false);
  await new EventSession('calendar.family').submit(ha,eventDraft(),false);
  ha.connection.connected=true;ha.states['calendar.family'].attributes.supported_features=0;
  await new EventSession('calendar.family').submit(ha,eventDraft(),false);
  const s=new ShoppingSession(['todo.family']);ha.states['todo.family'].state='unavailable';s.draft='Milk';await s.add(ha);
  assert.equal(writes,0);assert.equal(s.draft,'Milk');
});

test('disconnect during pre-read prevents create and keeps the unsent draft editable', async () => {
  const ha=hass(),read=deferred();let writes=0;
  ha.callApi=()=>read.promise;ha.callWS=async()=>{writes++;};
  const s=new EventSession('calendar.family');const pending=s.submit(ha,eventDraft(),false);
  ha.connection.connected=false;read.resolve([]);assert.equal(await pending,false);
  assert.equal(writes,0);assert.equal(s.outcome,'idle');assert.match(s.error,/before this event was sent/);
});
test('shopping reads both statuses from one explicit entity using response-returning native service', async () => {
  const ha=hass(),calls=[];ha.callWS=async payload=>{calls.push(payload);return response([item('1','Milk'),item('2','Bread','completed')]);};
  const s=new ShoppingSession(['todo.family']);assert.equal(await s.refresh(ha),true);
  assert.deepEqual(calls,[{type:'call_service',domain:'todo',service:'get_items',target:{entity_id:'todo.family'},service_data:{status:['needs_action','completed']},return_response:true}]);
  assert.equal(s.items.length,2);assert.ok(s.lastRead);
});
test('malformed response does not turn an unreadable list into a successful empty read', async () => {
  assert.throws(()=>parseTodoResponse({response:{'todo.family':{}}},'todo.family'));
  const ha=hass();ha.callWS=async()=>response([item('1','Existing')]);const s=new ShoppingSession(['todo.family']);await s.refresh(ha);
  const previous=s.items,stamp=s.lastRead;ha.callWS=async()=>({});assert.equal(await s.refresh(ha),false);
  assert.equal(s.items,previous);assert.equal(s.lastRead,stamp);assert.match(s.error,/could not refresh/);
});
test('one item per line preserves commas and successful addition order', async () => {
  const ha=hass(),calls=[],rows=[];
  ha.callWS=async payload=>{calls.push(payload);if(payload.service==='get_items')return response(rows);rows.push(item(String(rows.length),payload.service_data.item));};
  const s=new ShoppingSession(['todo.family']);s.draft='Milk\nApples, green\n\nBread';assert.equal(await s.add(ha),true);
  assert.deepEqual(calls.filter(c=>c.service==='add_item').map(c=>c.service_data.item),['Milk','Apples, green','Bread']);
  assert.equal(s.draft,'');assert.equal(s.items.length,3);assert.ok(s.lastRead);
  assert.equal(draftLines('Apples, green').length,1);
});
test('partial batch stops on first uncertain item and retains it plus unattempted duplicates/commas', async () => {
  const ha=hass(),calls=[];ha.callWS=async payload=>{calls.push(payload);if(payload.service==='get_items')return response([item('1','Milk')]);if(payload.service_data.item==='Bread')throw Error('synthetic failure');};
  const s=new ShoppingSession(['todo.family']);s.draft='Milk\nBread\nMilk\nApples, green';assert.equal(await s.add(ha),false);
  assert.equal(s.draft,'Bread\nMilk\nApples, green');assert.equal(s.uncertain,true);
  assert.deepEqual(calls.filter(c=>c.service==='add_item').map(c=>c.service_data.item),['Milk','Bread']);
  await s.add(ha);await s.refresh(ha);assert.equal(calls.filter(c=>c.service==='add_item').length,2);
  s.acknowledge();assert.equal(calls.filter(c=>c.service==='add_item').length,2);
});
test('shopping double-submit and writes blocked until failed readback is refreshed', async () => {
  const ha=hass(),write=deferred();let adds=0;
  ha.callWS=payload=>{if(payload.service==='add_item'){adds++;return write.promise;}throw Error('synthetic read failure');};
  const s=new ShoppingSession(['todo.family']);s.draft='Milk';const pending=s.add(ha);
  assert.equal(await s.add(ha),false);assert.equal(adds,1);write.resolve();await pending;
  assert.equal(s.draft,'');assert.equal(s.refreshNeeded,true);s.draft='Bread';await s.add(ha);assert.equal(adds,1);
  ha.callWS=async()=>response([item('1','Milk')]);await s.refresh(ha);assert.equal(s.refreshNeeded,false);
});
test('checkbox status waits for backend readback and supports uncheck without optimistic false success', async () => {
  const ha=hass(),write=deferred();let backend=[item('1','Milk')],calls=[];
  ha.callWS=async payload=>{calls.push(payload);if(payload.service==='get_items')return response(backend);await write.promise;backend=[item('1','Milk',payload.service_data.status)];};
  const s=new ShoppingSession(['todo.family']);await s.refresh(ha);const pending=s.setStatus(ha,'1','completed');
  assert.equal(s.items[0].status,'needs_action');write.resolve();await pending;assert.equal(s.items[0].status,'completed');
  await s.setStatus(ha,'1','needs_action');assert.equal(s.items[0].status,'needs_action');
  assert.deepEqual(calls.filter(c=>c.service==='update_item').map(c=>c.service_data),[{item:'1',status:'completed'},{item:'1',status:'needs_action'}]);
});
test('configured list allowlist rejects arbitrary selection and unknown item mutations', async () => {
  const ha=hass();let calls=0;ha.callWS=async()=>{calls++;return response();};
  const s=new ShoppingSession(['todo.family','todo.other']);assert.equal(s.select('todo.private'),false);
  assert.equal(await s.setStatus(ha,'not-read','completed'),false);assert.equal(calls,0);
  assert.throws(()=>new ShoppingSession(['calendar.family']));assert.throws(()=>new EventSession('calendar.family,calendar.other'));
});
test('missing shopping configuration is pending rather than a successful empty list', async () => {
  const ha=hass();let calls=0;ha.callWS=async()=>{calls++;};const s=new ShoppingSession([]);
  assert.equal(await s.refresh(ha),false);assert.equal(s.lastRead,null);assert.equal(calls,0);
});

test('missing, malformed or nonnumeric feature flags fail closed for calendar and list mutations', async () => {
  for (const features of [undefined,null,'7',NaN,Infinity,{},-1,1.5]) {
    const ha=hass();let writes=0;ha.callWS=async()=>{writes++;};
    ha.states['calendar.family'].attributes.supported_features=features;
    ha.states['todo.family'].attributes.supported_features=features;
    const event=new EventSession('calendar.family');assert.equal(await event.submit(ha,eventDraft(),false),false);
    const shopping=new ShoppingSession(['todo.family']);shopping.items=[item('target','Milk')];shopping.draft='Bread';
    assert.equal(await shopping.add(ha),false);assert.equal(await shopping.setStatus(ha,'target','completed'),false);
    assert.equal(writes,0);assert.equal(shopping.draft,'Bread');
  }
});
test('stale status readback stays pending until exact UID and requested status appear, with no replay', async () => {
  for (const [initial,desired] of [['needs_action','completed'],['completed','needs_action']]) {
    const ha=hass(),calls=[];let rows=[item('target','Same label',initial)];
    ha.callWS=async payload=>{calls.push(payload);if(payload.service==='get_items')return response(rows);};
    const s=new ShoppingSession(['todo.family']);await s.refresh(ha);
    rows=[item('target','Same label',initial),item('other','Same label',desired)];
    assert.equal(await s.setStatus(ha,'target',desired),false);
    assert.deepEqual(s.pendingStatus,{entity:'todo.family',uid:'target',status:desired});
    assert.equal(s.refreshNeeded,true);assert.doesNotMatch(s.message,/Item updated/);assert.equal(s.canWrite(ha,4),false);
    const lastRead=s.lastRead;assert.ok(lastRead);assert.equal(s.items[0].status,initial);
    s.draft='Another item';await s.add(ha);await s.setStatus(ha,'target',desired);
    rows=[item('other','Same label',desired)];assert.equal(await s.refresh(ha),true);
    assert.equal(s.refreshNeeded,true);assert.ok(s.pendingStatus);assert.doesNotMatch(s.message,/Item updated/);
    rows=[item('target','Same label',desired)];assert.equal(await s.refresh(ha),true);
    assert.equal(s.pendingStatus,null);assert.equal(s.refreshNeeded,false);assert.match(s.message,/confirmed/);
    assert.equal(calls.filter(call=>call.service==='update_item').length,1);
    assert.equal(calls.filter(call=>call.service==='add_item').length,0);
  }
});
test('missing target UID after acknowledged status update is not a confirmed deletion or completion', async () => {
  const ha=hass();let rows=[item('target','Milk')],writes=0;
  ha.callWS=async payload=>{if(payload.service==='get_items')return response(rows);writes++;rows=[];};
  const s=new ShoppingSession(['todo.family']);await s.refresh(ha);
  assert.equal(await s.setStatus(ha,'target','completed'),false);assert.equal(s.items.length,0);
  assert.ok(s.lastRead);assert.ok(s.pendingStatus);assert.equal(s.refreshNeeded,true);assert.doesNotMatch(s.message,/Item updated/);
  await s.refresh(ha);assert.equal(writes,1);assert.ok(s.pendingStatus);
  rows=[item('target','Milk','needs_action')];await s.refresh(ha);assert.ok(s.pendingStatus);
  rows=[item('target','Milk','completed')];await s.refresh(ha);assert.equal(s.pendingStatus,null);assert.equal(writes,1);
});
test('failed post-status read retains the exact pending target for later read-only confirmation', async () => {
  const ha=hass();let fail=false,writes=0;
  ha.callWS=async payload=>{if(payload.service==='get_items'){if(fail)throw Error('synthetic read outage');return response([item('target','Milk',writes?'completed':'needs_action')]);}writes++;fail=true;};
  const s=new ShoppingSession(['todo.family']);await s.refresh(ha);
  assert.equal(await s.setStatus(ha,'target','completed'),false);assert.ok(s.pendingStatus);assert.equal(s.refreshNeeded,true);
  assert.doesNotMatch(s.message,/Item updated/);fail=false;await s.refresh(ha);
  assert.equal(s.pendingStatus,null);assert.equal(s.refreshNeeded,false);assert.equal(writes,1);
});
