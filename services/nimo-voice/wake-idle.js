// Conservative read-only preflight, qualified against the existing HA frontend.
// An open conversation, recorder, reply or camera dialog keeps wake capture off.
// Unknown dialog shapes fail closed. Closing the conversation/window rearms it.
(() => {
  if (!window.edsysWakeCapture?.idle()) return false;
  const app = document.querySelector('home-assistant');
  if (!app?.hass?.connection?.connected || location.pathname !== '/edsys-home/home') return false;
  const roots = [document];
  for (let i = 0; i < roots.length; i++) {
    if (i >= 2000) return false;
    const root = roots[i];
    for (const dialog of root.querySelectorAll('ha-voice-command-dialog')) {
      if (dialog._open !== false || dialog._dialogOpen !== false) return false;
    }
    for (const dialog of root.querySelectorAll('ha-more-info-dialog')) {
      // A retained, closed dialog has a defined false open flag; unknown is busy.
      if (dialog._open !== false) return false;
    }
    for (const chat of root.querySelectorAll('ha-assist-chat')) {
      if (chat._audioRecorder || (chat._audio && !chat._audio.ended)) return false;
    }
    for (const element of root.querySelectorAll('*')) if (element.shadowRoot) roots.push(element.shadowRoot);
  }
  return true;
})()
