// Install before HA loads. When wake mode is off the original API is unchanged.
// In wake mode no native audio capture starts until the host acknowledges that
// its child detector released capture. No audio samples cross this bridge.
(() => {
  if (window.edsysWakeCapture) return;
  const media = navigator.mediaDevices;
  const native = media?.getUserMedia;
  if (typeof native !== 'function' || !window.chrome?.webview) return;
  let enabled = false, retired = false, epoch = 0, serial = 0;
  const requests = new Map(), streams = new Set(), openings = new Set();
  const prefix = crypto.randomUUID().replaceAll('-', '');
  const aborted = () => new DOMException('Capture request cancelled', 'AbortError');
  function cancel() {
    epoch++;retired=true;
    for (const pending of requests.values()) { clearTimeout(pending.timer); pending.reject(aborted()); }
    requests.clear();
    for (const stream of streams) for (const track of stream.getTracks()) track.stop();
    streams.clear();
  }
  window.chrome.webview.addEventListener('message', event => {
    const data = event.data;
    if (data?.type !== 'wake-capture-release' || typeof data.id !== 'string') return;
    const pending = requests.get(data.id);
    if (!pending) return;
    requests.delete(data.id);clearTimeout(pending.timer);
    if (data.allow !== true || pending.epoch !== epoch) { pending.reject(aborted());return; }
    let capture;
    openings.add(pending);
    try { capture = native.call(media, pending.constraints); }
    catch (error) { openings.delete(pending);pending.reject(error);return; }
    Promise.resolve(capture).then(stream => {
      openings.delete(pending);
      if (pending.epoch !== epoch) {
        for (const track of stream.getTracks()) track.stop();
        pending.reject(aborted());return;
      }
      streams.add(stream);pending.resolve(stream);
    }, error => {openings.delete(pending);pending.reject(error);});
  });
  const guarded = function (constraints) {
    if (!constraints?.audio) return native.call(this, constraints);
    if (retired) return Promise.reject(aborted());
    if (!enabled) return native.call(this, constraints);
    const id = prefix + '_' + (++serial);
    return new Promise((resolve, reject) => {
      const pending = {resolve, reject, constraints, epoch};
      pending.timer = setTimeout(() => { requests.delete(id);reject(aborted()); }, 5000);
      requests.set(id, pending);
      window.chrome.webview.postMessage('wake-capture:' + id);
    });
  };
  try { media.getUserMedia = guarded; } catch { return; }
  if (media.getUserMedia !== guarded) return;
  window.edsysWakeCapture = {
    enable(value) { enabled = value === true;return true; },
    cancel,
    begin() { cancel();retired=false; },
    idle() {
      for (const stream of streams) if (!stream.getTracks().some(track => track.readyState === 'live')) streams.delete(stream);
      return media.getUserMedia === guarded && enabled && requests.size === 0 && openings.size === 0 && streams.size === 0;
    }
  };
  document.addEventListener('dialog-closed', event => {
    if (event.detail?.dialog !== 'ha-voice-command-dialog') return;
    cancel();window.chrome.webview.postMessage('wake-dialog-closed');
  });
  window.addEventListener('pagehide', cancel);
})()
