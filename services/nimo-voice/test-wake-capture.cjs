const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/wake-capture.js','utf8');
let count=0;
function check(value, expected) {assert.equal(value,expected);count++;}
function setup(native, frozen=false) {
 const sent=[], handlers={},events={},timeouts=new Map();let serial=0;
 const media={getUserMedia:native};
 if(frozen) Object.defineProperty(media,'getUserMedia',{writable:false});
 const window={chrome:{webview:{postMessage:v=>sent.push(v),addEventListener:(n,cb)=>handlers[n]=cb}},addEventListener:(n,cb)=>events[n]=cb};
 vm.runInNewContext(source,{window,navigator:{mediaDevices:media},crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789abc'},document:{addEventListener:(n,cb)=>events[n]=cb},DOMException,setTimeout:cb=>{timeouts.set(++serial,cb);return serial;},clearTimeout:id=>timeouts.delete(id)});
 return {media,guard:window.edsysWakeCapture,sent,events,timeouts,grant:allow=>handlers.message({data:{type:'wake-capture-release',id:sent.at(-1).substring(13),allow}})};
}
(async()=>{
 check(setup(()=>{},true).guard,undefined);
 let calls=0;
 const track={readyState:'live',stop(){this.readyState='ended';}};
 const stream={getTracks:()=>[track]};
 let s=setup(()=>{calls++;return Promise.resolve(stream);});
 const raw=s.media.getUserMedia({audio:true});await raw;check(calls,1);check(s.sent.length,0);
 s.guard.enable(true);check(s.guard.idle(),true);
 const pending=s.media.getUserMedia({audio:true});check(calls,1);check(s.sent.length,1);check(s.guard.idle(),false);
 s.grant(true);await pending;check(calls,2);check(s.guard.idle(),false);
 track.stop();check(s.guard.idle(),true);
 let rejected=s.media.getUserMedia({audio:true}).catch(e=>e.name);s.grant(false);check(await rejected,'AbortError');check(calls,2);
 rejected=s.media.getUserMedia({audio:true}).catch(e=>e.name);s.guard.cancel();s.grant(true);check(await rejected,'AbortError');check(calls,2);
 s.guard.begin();rejected=s.media.getUserMedia({audio:true}).catch(e=>e.name);[...s.timeouts.values()][0]();s.grant(true);check(await rejected,'AbortError');check(calls,2);
 s.guard.cancel();s.guard.enable(false);rejected=s.media.getUserMedia({audio:true}).catch(e=>e.name);check(await rejected,'AbortError');check(calls,2);
 s.guard.begin();await s.media.getUserMedia({audio:true});check(calls,3);
 let resolveNative;const lateTrack={readyState:'live',stop(){this.readyState='ended';}};
 s=setup(()=>new Promise(resolve=>resolveNative=resolve));s.guard.enable(true);
 rejected=s.media.getUserMedia({audio:true}).catch(e=>e.name);s.grant(true);check(s.guard.idle(),false);
 s.guard.cancel();check(s.guard.idle(),false);resolveNative({getTracks:()=>[lateTrack]});check(await rejected,'AbortError');check(lateTrack.readyState,'ended');check(s.guard.idle(),true);
 s=setup(()=>{calls++;return Promise.resolve(stream)});s.guard.enable(true);
 rejected=s.media.getUserMedia({audio:true}).catch(e=>e.name);s.events['dialog-closed']({detail:{dialog:'ha-voice-command-dialog'}});check(await rejected,'AbortError');check(s.sent.at(-1),'wake-dialog-closed');
 const before=calls;s.guard.enable(false);check(await s.media.getUserMedia({audio:true}).catch(e=>e.name),'AbortError');check(calls,before);
 s=setup(()=>{throw new Error('native failure')});s.guard.enable(true);rejected=s.media.getUserMedia({audio:true}).catch(e=>e.message);s.grant(true);check(await rejected,'native failure');check(s.guard.idle(),true);
 s=setup(()=>{});s.guard.enable(true);s.media.getUserMedia=()=>{};check(s.guard.idle(),false);
 console.log(`${count} wake capture checks passed`);
})().catch(e=>{console.error(e);process.exitCode=1;});
