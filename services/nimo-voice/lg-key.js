// Executed only by the focused native keyboard button in the LG remote window.
(async () => {
  const binding = __LG_BINDING__;
  const button = __LG_BUTTON__;
  const epoch = window.edsysLGRemoteEpoch || 0;
  const allowed = new Set(['UP', 'DOWN', 'LEFT', 'RIGHT', 'ENTER', 'BACK', 'HOME', ...'0123456789']);
  if (!allowed.has(button) || location.pathname.replace(/\/$/, '') !== '/edsys-media/master-bedroom') return 'remote-not-ready';
  const app = document.querySelector('home-assistant');
  const hass = app?.hass;
  if (!hass || hass.connection?.socket?.readyState !== 1) return 'remote-offline';
  const roots = [document];
  for (let i = 0; i < roots.length && i < 2000; i++) {
    for (const element of roots[i].querySelectorAll('ha-dialog, ha-more-info-dialog, ha-voice-command-dialog, dialog[open]')) {
      if (element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden') return 'remote-dialog-open';
    }
    for (const element of roots[i].querySelectorAll('*')) if (element.shadowRoot) roots.push(element.shadowRoot);
  }
  let sent = false;
  let expired = false;
  let timer;
  try {
    const result = await Promise.race([(async () => {
      const entity = await hass.callWS({type: 'config/entity_registry/get', entity_id: binding.entity_id});
      if (entity.disabled_by || entity.platform !== 'webostv' || entity.device_id !== binding.device_id || entity.config_entry_id !== binding.config_entry_id) return 'remote-mapping-changed';
      let area = entity.area_id;
      if (!area) {
        const devices = await hass.callWS({type: 'config/device_registry/list'});
        area = devices.find(device => device.id === binding.device_id)?.area_id;
      }
      if (area !== binding.area_id) return 'remote-mapping-changed';
      const state = hass.states[binding.entity_id];
      if (!state || ['off', 'unknown', 'unavailable'].includes(state.state)) return 'remote-tv-off';
      if (expired || (window.edsysLGRemoteEpoch || 0) !== epoch || location.pathname.replace(/\/$/, '') !== '/edsys-media/master-bedroom' || app.hass.connection !== hass.connection) return 'remote-not-ready';
      if (hass.connection?.socket?.readyState !== 1) return 'remote-offline';
      sent = true;
      await hass.callService('webostv', 'button', {entity_id: binding.entity_id, button});
      return 'remote-sent';
    })(), new Promise(resolve => { timer = setTimeout(() => { expired = true; resolve(sent ? 'remote-uncertain' : 'remote-not-ready'); }, 10000); })]);
    return result;
  } catch { return sent ? 'remote-uncertain' : 'remote-not-ready'; }
  finally { clearTimeout(timer); }
})()
