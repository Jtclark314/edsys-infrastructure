// Called only in Nimo's signed-in, same-origin HA WebView. No token is read.
// Narrowly decorate the named pipeline's native subscription with a registered
// client device ID and consume only its typed camera-response card.
window.edsysSetupNimo = async (pipelineId) => {
  const app = document.querySelector('home-assistant');
  if (!app?.hass?.connection) throw new Error('home-not-ready');
  const connection = app.hass.connection;
  const client = await app.hass.callWS({type: 'edsys_voice_knowledge/nimo'});
  if (typeof client.device_id !== 'string' || !client.device_id) throw new Error('client-not-ready');
  const binding = window.edsysNimoBinding;
  if (binding?.connection === connection) {
    binding.pipeline = pipelineId;
    if (!binding.noReplay) {
      const previous = connection.subscribeMessage;
      connection.subscribeMessage = function (callback, message, options) {
        return previous.call(this, callback, message,
          message?.type === 'assist_pipeline/run' && message.pipeline === binding.pipeline ? {...options, resubscribe: false} : options);
      };
      binding.noReplay = true;
    }
    return;
  }
  const state = {connection, pipeline: pipelineId, sequence: 0, seen: new Set(), noReplay: true};
  window.edsysNimoBinding = state;
  const subscribe = connection.subscribeMessage;
  connection.subscribeMessage = function (callback, message, options) {
    if (message?.type !== 'assist_pipeline/run' || message.pipeline !== state.pipeline) {
      return subscribe.call(this, callback, message, options);
    }
    const sequence = ++state.sequence;
    let presentation;
    return subscribe.call(this, (event) => {
      // Native event handling is retained, including STT, speech audio and errors.
      callback(event);
      if (event?.type === 'intent-end') {
        const card = event.data?.intent_output?.response?.card?.edsys_camera;
        if (card?.content) {
          try { presentation = JSON.parse(card.content); } catch { presentation = undefined; }
        }
      }
      if (event?.type === 'run-end' && presentation && sequence === state.sequence) {
        void show(presentation, sequence);
      }
    // A reconnect must never resubmit a household command automatically.
    }, {...message, device_id: client.device_id}, {...options, resubscribe: false});
  };
  function roots() {
    const result = [document];
    for (let i = 0; i < result.length && i < 2000; i++) {
      for (const element of result[i].querySelectorAll('*')) if (element.shadowRoot) result.push(element.shadowRoot);
    }
    return result.slice(0, 2000);
  }
  async function show(card, sequence) {
    if (card.version !== 1 || typeof card.request_id !== 'string' || !/^[0-9A-HJKMNP-TV-Z]{26}$/.test(card.request_id) ||
        typeof card.entity_id !== 'string' || !/^camera\.[a-z0-9_]+$/.test(card.entity_id) ||
        !app.hass.states[card.entity_id] || state.seen.has(card.request_id)) return;
    state.seen.add(card.request_id);
    if (state.seen.size > 64) state.seen.delete(state.seen.values().next().value);
    // Native Assist retains its recorder for another turn. Release capture
    // during viewing without closing the dialog or cutting off reply audio.
    for (const root of roots()) {
      const chat = root.querySelector('ha-assist-chat');
      if (chat?._audioRecorder) {
        chat._audioRecorder.close();
        chat._audioRecorder = undefined;
        chat.requestUpdate?.('_audioRecorder');
      }
    }
    window.chrome.webview.postMessage('camera-opening');
    // HA's native more-info camera dialog owns its authenticated stream,
    // media lifecycle and errors. No external image URL or renderer is used.
    app.dispatchEvent(new CustomEvent('hass-more-info', {
      detail: {entityId: card.entity_id, large: true}, bubbles: true, composed: true
    }));
    let status = 'failed';
    let imageVisible = false;
    const deadline = performance.now() + 15000;
    while (performance.now() < deadline && sequence === state.sequence) {
      const dialog = roots().map(root => root.querySelector('ha-more-info-dialog')).find(Boolean);
      if (dialog && dialog.hass?.moreInfoEntityId === card.entity_id || dialog?._entityId === card.entity_id) {
        const local = [dialog.shadowRoot].filter(Boolean);
        for (let i = 0; i < local.length && i < 250; i++) {
          const video = local[i].querySelector('video');
          if (video && !video.paused && video.readyState >= 2 && video.videoWidth > 0) {
            const before = video.currentTime;
            await new Promise(resolve => setTimeout(resolve, 350));
            if (!video.paused && video.currentTime > before + 0.1) { status = 'playing'; break; }
          }
          imageVisible ||= [...local[i].querySelectorAll('img')].some(img => img.complete && img.naturalWidth > 0);
          for (const element of local[i].querySelectorAll('*')) if (element.shadowRoot) local.push(element.shadowRoot);
        }
      }
      if (status === 'playing') break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (sequence !== state.sequence) return;
    if (status !== 'playing' && imageVisible) status = 'image_visible';
    try {
      await app.hass.callWS({type: 'edsys_voice_knowledge/presentation_ack', request_id: card.request_id, status});
    } catch { status = 'failed'; }
    window.chrome.webview.postMessage(status === 'playing' ? 'camera-playing' : status === 'image_visible' ? 'camera-image-visible' : 'camera-failed');
  }
};
