import test from "node:test";
import assert from "node:assert/strict";
import {validateConfig,rendererURL,coordinates,projectStates,projectHistory,historyCommand,createNonce,reserveBrowserLoad} from "../core.js";
const now=Date.parse("2026-10-17T12:00:00Z");
const selected=[{entity:"person.synthetic"},{entity:"zone.synthetic"}];

test("nonce uses sixteen fresh CSPRNG bytes without requiring randomUUID",()=>{
  let calls=0;
  const cryptoSource={getRandomValues(bytes){assert.ok(bytes instanceof Uint8Array);assert.equal(bytes.length,16);for(let i=0;i<bytes.length;i++)bytes[i]=calls*16+i;calls++;return bytes;}};
  const first=createNonce(cryptoSource),second=createNonce(cryptoSource);
  assert.equal(first,"000102030405060708090a0b0c0d0e0f");
  assert.match(second,/^[0-9a-f]{32}$/);assert.notEqual(first,second);assert.equal(calls,2);
});
test("nonce fails closed on missing or throwing CSPRNG with a safe useful message",()=>{
  for(const source of [null,{}, {getRandomValues:42},{getRandomValues(){throw Error("private failure detail");}}])assert.throws(()=>createNonce(source),error=>error.message.includes("Secure random generation is unavailable") && !error.message.includes("private failure detail"));
});

test("renderer must use a separate HTTPS origin without URL credentials or query",()=>{
  const parent="https://ha.example.test";
  assert.equal(rendererURL("https://renderer.example.test/v1/renderer.html",parent).origin,"https://renderer.example.test");
  for(const value of [parent+"/renderer.html","http://renderer.example.test/renderer.html","https://user:pass@renderer.example.test/renderer.html","https://renderer.example.test/renderer.html?key=x","https://renderer.example.test/renderer.html#x","https://renderer.example.test/not-renderer"])assert.throws(()=>rendererURL(value,parent));
});

