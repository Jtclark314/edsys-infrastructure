// Actual Phil decorator with deferred HA replies, a fake clock and fake Audio.
// No browser, network, microphone, household command or playback device is used.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(__dirname+'/camera-view.js','utf8');
const flush=async()=>{for(let i=0;i<60;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
class Target extends EventTarget {
 constructor(){super();this.listeners=new Map();}
 addEventListener(type,fn){super.addEventListener(type,fn);if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(fn);}
 removeEventListener(type,fn){super.removeEventListener(type,fn);this.listeners.get(type)?.delete(fn);}
 count(){return [...this.listeners.values()].reduce((n,v)=>n+v.size,0);}
 fire(type){this.dispatchEvent(new Event(type));}
}
async function fixture({output='pi'}={}) {
 const sent=[],native=[],begins=[],audible=[],received=[],intervals=new Map(),timeouts=new Map();let next=0;
 class NativeAudio extends Target {
  constructor(url){super();this.url=url;this.paused=true;this.ended=false;this.readyState=0;this.currentTime=0;audible.push(this);}
  play(){this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.fire('pause');}
  removeAttribute(){}
 }
 const connection=new Target();const subscriptionResults=[];const status=()=>({active:true,audio_state:'preparing',started:false});
 connection.subscribeMessage=(callback,message,options)=>{native.push({callback,message,options});return subscriptionResults.shift()?.promise||Promise.resolve(()=>{});};
 const hass={connection,states:{},status,update:m=>({active:!['cancelled','audio_end'].includes(m.phase)}),callWS:m=>{
  sent.push(m);
  if(m.type.endsWith('/phil_begin')){const d=deferred();begins.push(d);return d.promise;}
  if(m.type.endsWith('/phil_status'))return Promise.resolve(hass.status(m));
  return Promise.resolve(hass.update(m));
 }};
 const chat={pipeline:{id:'named'}};
 const document={querySelector:s=>s==='home-assistant'?{hass}:s==='ha-assist-chat'?chat:null,querySelectorAll:()=>[]};
 const window=new Target();window.Audio=NativeAudio;
 const ctx={window,document,Promise,Event,EventTarget,
  setTimeout:(fn,ms)=>{timeouts.set(++next,{fn,ms});return next;},clearTimeout:id=>timeouts.delete(id),
  setInterval:(fn,ms)=>{intervals.set(++next,{fn,ms});return next;},clearInterval:id=>intervals.delete(id)};
 vm.createContext(ctx);vm.runInContext(source,ctx);await window.edsysSetupPhil('named');
 function start(){return connection.subscribeMessage(event=>{
  received.push(event.type);
  if(event.type==='run-start'){chat._audio=new window.Audio('/native-generated-reply');chat._audio.play();}
 },{type:'assist_pipeline/run',pipeline:'named',start_stage:'stt',end_stage:'tts'},{});}
 async function lease(index,id){begins[index].resolve({active:true,session_id:id,audio_ref:'ref-'+id,output});await flush();}
 async function timeout(ms){for(const [id,t]of [...timeouts])if(t.ms===ms){timeouts.delete(id);t.fn();}await flush();}
 async function interval(ms){for(const t of [...intervals.values()])if(t.ms===ms)t.fn();await flush();}
 const counts=()=>({intervals:intervals.size,timeouts:timeouts.size,listeners:connection.count()+window.count()+audible.reduce((n,a)=>n+a.count(),0)});
 const cancellations=()=>sent.filter(m=>m.phase==='cancelled').map(m=>m.session_id);
 return{window,hass,connection,subscriptionResults,sent,native,begins,audible,received,intervals,timeouts,chat,start,lease,timeout,interval,counts,cancellations};
}
function empty(f){assert.deepEqual(f.counts(),{intervals:0,timeouts:0,listeners:0});}
(async()=>{
 let checks=0;
 // Only the newest still-pending start can submit a pipeline, in either order.
 for(const order of [[0,1],[1,0]]){
  const f=await fixture();const a=f.start();await flush();const b=f.start();await flush();assert.equal(f.begins.length,2);
  for(const index of order)await f.lease(index,index?'new':'old');
  await Promise.all([a,b]);assert.equal(f.native.length,1);assert.equal(f.native[0].message.phil_session_id,'new');
  assert.deepEqual(f.cancellations(),['old']);assert.equal(f.window.edsysPhilBinding.current.session_id,'new');
  assert.equal(f.intervals.size,1);f.window.edsysStopPhil();await flush();empty(f);
  assert.deepEqual(f.cancellations(),['old','new']);assert.equal(f.audible.length,0);checks++;
 }
 // Two starts in one tick collapse before the cancelled one contacts HA.
 {
  const f=await fixture();const a=f.start(),b=f.start();await flush();assert.equal(f.begins.length,1);
  await f.lease(0,'new');await Promise.all([a,b]);assert.equal(f.native.length,1);
  f.window.edsysStopPhil();await flush();empty(f);assert.deepEqual(f.cancellations(),['new']);checks++;
 }
 // Stop before the first microtask submits neither a begin nor a pipeline.
 {
  const f=await fixture();const p=f.start();f.window.edsysStopPhil();await p;await flush();
  assert.equal(f.begins.length,0);assert.equal(f.native.length,0);empty(f);checks++;
 }
 // Cancellation can land after begin's finally and before its caller claims
 // the lease; ownership must transfer explicitly across that await boundary.
 {
  const f=await fixture();const p=f.start();await flush();let interrupted=false;
  f.begins[0].resolve({active:true,session_id:'gap',audio_ref:'ref-gap',output:'pi'});
  for(let i=0;i<30;i++){
   await Promise.resolve();
   const state=f.window.edsysPhilBinding;
   if(!state.pending&&!state.current){f.window.edsysStopPhil();interrupted=true;break;}
  }
  await p;await flush();assert.equal(interrupted,true);assert.equal(f.native.length,0);
  assert.deepEqual(f.cancellations(),['gap']);empty(f);checks++;
 }
 // Cancellation also owns a begin that has not returned; late leases cannot
 // submit speech, revive timers or leave connection/window listeners behind.
 for(const event of ['stop','disconnected','pagehide','beforeunload']){
  const f=await fixture();const pending=f.start();await flush();
  if(event==='stop')f.window.edsysStopPhil();else (event==='disconnected'?f.connection:f.window).fire(event);
  await pending;empty(f);await f.lease(0,'late');assert.equal(f.native.length,0);
  assert.deepEqual(f.cancellations(),['late']);empty(f);checks++;
 }
 // Timeout falls back to exactly one ordinary native subscription. Its late
 // response is cancelled without resubmitting or replaying that command.
 {
  const f=await fixture();const p=f.start();await flush();await f.timeout(2000);await p;
  assert.equal(f.native.length,1);assert.equal(f.native[0].message.type,'assist_pipeline/run');empty(f);
  await f.lease(0,'late');assert.deepEqual(f.cancellations(),['late']);assert.equal(f.native.length,1);empty(f);checks++;
 }
 // A late old lease and old native run-start must not affect a newer turn or
 // construct local audio while the newer Pi route owns its generated speech.
 {
  const f=await fixture();const a=f.start();await flush();await f.timeout(2000);await a;
  const b=f.start();await flush();await f.lease(1,'new');await b;await f.lease(0,'old');
  f.native[0].callback({type:'run-start'});assert.equal(f.audible.length,0);
  assert.equal(f.native.length,2);assert.equal(f.window.edsysPhilBinding.current.session_id,'new');
  assert.deepEqual(f.cancellations(),['old']);f.window.edsysStopPhil();await flush();empty(f);checks++;
 }
 // A delayed failure of a superseded subscription cannot stop the newer lease.
 {
  const f=await fixture(),handshake=deferred();f.subscriptionResults.push(handshake);
  const a=f.start();await flush();await f.lease(0,'old');const rejected=assert.rejects(a,/old-subscription/);
  const b=f.start();await flush();await f.lease(1,'new');await b;
  handshake.reject(Error('old-subscription'));await rejected;await flush();
  f.native[0].callback({type:'run-start'});assert.equal(f.audible.length,0);
  assert.equal(f.window.edsysPhilBinding.current.session_id,'new');assert.deepEqual(f.cancellations(),['old']);
  f.window.edsysStopPhil();await flush();empty(f);checks++;
 }
 // Stop clears real in-flight poll/heartbeat timeouts and their listeners. A
 // delayed completion cannot play or complete another turn after retirement.
 {
  const f=await fixture();const p=f.start();await flush();await f.lease(0,'one');await p;
  f.native[0].callback({type:'run-start'});const poll=deferred(),heartbeat=deferred();
  let errors=0,ended=0;const proxy=f.chat._audio;
  proxy.addEventListener('error',()=>errors++);proxy.addEventListener('ended',()=>ended++);
  f.hass.status=()=>poll.promise;f.hass.update=m=>m.phase==='heartbeat'?heartbeat.promise:{active:false};
  await f.interval(350);await f.interval(2000);assert.equal(f.timeouts.size,2);
  f.window.edsysStopPhil();await flush();empty(f);assert.deepEqual(f.cancellations(),['one']);
  poll.resolve({active:false,audio_state:'finished',started:true});heartbeat.resolve({active:true});await flush();
  empty(f);assert.equal(f.audible.length,0);assert.equal(f.native.length,1);
  assert.equal(proxy.paused,true);assert.equal(errors,1);assert.equal(ended,0);checks++;
 }
 // Native-output and safe-fallback Audio handlers are owned and removed too.
 // An optional display loss must leave this current native speech pipeline
 // running; only explicit stop/supersession may retire its native callbacks.
 for(const failure of ['inactive','rejected']){
  const f=await fixture({output:'nimo'});const p=f.start();await flush();await f.lease(0,'one');await p;
  f.native[0].callback({type:'run-start'});
  f.hass.update=()=>failure==='inactive'?{active:false}:Promise.reject(Error('display-offline'));
  await f.interval(2000);empty(f);
  for(const type of ['stt-end','intent-start','intent-end','tts-end','run-end'])f.native[0].callback({type});
  assert.deepEqual(f.received,['run-start','stt-end','intent-start','intent-end','tts-end','run-end']);
  assert.equal(f.native.length,1);assert.equal(f.audible.length,1);assert.equal(f.audible[0].paused,false);
  f.window.edsysStopPhil();f.native[0].callback({type:'run-start'});await flush();
  assert.equal(f.audible.length,1);empty(f);checks++;
 }
 {
  const f=await fixture({output:'nimo'});const p=f.start();await flush();await f.lease(0,'one');await p;
  f.native[0].callback({type:'run-start'});assert.equal(f.audible[0].count(),5);
  f.window.edsysStopPhil();await flush();empty(f);checks++;
 }
 {
  const f=await fixture();const p=f.start();await flush();await f.lease(0,'one');await p;
  f.native[0].callback({type:'run-start'});f.hass.status=()=>({active:false,audio_state:'failed',fallback_allowed:true,started:false});
  await f.interval(350);assert.equal(f.audible.length,1);assert.equal(f.audible[0].count(),3);
  f.window.edsysStopPhil();await flush();empty(f);assert.equal(f.audible[0].paused,true);checks++;
 }
 // A pipeline change during setup retires pending work without a new bind.
 {
  const f=await fixture();const p=f.start();await flush();await f.window.edsysSetupPhil('other');await p;
  await f.lease(0,'old');assert.equal(f.native.length,0);empty(f);checks++;
 }
 // Connection replacement retires old pending work; the obsolete wrapper may
 // not start another lease or interfere with the new connection's binding.
 {
  const f=await fixture();const p=f.start();await flush();const next=new Target();let nextPipelines=0;
  next.subscribeMessage=async()=>{nextPipelines++;return()=>{};};f.hass.connection=next;
  await f.window.edsysSetupPhil('named');await p;await f.lease(0,'old');await f.start();
  assert.equal(f.begins.length,1);assert.equal(f.native.length,0);assert.equal(nextPipelines,0);empty(f);checks++;
 }
 // A subscription handshake that succeeds only after cancellation is retired
 // once, rather than handing a late native subscription back to the frontend.
 {
  const f=await fixture(),handshake=deferred();f.subscriptionResults.push(handshake);let retired=0;
  const p=f.start();await flush();await f.lease(0,'old');f.window.edsysStopPhil();
  handshake.resolve(()=>{retired++;return Promise.reject(Error('already-disconnected'));});const unsubscribe=await p;unsubscribe();await flush();
  assert.equal(retired,1);assert.equal(f.native.length,1);empty(f);checks++;
 }
 console.log(JSON.stringify({checks,network_or_devices:false}));
})().catch(error=>{console.error(error);process.exit(1);});
