// Actual start/stop scripts with deferred frontend operations and native action
// stand-ins. No browser, network, microphone, speech or playback is used.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const startSource = fs.readFileSync(__dirname+'/start-assist.js', 'utf8')
  .replaceAll('__PIPELINE_NAME__', JSON.stringify('test pipeline'));
const stopSource = fs.readFileSync(__dirname+'/stop-assist.js', 'utf8');
const flush = async () => { for (let i=0; i<30; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve=yes; reject=no; });
  return {promise, resolve, reject};
}
function fixture({hold, noTarget=false, missingPipeline=false, optionalFailure=false, captureGuard=true}={}) {
  const waits=[], messages=[], actions=[], cards=[], calls=[];
  let closed=0, philStops=0, captureCancels=0;
  const value = stage => stage==='pipeline' ? {pipelines: missingPipeline ? [] : [{id:'test-id', name:'test pipeline'}]} : undefined;
  function step(stage) {
    calls.push(stage);
    if (stage===hold) {
      const pending=deferred();waits.push(pending);return pending.promise;
    }
    if (optionalFailure && ['nimo','phil'].includes(stage)) return Promise.reject(Error('optional unavailable'));
    return Promise.resolve(value(stage));
  }
  const app={hass:{callWS:() => step('pipeline')}};
  const existing={parentNode:{appendChild(card) { card.attached=true; }}};
  const document={
    querySelector:selector => selector==='home-assistant' ? app : null,
    querySelectorAll:selector => selector==='*' ? [{shadowRoot:root}] : [],
    createElement:() => {
      const card={style:{}, attached:false, removed:false,
        setConfig(config) { this.config=config; },
        get updateComplete() { return step('card'); },
        shadowRoot:{querySelector:() => noTarget ? null : {dispatchEvent(event) { actions.push(event); }}},
        remove() { this.attached=false;this.removed=true; }};
      cards.push(card);return card;
    }
  };
  const root={
    querySelector:selector => selector.startsWith('hui-') ? existing
      : selector==='ha-voice-command-dialog' ? {closeDialog() { closed++; }} : null,
    querySelectorAll:() => []
  };
  const window={
    edsysSetupNimo:() => step('nimo'), edsysSetupPhil:() => step('phil'),
    edsysStopPhil:() => { philStops++; }, edsysNimoBinding:{sequence:0},
    chrome:{webview:{postMessage:value => messages.push(value)}}
  };
  if (captureGuard) window.edsysWakeCapture={cancel() { captureCancels++; }};
  const context=vm.createContext({window, document, customElements:{get:() => true},
    CustomEvent:class { constructor(type, options) { this.type=type;Object.assign(this, options); } }});
  return {
    waits, messages, actions, cards, calls, window,
    start:() => vm.runInContext(startSource, context),
    stop:() => vm.runInContext(stopSource, context),
    release:index => waits[index].resolve(value(hold)),
    closed:() => closed, philStops:() => philStops, captureCancels:() => captureCancels
  };
}
function noCardsLeft(f) { assert.ok(f.cards.every(card => !card.attached && card.removed)); }
(async () => {
  let checks=0;
  // Closing while any asynchronous stage is pending retires the request. This
  // includes the card's render completion after it entered the Lovelace tree.
  for (const stage of ['pipeline','nimo','phil','card']) {
    const f=fixture({hold:stage});const pending=f.start();await flush();
    assert.equal(f.waits.length,1);f.stop();f.release(0);await pending;
    assert.equal(f.actions.length,0);assert.deepEqual(f.messages,[]);noCardsLeft(f);
    assert.equal(f.closed(),1);assert.equal(f.philStops(),1);assert.equal(f.captureCancels(),1);
    assert.equal(f.window.edsysNimoBinding.sequence,1);checks++;
  }
  // A rejected old stage is equally retired, without overwriting a newer host
  // status with an obsolete failure or invoking a later optional decorator.
  for (const stage of ['pipeline','nimo','phil','card']) {
    const f=fixture({hold:stage});const pending=f.start();await flush();f.stop();
    f.waits[0].reject(Error('late failure'));await pending;
    assert.equal(f.actions.length,0);assert.deepEqual(f.messages,[]);noCardsLeft(f);checks++;
  }
  // A newer request wins at every await boundary and in either resolution
  // order. Exactly its action and its completion message can reach the host.
  for (const stage of ['pipeline','nimo','phil','card']) {
    for (const order of [[0,1],[1,0]]) {
      const f=fixture({hold:stage});const old=f.start();await flush();const fresh=f.start();await flush();
      assert.equal(f.waits.length,2);
      for (const index of order) { f.release(index);await flush(); }
      await Promise.all([old,fresh]);
      assert.equal(f.actions.length,1);assert.deepEqual(f.messages,['requested']);noCardsLeft(f);checks++;
    }
  }
  // Stop need not wait for even the first Promise continuation to take effect.
  {
    const f=fixture();const pending=f.start();f.stop();await pending;
    assert.equal(f.actions.length,0);assert.deepEqual(f.calls,['pipeline']);assert.deepEqual(f.messages,[]);checks++;
  }
  {
    const f=fixture({captureGuard:false});const pending=f.start();f.stop();await pending;
    assert.equal(f.actions.length,0);assert.equal(f.closed(),1);assert.deepEqual(f.messages,[]);checks++;
  }
  // The ordinary native action is retained with optional decorators absent or
  // unavailable, and an unsuccessful card render never leaks its hidden card.
  for (const optionalFailure of [false,true]) {
    const f=fixture({optionalFailure});await f.start();
    assert.equal(f.actions.length,1);assert.equal(f.actions[0].type,'action');
    assert.equal(f.cards[0].config.tap_action.pipeline_id,'test-id');
    assert.equal(f.cards[0].config.tap_action.start_listening,true);
    assert.deepEqual(f.messages,['requested']);noCardsLeft(f);checks++;
  }
  {
    const f=fixture({noTarget:true});await f.start();assert.equal(f.actions.length,0);
    assert.deepEqual(f.messages,['assist-button-not-ready']);noCardsLeft(f);checks++;
  }
  {
    const f=fixture({missingPipeline:true});await f.start();assert.equal(f.actions.length,0);
    assert.deepEqual(f.messages,['pipeline-missing']);assert.equal(f.cards.length,0);checks++;
  }
  {
    const f=fixture({hold:'pipeline'});const pending=f.start();f.waits[0].reject(Error('current failure'));await pending;
    assert.equal(f.actions.length,0);assert.deepEqual(f.messages,['assist-unavailable']);checks++;
  }
  console.log(JSON.stringify({checks, network_or_devices:false}));
})().catch(error => { console.error(error);process.exit(1); });
