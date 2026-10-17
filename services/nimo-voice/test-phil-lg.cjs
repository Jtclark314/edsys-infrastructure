// Native Assist callback shape from the installed frontend, with fake HA/audio.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(__dirname+'/camera-view.js','utf8');
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
async function fixture({active=true}={}) {
 const sent=[],native=[],audible=[],timers=new Map();let next=0,cb,followups=0,status={active:true,audio_state:'preparing',started:false};
 class NativeAudio extends EventTarget {
  constructor(url){super();this.url=url;this.paused=true;this.ended=false;audible.push(this);}
  play(){this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;}
  removeAttribute(){}
 }
 const chat={pipeline:{id:'named'}};
 const connection={addEventListener(k,f){this[k]=f;},subscribeMessage:async(f,m,o)=>{cb=f;native.push({m,o});return()=>{};}};
 const hass={states:{},connection,callWS:async m=>{sent.push(m);
  if(m.type.endsWith('/nimo'))return{device_id:'registered-client'};
  if(m.type.endsWith('/phil_begin'))return{active,output:'pi',session_id:'session',audio_ref:'audio'};
  if(m.type.endsWith('/phil_status'))return status;
  return{active:true};
 }};
 const document={querySelector:s=>s==='home-assistant'?{hass}:s==='ha-assist-chat'?chat:null,querySelectorAll:()=>[]};
 const window={Audio:NativeAudio};const ctx={window,document,Promise,Event,EventTarget,setTimeout:()=>0,clearTimeout:()=>{},setInterval:(f,ms)=>{timers.set(++next,{f,ms});return next;},clearInterval:id=>timers.delete(id)};
 vm.createContext(ctx);vm.runInContext(source,ctx);await window.edsysSetupNimo('named');await window.edsysSetupPhil('named');
 async function start(){await connection.subscribeMessage(e=>{
  if(e.type==='run-start'){
   chat._audio=new window.Audio(e.data.tts_output.url);chat._audio.play();
   chat._audio.addEventListener('ended',()=>{chat._audio.pause();chat._audio.removeAttribute('src');followups++;});
   chat._audio.addEventListener('pause',()=>chat._audio?.pause());
  }
 },{type:'assist_pipeline/run',pipeline:'named',start_stage:'stt',end_stage:'tts',input:{sample_rate:16000}},{});}
 async function poll(value){status=value;for(const {f,ms}of [...timers.values()])if(ms===350)f();await flush();}
 return{window,NativeAudio,hass,connection,sent,native,audible,chat,timers,start,poll,followups:()=>followups,
  event:async type=>{cb({type,data:type==='run-start'?{tts_output:{url:'/native-private-reply'}}:{}});await flush();}};
}
(async()=>{
 let checks=0;
 const f=await fixture();await f.start();await f.event('run-start');
 assert.equal(f.native[0].m.type,'edsys_voice_knowledge/phil_pipeline');assert.equal(f.native[0].m.phil_session_id,'session');assert.equal(f.native[0].m.device_id,'registered-client');assert.equal(f.native[0].o.resubscribe,false);checks++;
 assert.equal(f.audible.length,0);assert.equal(f.window.Audio,f.NativeAudio);checks++;
 await f.event('run-end');assert.equal(f.followups(),0);assert.equal(f.timers.size,2);checks++;
 await f.poll({active:true,audio_state:'playing',started:true});assert.equal(f.followups(),0);assert.equal(f.chat._audio.paused,false);checks++;
 assert.equal(f.sent.some(m=>m.phase==='audio_start'),false);checks++;
 await f.poll({active:false,audio_state:'finished',started:true});assert.equal(f.followups(),1);assert.equal(f.timers.size,0);assert.equal(f.audible.length,0);checks++;
 for(const result of [{active:false,audio_state:'uncertain',fallback_allowed:false},{active:false,audio_state:'cancelled',fallback_allowed:false},{active:false,audio_state:'failed',fallback_allowed:true,started:true}]){
  const x=await fixture();await x.start();await x.event('run-start');await x.poll(result);assert.equal(x.audible.length,0);assert.equal(x.followups(),0);assert.equal(x.timers.size,0);checks++;
 }
 const b=await fixture();await b.start();await b.event('run-start');await b.poll({active:false,audio_state:'failed',fallback_allowed:true,started:false});
 assert.equal(b.audible.length,1);assert.equal(b.audible[0].url,'/native-private-reply');assert.equal(b.native.length,1);checks++;
 b.audible[0].dispatchEvent(new Event('ended'));assert.equal(b.followups(),1);checks++;
 const d=await fixture();await d.start();await d.event('run-start');d.connection.disconnected();await flush();
 assert.equal(d.timers.size,0);assert.equal(d.sent.filter(m=>m.phase==='cancelled').length,1);assert.equal(d.audible.length,0);checks++;
 const off=await fixture({active:false});await off.start();await off.event('run-start');assert.equal(off.native[0].m.type,'assist_pipeline/run');assert.equal(off.audible.length,1);assert.equal(off.timers.size,0);checks++;
 const r=await fixture();await r.start();await r.event('run-start');const old=r.chat._audio;await r.start();await r.event('run-start');old.pause();assert.equal(r.window.edsysPhilBinding.current.closed,false);checks++;
 console.log(JSON.stringify({checks,network_or_devices:false}));
})().catch(e=>{console.error(e);process.exit(1);});
