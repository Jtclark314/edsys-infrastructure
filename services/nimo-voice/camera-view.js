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
  if (old?.connection === connection) {
    if (old.pipeline !== pipelineId) old.stop();
    old.pipeline = pipelineId;return;
  }
  old?.stop();
  const state = {connection, pipeline: pipelineId, current: null, pending: null, generation: 0};
  window.edsysPhilBinding = state;
  let watching = false;
  function watch(active) {
    if (watching === active) return;
    watching = active;
    const method = active ? 'addEventListener' : 'removeEventListener';
    connection[method]?.('disconnected', stop);
    window[method]?.('pagehide', stop);
    window[method]?.('beforeunload', stop);
  }
  function idle() { if (!state.current && !state.pending) watch(false); }
  function intervals(run) { clearInterval(run.timer);clearInterval(run.poller); }
  function unlisten(run) { for (const remove of run.listeners.splice(0)) remove(); }
  function clear(run) {
    intervals(run);unlisten(run);run.retire();
    for (const timer of run.timeouts) clearTimeout(timer);
    run.timeouts.clear();
  }
  function listen(run, target, event, callback) {
    target.addEventListener(event, callback);
    run.listeners.push(() => target.removeEventListener?.(event, callback));
  }
  async function bounded(run, promise, ms, reason) {
    let timer;
    try {
      return await Promise.race([promise,
        new Promise((_, reject) => { timer = setTimeout(() => reject(Error(reason)), ms);run.timeouts.add(timer); }),
        run.retired.then(() => { throw Error('voice-retired'); })]);
    } finally { clearTimeout(timer);run.timeouts.delete(timer); }
  }
  function close(run) {
    run.closed = true;clear(run);
    if (state.current === run) state.current = null;
    idle();
  }
  function stop() {
    state.generation++;
    const pending = state.pending;
    state.pending = null;pending?.cancel();
    const run = state.current;
    if (!run) { idle();return; }
    close(run);
    run.fallback?.pause();
    if (run.proxy) {
      run.proxy.paused = true;
      run.proxy.dispatchEvent(new Event('error'));
    }
    const sequence = ++run.sequence;
    // Never replay a command or retry an uncertain speech commit.
    run.queue = run.queue.catch(() => {}).then(() => app.hass.callWS({
      type: 'edsys_voice_knowledge/phil_update', session_id: run.session_id,
      sequence, phase: 'cancelled', audio_ref: null
    })).catch(() => {});
  }
  state.stop = stop;
  window.edsysStopPhil = stop;
  async function begin(generation) {
    let cancel, timer;
    const attempt = {done: false, accepted: false, cancelled: new Set()};
    const cancelled = new Promise(resolve => { cancel = resolve; });
    attempt.cancel = () => { clearTimeout(timer);cancel(); };
    state.pending = attempt;watch(true);
    // A late response can retire only its own never-bound lease. The server's
    // session check prevents it from cancelling a newer turn.
    function discard(lease) {
      if (!lease?.active || !lease.session_id || attempt.cancelled.has(lease.session_id)) return;
      attempt.cancelled.add(lease.session_id);
      void app.hass.callWS({type: 'edsys_voice_knowledge/phil_update', session_id: lease.session_id,
        sequence: 0, phase: 'cancelled', audio_ref: null}).catch(() => {});
    }
    const request = Promise.resolve().then(() => live(generation) ? app.hass.callWS({
      type: 'edsys_voice_knowledge/phil_begin', pipeline_id: state.pipeline
    }) : null);
    request.then(lease => { if (attempt.done && !attempt.accepted) discard(lease); }, () => {});
    try {
      const result = await Promise.race([
        request.then(lease => ({lease}), () => ({})),
        new Promise(resolve => { timer = setTimeout(() => resolve({}), 2000); }),
        cancelled.then(() => ({}))]);
      attempt.done = true;
      if (!live(generation)) { discard(result.lease);return null; }
      // A timeout uses native Nimo speech once. Its eventual lease must still
      // be discarded, rather than opening an orphan Pi display after fallback.
      return {lease: result.lease, discard: () => discard(result.lease),
        accept: () => { if (result.lease) attempt.accepted = true; }};
    } finally {
      clearTimeout(timer);
      if (state.pending === attempt) state.pending = null;
    }
  }
  function live(generation) { return generation === state.generation && window.edsysPhilBinding === state; }
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
      const result = await bounded(run, app.hass.callWS({type: 'edsys_voice_knowledge/phil_update',
        session_id: run.session_id, sequence, phase, audio_ref: ref}), 3000, 'display-timeout');
      // Pi completion/outcome is read separately. Do not lose its safe fallback
      // outcome to a simultaneous final heartbeat.
      if (!result?.active && run.output !== 'pi') close(run);
    }).catch(() => { if (run.output === 'pi') fail(run); else close(run); });
    return run.queue;
  }
  function complete(run, event) {
    if (!current(run)) return;
    run.terminal = true;close(run);
    if (run.proxy) { run.proxy.ended = event === 'ended';run.proxy.paused = true;run.proxy.dispatchEvent(new Event(event)); }
  }
  function fail(run) {
    if (!current(run)) return;
    stop();
  }
  function attach(run) {
    const nativeAudio = audio();
    if (!nativeAudio?.addEventListener) return;
    run.audio = nativeAudio;
    const played = () => { if (current(run)) { run.audioStarted = true;enqueue(run, 'audio_start', run.audio_ref); } };
    const ended = () => {
      if (!current(run) || run.terminal) return;
      run.terminal = true;intervals(run);unlisten(run);enqueue(run, 'audio_end', run.audio_ref);
    };
    listen(run, nativeAudio, 'playing', played);
    listen(run, nativeAudio, 'waiting', () => { if (current(run) && run.audioStarted) enqueue(run, 'audio_waiting', run.audio_ref); });
    listen(run, nativeAudio, 'ended', ended);
    listen(run, nativeAudio, 'pause', () => { if (current(run)) { if (nativeAudio.ended) ended(); else stop(); } });
    listen(run, nativeAudio, 'error', () => { if (current(run)) { intervals(run);unlisten(run);run.terminal = true;enqueue(run, 'failed'); } });
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
        const status = await bounded(run, app.hass.callWS({type: 'edsys_voice_knowledge/phil_status',
          session_id: run.session_id}), 3000, 'reply-timeout');
        if (!current(run)) return;
        if (status.started && !run.audioStarted) {
          run.audioStarted = true;proxy.paused = false;proxy.readyState = 2;proxy.dispatchEvent(new Event('playing'));
        }
        if (status.audio_state === 'finished' && !status.active) complete(run, 'ended');
        else if (status.audio_state === 'failed' && status.fallback_allowed === true && !status.started && run.nativeUrl) {
          // Same generated TTS only, and only with an explicit pre-commit HA
          // outcome. No pipeline/household command is submitted a second time.
          intervals(run);run.fallback = new NativeAudio(run.nativeUrl);
          listen(run, run.fallback, 'playing', () => { if (current(run)) proxy.dispatchEvent(new Event('playing')); });
          listen(run, run.fallback, 'ended', () => complete(run, 'ended'));
          listen(run, run.fallback, 'error', () => fail(run));
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
    if (window.edsysPhilBinding !== state) return () => {};
    stop();
    const generation = state.generation;
    const attempt = await begin(generation);
    // Superseded/cancelled starts never submit a native pipeline or household
    // command. The latest start alone owns the eventual subscription.
    if (!live(generation)) { attempt?.discard();idle();return () => {}; }
    const lease = attempt?.lease;
    attempt?.accept();
    const run = lease?.active ? {...lease, sequence: -1, queue: Promise.resolve(), closed: false,
      terminal: false, listeners: [], timeouts: new Set()} : null;
    if (run) {
      run.retired = new Promise(resolve => { run.retire = resolve; });
      state.current = run;
      run.timer = setInterval(() => { if (!run.terminal) enqueue(run, 'heartbeat'); }, 2000);
    } else idle();
    const wire = run?.output === 'pi' ? {...message, type: 'edsys_voice_knowledge/phil_pipeline', phil_session_id: run.session_id} : message;
    try {
      const unsubscribe = await subscribe.call(this, event => {
        // Losing the optional Phil display must not suppress the current
        // native Nimo capture/intent/TTS lifecycle. Pi speech owns its proxy
        // completion and therefore still fails closed when its lease retires.
        if (!live(generation) || run?.output === 'pi' && !current(run)) return;
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
          else { intervals(run);unlisten(run);run.terminal = true;enqueue(run, 'failed'); }
        }
        // Generation run-end never completes reply playback/follow-up listening.
      }, wire, {...options, resubscribe: false});
      if (!live(generation)) {
        try { void Promise.resolve(unsubscribe?.()).catch(() => {}); } catch { }
        return () => {};
      }
      return unsubscribe;
    } catch (error) { if (live(generation)) stop();throw error; }
  };
};
