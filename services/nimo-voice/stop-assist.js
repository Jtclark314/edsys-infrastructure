(() => {
  // Retire pending pipeline/setup/card awaits before touching any dialog.
  window.edsysAssistRequestEpoch = (window.edsysAssistRequestEpoch || 0) + 1;
  window.edsysWakeCapture?.cancel();
  window.edsysStopPhil?.();
  if (window.edsysNimoBinding) window.edsysNimoBinding.sequence++;
  const roots = [document];
  for (let i = 0; i < roots.length && i < 2000; i++) {
    const dialog = roots[i].querySelector('ha-voice-command-dialog');
    if (dialog) dialog.closeDialog();
    const camera = roots[i].querySelector('ha-more-info-dialog');
    if (camera?.closeDialog) camera.closeDialog();
    for (const element of roots[i].querySelectorAll('*')) {
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
  }
})()
