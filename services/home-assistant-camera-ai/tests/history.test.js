import test from 'node:test';
import assert from 'node:assert/strict';
const elements=new Map();const created=[];
globalThis.HTMLElement=class {replaceChildren(...children){this.children=children;}};
globalThis.customElements={get:name=>elements.get(name),define:(name,value)=>elements.set(name,value),whenDefined:async()=>{}};
globalThis.window={loadCardHelpers:async()=>({createCardElement:config=>{const card={config};created.push(card);return card;}})};
await import('../edsys-camera-ai-card.js');
const History=elements.get('edsys-camera-ai-history');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const hass=start=>({states:{'calendar.llm_vision_timeline':{attributes:{starts:start,title:'Outdoor · Camera AI',description:'Answer'}}}});

test('saved timeline update refreshes stock card without waiting for its fetch cache',async()=>{
  const card=new History();card.setConfig({type:'custom:edsys-camera-ai-history',header:'Recent analyses'});
  const initial=created.length;card.hass=hass(null);await settle();
  assert.equal(created.length,initial+1);
  card.hass=hass(null);await settle();assert.equal(created.length,initial+1);
  card.hass=hass('2026-10-07T21:00:00Z');await settle();
  assert.equal(created.length,initial+2);
  assert.equal(card.children[0].config.type,'custom:llmvision-card');
});

test('rapid updates cannot replace current history with an older pending card',async()=>{
  const card=new History();card.setConfig({type:'custom:edsys-camera-ai-history'});
  const initial=created.length;card.hass=hass('old');card.hass=hass('new');await settle();
  assert.equal(created.length,initial+1);
  assert.equal(card.children[0].hass.states['calendar.llm_vision_timeline'].attributes.starts,'new');
});
