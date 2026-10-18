const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname+'/wake-idle.js','utf8');
let count=0;
function run(nodes={}, options={}) {
  const doc={querySelector:()=>({hass:{connection:{connected:options.connected??true}}}),
    querySelectorAll:selector=>nodes[selector]||[]};
  return vm.runInNewContext(source,{window:{edsysWakeCapture:{idle:()=>options.captureIdle??true}},document:doc,location:{pathname:options.path??'/edsys-home/home'}});
}
function check(value,expected) {assert.equal(value,expected);count++;}
check(run(),true);
check(run({}, {captureIdle:false}),false);
check(run({}, {connected:false}),false);
check(run({}, {path:'/edsys-media/master-bedroom'}),false);
check(run({'ha-voice-command-dialog':[{}]}),false);
check(run({'ha-voice-command-dialog':[{_open:true,_dialogOpen:true}]}),false);
check(run({'ha-voice-command-dialog':[{_open:false,_dialogOpen:true}]}),false);
check(run({'ha-voice-command-dialog':[{_open:false,_dialogOpen:false}]}),true);
check(run({'ha-assist-chat':[{_audioRecorder:{}}]}),false);
check(run({'ha-assist-chat':[{_audio:{paused:true,ended:false}}]}),false);
check(run({'ha-assist-chat':[{_audio:{ended:true}}]}),true);
check(run({'ha-more-info-dialog':[{}]}),false);
check(run({'ha-more-info-dialog':[{_open:true}]}),false);
check(run({'ha-more-info-dialog':[{_open:false}]}),true);
const nested={querySelectorAll:s=>s==='ha-assist-chat'?[{_audioRecorder:{}}]:[]};
check(run({'*':[{shadowRoot:nested}]}),false);
const loop={querySelectorAll:s=>s==='*'?[{shadowRoot:loop}]:[]};
check(run({'*':[{shadowRoot:loop}]}),false);
console.log(`${count} wake idle checks passed`);
