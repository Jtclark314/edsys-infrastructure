import test from "node:test";
import assert from "node:assert/strict";

const elements=new Map();
globalThis.HTMLElement=class {attachShadow(){this.shadowRoot={querySelector:selector=>elements.get(selector)};}};
globalThis.HTMLIFrameElement=class {};
HTMLIFrameElement.prototype.credentialless=false;
const constructors=new Map();
globalThis.customElements={get:name=>constructors.get(name),define:(name,value)=>constructors.set(name,value)};
globalThis.window={location:{origin:"http://ha.example.test"},customCards:[]};
await import("../edsys-google-maps-trial.js");
const Card=constructors.get("edsys-google-maps-trial");

function setup(cryptoSource) {
  Object.defineProperty(globalThis,"crypto",{configurable:true,value:cryptoSource});
  let reads=0,writes=0,frames=0;
  globalThis.localStorage={getItem(){reads++;return null;},setItem(){writes++;}};
  globalThis.document={createElement(tag){assert.equal(tag,"iframe");frames++;return {setAttribute(name,value){this[name]=value;}};}};
  for(const selector of [".load",".stop",".stage"])elements.set(selector,{replaceChildren(){}});
  const card=new Card(),messages=[];card.status=message=>messages.push(message);
  card._config={api_key:"synthetic-browser-key",renderer_url:"https://renderer.example.test/v1/renderer.html",entities:[{entity:"person.synthetic"}]};
  card._hass={connected:true,states:{"person.synthetic":{state:"home",attributes:{latitude:1,longitude:2}}}};
  return {card,messages,counts:()=>({reads,writes,frames})};
}

test("HTTP-style crypto with no randomUUID starts one isolated frame and reserves one load",()=>{
  const {card,counts}=setup({getRandomValues(bytes){bytes.fill(17);return bytes;}});
  try {
    card.start();
    assert.deepEqual(counts(),{reads:1,writes:1,frames:1});
    assert.equal(card._nonce,"11".repeat(16));
    assert.equal(new URL(card._frame.src).hash,"#"+card._nonce);
    assert.equal(card._frame.credentialless,true);
    assert.equal(card._frame.sandbox,"allow-scripts allow-same-origin allow-popups");
  } finally {clearTimeout(card._timeout);}
});

test("missing or throwing CSPRNG leaves load reservation and iframe creation untouched",()=>{
  for(const cryptoSource of [undefined,{}, {getRandomValues(){throw Error("private failure detail");}}]) {
    const {card,messages,counts}=setup(cryptoSource);
    card.start();
    assert.deepEqual(counts(),{reads:0,writes:0,frames:0});
    assert.equal(card._frame,undefined);
    assert.match(messages.at(-1),/Secure random generation is unavailable.*existing map/);
    assert.ok(!messages.at(-1).includes("private failure detail"));
  }
});
