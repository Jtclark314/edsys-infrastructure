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
          ['assist_pipeline/run','edsys_voice_knowledge/phil_pipeline'].includes(message?.type) && message.pipeline === binding.pipeline ? {...options, resubscribe: false} : options);
      };
      binding.noReplay = true;
    }
    return;
  }
  const state = {connection, pipeline: pipelineId, sequence: 0, seen: new Set(), noReplay: true};
  window.edsysNimoBinding = state;
  const subscribe = connection.subscribeMessage;
  connection.subscribeMessage = function (callback, message, options) {
    if (!['assist_pipeline/run','edsys_voice_knowledge/phil_pipeline'].includes(message?.type) || message.pipeline !== state.pipeline) {
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

// Native HA owns capture, intent execution and TTS. An eligible HDMI turn uses
// a server-bound native pipeline subscription and an Audio-compatible completion
// proxy; only the Pi's actual playback can complete that native frontend turn.
window.edsysSetupPhil = async pipelineId => {
  const app = document.querySelector('home-assistant');
  const connection = app?.hass?.connection;
  if (!connection) return;
  const old = window.edsysPhilBinding;
  if (old?.connection === connection) { old.pipeline = pipelineId; return; }
  old?.stop();
  const state = {connection, pipeline: pipelineId, current: null};
  window.edsysPhilBinding = state;
  function clear(run) { clearInterval(run.timer);clearInterval(run.poller); }
  function stop() {
    const run = state.current;
    if (!run) return;
    state.current = null;clear(run);
    if (run.closed) return;
    run.closed = true;
    run.fallback?.pause();
    const sequence = ++run.sequence;
    // Never replay a command or retry an uncertain speech commit.
    run.queue = run.queue.catch(() => {}).then(() => app.hass.callWS({
      type: 'edsys_voice_knowledge/phil_update', session_id: run.session_id,
      sequence, phase: 'cancelled', audio_ref: null
    })).catch(() => {});
  }
  state.stop = stop;
  window.edsysStopPhil = stop;
  connection.addEventListener?.('disconnected', stop);
  function audio() {
    const roots = [document];
    for (let i = 0; i < roots.length && i < 2000; i++) {
      const chat = roots[i].querySelector('ha-assist-chat');
      if (chat?.pipeline?.id === state.pipeline && chat._audio) return chat._audio;
      for (const element of roots[i].querySelectorAll('*')) if (element.shadowRoot) roots.push(element.shadowRoot);
    }
    return null;
  }
  function current(run) { return state.current === run && !run.closed; }
  function enqueue(run, phase, ref = null) {
    const sequence = ++run.sequence;
    run.queue = run.queue.then(async () => {
      if (!current(run)) return;
      const result = await Promise.race([
        app.hass.callWS({type: 'edsys_voice_knowledge/phil_update',
          session_id: run.session_id, sequence, phase, audio_ref: ref}),
        new Promise((_, reject) => setTimeout(() => reject(Error('display-timeout')), 3000))
      ]);
      // Pi completion/outcome is read separately. Do not lose its safe fallback
      // outcome to a simultaneous final heartbeat.
      if (!result?.active && run.output !== 'pi') { run.closed = true;clear(run); }
    }).catch(() => { if (run.output === 'pi') fail(run); else { run.closed = true;clear(run); } });
    return run.queue;
  }
  function complete(run, event) {
    if (!current(run)) return;
    clear(run);run.closed = true;run.terminal = true;state.current = null;
    if (run.proxy) { run.proxy.ended = event === 'ended';run.proxy.paused = true;run.proxy.dispatchEvent(new Event(event)); }
  }
  function fail(run) {
    if (!current(run)) return;
    const proxy=run.proxy;stop();
    if (proxy) proxy.dispatchEvent(new Event('error'));
  }
  function attach(run) {
    const nativeAudio = audio();
    if (!nativeAudio?.addEventListener) return;
    run.audio = nativeAudio;
    const played = () => { if (current(run)) { run.audioStarted = true;enqueue(run, 'audio_start', run.audio_ref); } };
    const ended = () => {
      if (!current(run) || run.terminal) return;
      run.terminal = true;clear(run);enqueue(run, 'audio_end', run.audio_ref);
    };
    nativeAudio.addEventListener('playing', played);
    nativeAudio.addEventListener('waiting', () => { if (current(run) && run.audioStarted) enqueue(run, 'audio_waiting', run.audio_ref); });
    nativeAudio.addEventListener('ended', ended);
    nativeAudio.addEventListener('pause', () => { if (current(run)) { if (nativeAudio.ended) ended(); else stop(); } });
    nativeAudio.addEventListener('error', () => { if (current(run)) { clear(run);run.terminal = true;enqueue(run, 'failed'); } });
    if (!nativeAudio.paused && nativeAudio.readyState >= 2 && nativeAudio.currentTime > 0) played();
  }
  function remoteAudio(run, NativeAudio, url) {
    const proxy = new EventTarget();
    Object.assign(proxy, {paused: true, ended: false, readyState: 0, currentTime: 0,
      play: () => Promise.resolve(),
      pause: () => { if (current(run)) stop(); },
      removeAttribute: name => { if (name === 'src') run.nativeUrl = null; }
    });
    run.proxy = proxy;run.nativeUrl = url;
    async function poll() {
      if (!current(run) || run.polling || run.fallback) return;
      run.polling = true;
      try {
        const status = await Promise.race([
          app.hass.callWS({type: 'edsys_voice_knowledge/phil_status', session_id: run.session_id}),
          new Promise((_, reject) => setTimeout(() => reject(Error('reply-timeout')), 3000))
        ]);
        if (!current(run)) return;
        if (status.started && !run.audioStarted) {
          run.audioStarted = true;proxy.paused = false;proxy.readyState = 2;proxy.dispatchEvent(new Event('playing'));
        }
        if (status.audio_state === 'finished' && !status.active) complete(run, 'ended');
        else if (status.audio_state === 'failed' && status.fallback_allowed === true && !status.started && run.nativeUrl) {
          // Same generated TTS only, and only with an explicit pre-commit HA
          // outcome. No pipeline/household command is submitted a second time.
          clear(run);run.fallback = new NativeAudio(run.nativeUrl);
          run.fallback.addEventListener('playing', () => { if (current(run)) proxy.dispatchEvent(new Event('playing')); });
          run.fallback.addEventListener('ended', () => complete(run, 'ended'));
          run.fallback.addEventListener('error', () => fail(run));
          run.fallback.play().catch(() => fail(run));
        } else if (!status.active) fail(run);
      } catch { fail(run); }
      finally { run.polling = false; }
    }
    run.poller = setInterval(() => void poll(), 350);
    return proxy;
  }
  const subscribe = connection.subscribeMessage;
  connection.subscribeMessage = async function(callback, message, options) {
    if (message?.type !== 'assist_pipeline/run' || message.pipeline !== state.pipeline || message.start_stage !== 'stt') {
      return subscribe.call(this, callback, message, options);
    }
    stop();
    let lease;
    try {
      lease = await Promise.race([
        app.hass.callWS({type: 'edsys_voice_knowledge/phil_begin', pipeline_id: state.pipeline}),
        new Promise(resolve => setTimeout(() => resolve(null), 2000))
      ]);
    } catch { }
    const run = lease?.active ? {...lease, sequence: -1, queue: Promise.resolve(), closed: false, terminal: false} : null;
    if (run) {
      state.current = run;
      run.timer = setInterval(() => { if (!run.terminal) enqueue(run, 'heartbeat'); }, 2000);
    }
    const wire = run?.output === 'pi' ? {...message, type: 'edsys_voice_knowledge/phil_pipeline', phil_session_id: run.session_id} : message;
    try {
      return await subscribe.call(this, event => {
        if (run?.output === 'pi' && current(run) && event?.type === 'run-start') {
          // HA currently constructs its Audio synchronously in run-start. Scope
          // the constructor substitution to that callback and restore it even
          // on errors; do not mute unrelated browser media.
          const NativeAudio = window.Audio;
          try {
            window.Audio = function(url) { return remoteAudio(run, NativeAudio, url); };
            callback(event);
          } finally { window.Audio = NativeAudio; }
          if (!run.proxy) fail(run);
        } else callback(event);
        if (!run || !current(run) || run.terminal) return;
        if (event?.type === 'run-start' && run.output !== 'pi') attach(run);
        else if (event?.type === 'stt-start') enqueue(run, 'listening');
        else if (['stt-vad-end','stt-end','intent-start'].includes(event?.type)) enqueue(run, 'processing');
        else if (event?.type === 'error') {
          if (run.output === 'pi') fail(run);
          else { clear(run);run.terminal = true;enqueue(run, 'failed'); }
        }
        // Generation run-end never completes reply playback/follow-up listening.
      }, wire, {...options, resubscribe: false});
    } catch (error) { stop();throw error; }
  };
};
