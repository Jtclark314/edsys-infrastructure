(() => {
  const roots = [document];
  for (let i = 0; i < roots.length && i < 2000; i++) {
    const dialog = roots[i].querySelector('ha-voice-command-dialog');
    if (dialog) { dialog.closeDialog(); return; }
    for (const element of roots[i].querySelectorAll('*')) {
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
  }
})()
