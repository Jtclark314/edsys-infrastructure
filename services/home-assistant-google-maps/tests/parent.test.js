import test from "node:test";
import assert from "node:assert/strict";

function node(){const value=new EventTarget();value.replaceChildren=()=>{};value.setAttribute=(name,text)=>{value[name]=text;};value.setCustomValidity=text=>{value.validityMessage=text;};value.reportValidity=()=>!value.validityMessage;return value;}
globalThis.HTMLElement=class extends EventTarget {attachShadow(){
  const elements=new Map();let html="";
  this.shadowRoot={get innerHTML(){return html;},set innerHTML(value){html=value;elements.clear();},querySelector(selector){if(!elements.has(selector))elements.set(selector,node());return elements.get(selector);},append(){}};
}};
globalThis.HTMLIFrameElement=class {};
HTMLIFrameElement.prototype.credentialless=false;
const constructors=new Map();
globalThis.customElements={get:name=>constructors.get(name),define:(name,value)=>constructors.set(name,value)};
globalThis.window={location:{origin:"http://ha.example.test"},customCards:[]};
await import("../edsys-google-maps-trial.js");
const Card=constructors.get("edsys-google-maps-trial");
const Editor=constructors.get("edsys-google-map-editor");

function setup(cryptoSource) {
  Object.defineProperty(globalThis,"crypto",{configurable:true,value:cryptoSource});
  let reads=0,writes=0,frames=0;
  globalThis.localStorage={getItem(){reads++;return null;},setItem(){writes++;}};
  globalThis.document={createElement(tag){if(tag==="iframe")frames++;return node();}};
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

test("saved Hybrid/fallback config survives rendering, a new instance and native editor key roundtrip",()=>{
  const {card,counts}=setup({getRandomValues(bytes){bytes.fill(17);return bytes;}});
  const saved={type:"custom:edsys-google-maps-trial",renderer_url:"https://renderer.example.test/v1/renderer.html",entities:["person.synthetic"],default_map_type:"hybrid",fallback_path:"/map-fallback"};
  card.setConfig(saved);
  assert.equal(card.shadowRoot.querySelector("select").value,"hybrid");
  assert.equal(card.shadowRoot.querySelector("a").href,"/map-fallback");
  assert.equal(card.shadowRoot.querySelector("a").textContent,"HA Map (Fallback)");
  const replacement=new Card();replacement.setConfig(JSON.parse(JSON.stringify(card._config)));
  assert.equal(replacement.shadowRoot.querySelector("select").value,"hybrid");
  const editor=new Editor();editor.setConfig(replacement._config);
  let roundtrip;editor.addEventListener("config-changed",event=>{roundtrip=event.detail.config;});
  const input=editor.shadowRoot.querySelector("input");input.value="AI"+"za"+"A".repeat(35);input.dispatchEvent(new Event("change"));
  assert.equal(roundtrip.default_map_type,"hybrid");assert.equal(roundtrip.fallback_path,"/map-fallback");
  replacement.setConfig(roundtrip);
  assert.equal(replacement.shadowRoot.querySelector("select").value,"hybrid");
  assert.equal(replacement.shadowRoot.querySelector("a").href,"/map-fallback");
  assert.deepEqual(counts(),{reads:0,writes:0,frames:0});
});

test("legacy config still renders Road and its original native map link without loading",()=>{
  const {card,counts}=setup({getRandomValues(bytes){bytes.fill(17);return bytes;}});
  card.setConfig({type:"custom:edsys-google-maps-trial",entities:["person.synthetic"]});
  assert.equal(card.shadowRoot.querySelector("select").value,"roadmap");
  assert.equal(card.shadowRoot.querySelector("a").href,"/map");
  assert.equal(card.shadowRoot.querySelector("a").textContent,"Existing map");
  assert.deepEqual(counts(),{reads:0,writes:0,frames:0});
});
