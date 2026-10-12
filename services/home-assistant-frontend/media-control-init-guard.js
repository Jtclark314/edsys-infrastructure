const TAG = "hui-media-control-card";
const INSTALLED = Symbol.for("edsys.media-control-init-guard.v1");
const AFFECTED_GETTER = /^get\s+_stateObj\(\)\s*\{\s*return\s+this\.hass\.states\[this\._config\.entity\];?\s*\}$/;

export function guardMediaControlInitialization(Card) {
  const prototype = Card?.prototype;
  const descriptor = prototype && Object.getOwnPropertyDescriptor(prototype, "_stateObj");
  const original = descriptor?.get;
  if (original?.[INSTALLED]) return "already-installed";
  if (!descriptor?.configurable || !original || !AFFECTED_GETTER.test(original.toString())) {
    return "not-affected";
  }

  // The native progress controller runs before setConfig during lazy loading.
  function getStateWhenReady() {
    if (!this.hass?.states || !this._config?.entity) return undefined;
    return original.call(this);
  }
  Object.defineProperty(getStateWhenReady, INSTALLED, { value: true });
  Object.defineProperty(prototype, "_stateObj", { ...descriptor, get: getStateWhenReady });
  return "installed";
}

export async function initializeMediaControlGuard(browser) {
  const registry = browser.customElements;
  if (registry.get(TAG)) return guardMediaControlInitialization(registry.get(TAG));

  const previous = Object.getOwnPropertyDescriptor(registry, "define");
  const originalDefine = registry.define;
  let result;
  function restoreDefinition() {
    if (registry.define !== defineWithGuard) return;
    if (previous) Object.defineProperty(registry, "define", previous);
    else delete registry.define;
  }
  function defineWithGuard(name, Card, ...options) {
    if (name === TAG) {
      // Guard before define upgrades already-connected, unconfigured elements.
      result = guardMediaControlInitialization(Card);
      restoreDefinition();
    }
    return originalDefine.call(this, name, Card, ...options);
  }
  Object.defineProperty(registry, "define", { value: defineWithGuard, configurable: true, writable: true });
  try {
    const helpers = await browser.loadCardHelpers();
    helpers.createCardElement({ type: "media-control", entity: "media_player.edsys_media_guard_bootstrap" });
    await registry.whenDefined(TAG);
    return result ?? guardMediaControlInitialization(registry.get(TAG));
  } finally {
    restoreDefinition();
  }
}

if (typeof window !== "undefined") {
  await initializeMediaControlGuard(window);
}