test("trial accepts only bounded explicit location entities and empty/restricted-format key",()=>{
  const c=validateConfig({type:"custom:edsys-google-maps-trial",entities:["person.synthetic"]});assert.equal(c.api_key,"");assert.equal(c.hours_to_show,2);
  for(const entities of [[],["light.synthetic"],["person.synthetic","person.synthetic"],Array.from({length:11},(_,i)=>`person.synthetic_${i}`)])assert.throws(()=>validateConfig({...c,entities}));
  assert.throws(()=>validateConfig({...c,api_key:"not-a-browser-key"}));assert.throws(()=>validateConfig({...c,hours_to_show:48}));
});
test("saved map style and approved fallback survive normalization with backward-compatible defaults",()=>{
  const base={type:"custom:edsys-google-maps-trial",entities:["person.synthetic"]};
  const defaults=validateConfig(base);assert.equal(defaults.default_map_type,"roadmap");assert.equal(defaults.fallback_path,"/map");
  for(const default_map_type of ["roadmap","satellite","hybrid"])for(const fallback_path of ["/map","/map-fallback"]){
    const config=validateConfig({...base,default_map_type,fallback_path});
    assert.equal(config.default_map_type,default_map_type);assert.equal(config.fallback_path,fallback_path);
    assert.deepEqual(validateConfig(JSON.parse(JSON.stringify(config))),config);
  }
});
test("map defaults reject unsupported styles, arbitrary URLs, paths and wrong types",()=>{
  const base={type:"custom:edsys-google-maps-trial",entities:["person.synthetic"]};
  for(const default_map_type of [null,"","Hybrid","terrain",0,false,{},"hybrid\"><script>"])assert.throws(()=>validateConfig({...base,default_map_type}));
  for(const fallback_path of [null,"","/map/","/map-fallback/","/map?x","/map#x","//other.example.test/map","https://other.example.test/map","javascript:alert(1)",0,false,{}])assert.throws(()=>validateConfig({...base,fallback_path}));
});
test("invalid or string coordinates cannot turn into false points",()=>{
  for(const value of [{latitude:"1",longitude:2},{latitude:91,longitude:2},{latitude:1,longitude:Infinity},null])assert.equal(coordinates(value),null);
  assert.deepEqual(coordinates({latitude:0,longitude:0}),{lat:0,lng:0});
});
test("only selected fields cross the boundary; unavailable locations and entity IDs do not",()=>{
  const states={"person.synthetic":{state:"home",last_updated:new Date(now-60000).toISOString(),attributes:{latitude:1,longitude:2,friendly_name:"Synthetic person",gps_accuracy:9,access_token:"never-pass",secret:"never-pass"}},"zone.synthetic":{state:"unavailable",attributes:{latitude:5,longitude:6}},"person.unselected":{state:"home",attributes:{latitude:10,longitude:20}}};
  const output=projectStates(states,selected,now);assert.equal(output.length,2);assert.deepEqual(output[0].position,{lat:1,lng:2});assert.equal(output[0].age_minutes,1);assert.equal(output[1].position,null);assert.equal(output[1].label,"Location 2");
  const wire=JSON.stringify(output);for(const forbidden of ["secret","access_token","never-pass","person.synthetic","zone.synthetic","unselected"])assert.ok(!wire.includes(forbidden));
});
test("unknown or future update time is explicit and bounded",()=>{
  const out=projectStates({"person.synthetic":{state:"unknown",last_updated:new Date(now+1000).toISOString(),attributes:{latitude:1,longitude:2}}},selected,now);assert.equal(out[0].age_minutes,null);assert.equal(out[0].position,null);
});
test("history reads are permission-filtered native queries for selected trackers only",()=>{
  const command=historyCommand(selected,now);assert.equal(command.type,"history/history_during_period");assert.deepEqual(command.entity_ids,["person.synthetic"]);assert.equal(Date.parse(command.end_time)-Date.parse(command.start_time),7200000);assert.equal(command.no_attributes,false);
});
test("history strips extra data and limits coordinate/time range and point count",()=>{
  const rows=Array.from({length:1000},(_,i)=>({s:"home",lu:(now-60000+i)/1000,a:{latitude:1,longitude:2,secret:"never-pass"}}));rows.push({s:"home",lu:(now+1000)/1000,a:{latitude:3,longitude:4}});
  const result=projectHistory({"person.synthetic":rows,"person.unselected":rows},selected,now);assert.equal(result.length,2);assert.ok(result[0].segments.flat().length<=101);assert.ok(result[0].segments.flat().every(point=>point.lat===1));assert.ok(!JSON.stringify(result).includes("secret"));
});
test("old and unknown history states do not become current trails",()=>{
  const result=projectHistory({"person.synthetic":[{s:"unknown",lu:now/1000,a:{latitude:1,longitude:2}},{s:"home",lu:(now-8000000)/1000,a:{latitude:1,longitude:2}}]},selected,now);assert.deepEqual(result[0].segments,[]);
});
test("history projection retains the requested window boundary despite response delay",()=>{
  const command=historyCommand(selected,now);
  const rows=[{s:"home",lu:(now-7200000)/1000,a:{latitude:1,longitude:2}},{s:"home",lu:(now-7190000)/1000,a:{latitude:1.1,longitude:2.1}}];
  const result=projectHistory({"person.synthetic":rows},selected,Date.parse(command.end_time));assert.equal(result[0].segments[0].length,2);
});
test("browser guard limits starts and refuses blocked/corrupt storage",()=>{
  const data=new Map();const storage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)};
  for(let i=1;i<=20;i++)assert.equal(reserveBrowserLoad(storage,now),i);assert.throws(()=>reserveBrowserLoad(storage,now));assert.equal(reserveBrowserLoad(storage,now+86400000),1);
  assert.throws(()=>reserveBrowserLoad({getItem:()=>"bad json",setItem:()=>{}},now));assert.throws(()=>reserveBrowserLoad({getItem:()=>{throw Error("blocked");}},now));
});
