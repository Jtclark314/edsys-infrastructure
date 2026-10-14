// Offline transport fakes: these checks cannot reach Home Assistant or a TV.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync(__dirname + '/lg-key.js', 'utf8');
const binding = {entity_id: 'media_player.fixture', device_id: 'fixture_device', config_entry_id: 'fixture_entry', area_id: 'fixture_room'};
async function fixture(options = {}) {
  const calls = [], timers = [];
  const location = {pathname: options.path || '/edsys-media/master-bedroom'};
  const window = {};
  const hass = {connection: {socket: {readyState: options.offline ? 3 : 1}}, states: {[binding.entity_id]: {state: options.state || 'on'}},
    callWS: async message => {
      if (options.read) await options.read({location, window, timers});
      if (message.type === 'config/device_registry/list') return [{id: binding.device_id, area_id: binding.area_id}];
      return {...binding, platform: 'webostv', disabled_by: null, ...(options.entity || {})};
    },
    callService: async (...args) => { calls.push(args); if (options.error) throw Error('fixture transport failure'); }};
  const app = {hass};
  const document = {querySelector: () => app, querySelectorAll: selector => selector === '*' ? [] : options.dialog ? [{getClientRects: () => [1]}] : []};
  const context = {window, document, location, getComputedStyle: () => ({visibility: 'visible'}),
    setTimeout: cb => { timers.push(cb); return timers.length; }, clearTimeout: () => {}};
  vm.createContext(context);
  const result = await vm.runInContext(source.replace('__LG_BINDING__', JSON.stringify(binding)).replace('__LG_BUTTON__', JSON.stringify(options.button || 'LEFT')), context);
  return {result, calls};
}
(async () => {
  let checked = 0;
  const good = await fixture(); assert.equal(good.result, 'remote-sent'); assert.equal(good.calls.length, 1);
  assert.equal(good.calls[0][0], 'webostv'); assert.equal(good.calls[0][1], 'button');
  assert.equal(good.calls[0][2].entity_id, binding.entity_id); checked++;
  for (const [options, expected] of [
    [{offline: true}, 'remote-offline'], [{path: '/edsys-home/home'}, 'remote-not-ready'], [{button: 'POWER'}, 'remote-not-ready'],
    [{state: 'off'}, 'remote-tv-off'], [{entity: {device_id: 'changed'}}, 'remote-mapping-changed'],
    [{entity: {area_id: 'changed'}}, 'remote-mapping-changed'], [{entity: {disabled_by: 'user'}}, 'remote-mapping-changed'],
    [{dialog: true}, 'remote-dialog-open'],
    [{read: async ({location}) => { location.pathname = '/edsys-home/home'; }}, 'remote-not-ready'],
    [{read: async ({window}) => { window.edsysLGRemoteEpoch = 1; }}, 'remote-not-ready'],
    [{read: async ({timers}) => { await Promise.resolve(); timers[0](); }}, 'remote-not-ready']]) {
    const r = await fixture(options); assert.equal(r.result, expected); assert.equal(r.calls.length, 0); checked++;
  }
  const inherited = await fixture({entity: {area_id: null}}); assert.equal(inherited.result, 'remote-sent'); checked++;
  const failed = await fixture({error: true}); assert.equal(failed.result, 'remote-uncertain'); assert.equal(failed.calls.length, 1); checked++;
  console.log(JSON.stringify({keyboard_guard_checks: checked, passed: true}));
})().catch(error => { console.error(error); process.exitCode = 1; });
