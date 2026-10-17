import test from "node:test";
import assert from "node:assert/strict";
import {validateConfig,rendererURL,coordinates,projectStates,projectHistory,historyCommand,reserveBrowserLoad} from "../core.js";
const now=Date.parse("2026-10-17T12:00:00Z");
const selected=[{entity:"person.synthetic"},{entity:"zone.synthetic"}];

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
