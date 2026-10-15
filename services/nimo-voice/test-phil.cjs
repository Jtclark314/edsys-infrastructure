// Real lifecycle ordering with fake HA/native Audio: no network or device I/O.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(__dirname+'/camera-view.js','utf8');
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
async function fixture({eligible=true,fail=false}={}) {
 const sent=[],native=[],intervals=new Map();let callback,nextTimer=0;
 const audio={paused:true,ended:false,readyState:0,currentTime:0,events:{},addEventListener(k,f){(this.events[k]||=[]).push(f);},fire(k){for(const f of this.events[k]||[])f();}};
 const chat={pipeline:{id:'named'},_audio:audio};
 const connection={addEventListener(k,f){this[k]=f;},subscribeMessage:async(cb,m,o)=>{callback=cb;native.push({m,o});return()=>{};}};
 const hass={connection,callWS:async m=>{sent.push(m);if(fail)throw Error('offline');return m.type.endsWith('phil_begin')?{active:eligible,session_id:'fixture-session',audio_ref:'fixture-audio'}:{active:!['audio_end','cancelled'].includes(m.phase)};}};
 const document={querySelector:s=>s==='home-assistant'?{hass}:s==='ha-assist-chat'?chat:null,querySelectorAll:()=>[]};
 const window={};
 const ctx={window,document,Promise,setTimeout:()=>0,setInterval:f=>{intervals.set(++nextTimer,f);return nextTimer;},clearInterval:id=>intervals.delete(id)};
 vm.createContext(ctx);vm.runInContext(source,ctx);await window.edsysSetupPhil('named');
 async function start(message={type:'assist_pipeline/run',pipeline:'named',start_stage:'stt'}){
  let called=0;await connection.subscribeMessage(()=>called++,message,{});return()=>called;
 }
 return {window,hass,connection,audio,chat,sent,native,intervals,start,event:async e=>{callback(e);await flush();}};
}
(async()=>{
 let checks=0;
 const f=await fixture();const count=await f.start();await f.event({type:'run-start'});
 assert.equal(f.sent.filter(m=>m.phase==='audio_start').length,0);checks++;
 f.audio.fire('waiting');await flush();assert.equal(f.sent.filter(m=>m.phase==='audio_waiting').length,0);checks++;
 await f.event({type:'intent-start'});assert.equal(f.sent.at(-1).phase,'processing');checks++;
 f.audio.paused=false;f.audio.fire('playing');await flush();
 assert.equal(f.sent.at(-1).phase,'audio_start');assert.equal(f.sent.at(-1).audio_ref,'fixture-audio');checks++;
 await f.event({type:'run-end'});assert.equal(f.intervals.size,1);assert.equal(f.sent.at(-1).phase,'audio_start');checks++;
 f.audio.fire('waiting');await flush();assert.equal(f.sent.at(-1).phase,'audio_waiting');checks++;
 f.audio.fire('playing');await flush();assert.equal(f.sent.at(-1).phase,'audio_start');checks++;
 f.audio.ended=true;f.audio.fire('pause');f.audio.fire('ended');await flush();
 assert.equal(f.sent.filter(m=>m.phase==='audio_end').length,1);assert.equal(f.intervals.size,0);assert.equal(count(),3);checks++;
 assert.equal(f.native[0].o.resubscribe,false);assert.equal(f.native[0].m.start_stage,'stt');checks++;
 assert.equal(JSON.stringify(f.sent).includes('tts_output'),false);assert.equal(JSON.stringify(f.sent).includes('text'),false);checks++;
 for(const message of [{type:'assist_pipeline/run',pipeline:'other',start_stage:'stt'},{type:'assist_pipeline/run',pipeline:'named',start_stage:'intent'},{type:'subscribe_events'}]){
  const x=await fixture();await x.start(message);assert.equal(x.sent.length,0);assert.equal(x.native.length,1);checks++;
 }
 for(const options of [{eligible:false},{fail:true}]){
  const x=await fixture(options);const cb=await x.start();await x.event({type:'run-start'});assert.equal(x.native.length,1);assert.equal(cb(),1);assert.equal(x.intervals.size,0);checks++;
 }
 const s=await fixture();await s.start();await s.event({type:'run-start'});s.window.edsysStopPhil();await flush();const before=s.sent.length;
 s.audio.fire('playing');await flush();assert.equal(s.sent.length,before);assert.equal(s.sent.at(-1).phase,'cancelled');checks++;
 const d=await fixture();await d.start();d.connection.disconnected();await flush();assert.equal(d.intervals.size,0);assert.equal(d.sent.at(-1).phase,'cancelled');checks++;
 const r=await fixture();await r.start();await r.event({type:'run-start'});const old=r.audio;await r.start();r.chat._audio={...old,events:{}};await r.event({type:'run-start'});
 old.fire('ended');await flush();assert.equal(r.sent.filter(m=>m.phase==='audio_end').length,0);checks++;
 console.log(JSON.stringify({checks,network_or_devices:false}));
})().catch(e=>{console.error(e);process.exit(1);});
