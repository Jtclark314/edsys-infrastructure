(async () => {
  const app = document.querySelector('home-assistant');
  if (!app?.hass || !customElements.get('hui-button-card')) return 'sign-in-or-home-required';
  const pipelines = await app.hass.callWS({ type: 'assist_pipeline/pipeline/list' });
  const pipeline = pipelines.pipelines.find(p => p.name === __PIPELINE_NAME__);
  if (!pipeline) return 'pipeline-missing';
  // The ordinary native assistant stays usable if the optional pilot is absent.
  try { await window.edsysSetupNimo?.(pipeline.id); } catch { }
  // Use HA's native documented Assist button action and its own authenticated
  // frontend. No token is read/exported and no direct device action is sent.
  const roots = [document];
  let existing;
  for (let i = 0; i < roots.length && i < 2000; i++) {
    existing = roots[i].querySelector('hui-button-card, hui-tile-card, hui-shortcut-card');
    if (existing) break;
    for (const element of roots[i].querySelectorAll('*')) {
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
  }
  if (!existing) return 'assist-button-not-ready';
  const card = document.createElement('hui-button-card');
  card.style.display = 'none';
  // Keep the event inside the native Lovelace tree, whose action handler owns
  // the Assist dialog. The temporary element never changes dashboard storage.
  existing.parentNode.appendChild(card);
  card.hass = app.hass;
  card.setConfig({ type: 'button', name: 'Talk to Home Assistant',
    tap_action: { action: 'assist', pipeline_id: pipeline.id, start_listening: true } });
  await card.updateComplete;
  const target = card.shadowRoot?.querySelector('ha-card');
  if (!target) return 'assist-button-not-ready';
  target.dispatchEvent(new CustomEvent('action', { detail: { action: 'tap' }, bubbles: true, composed: true }));
  card.remove();
  return 'requested';
})().then(result => window.chrome.webview.postMessage(result))
  .catch(() => window.chrome.webview.postMessage('assist-unavailable'));
