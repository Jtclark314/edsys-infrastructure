import assert from "node:assert/strict";
import test from "node:test";
import { guardMediaControlInitialization, initializeMediaControlGuard } from "../media-control-init-guard.js";

function nativeCard() {
  return class NativeCard {
    get _stateObj() {
      return this.hass.states[this._config.entity];
    }
    setConfig(config) {
      this._config = config;
    }
  };
}

test("reproduces the native failure before configuration", () => {
  const card = new (nativeCard())();
  card.hass = { states: {} };
  assert.throws(() => card._stateObj, /entity/);
});

for (const [name, prepare] of [
  ["neither configuration nor hass", () => {}],
  ["hass before configuration", (card) => { card.hass = { states: {} }; }],
  ["configuration before hass", (card) => { card.setConfig({ entity: "media_player.test" }); }],
  ["hass without states", (card) => { card.hass = {}; card.setConfig({ entity: "media_player.test" }); }],
  ["configuration without entity", (card) => { card.hass = { states: {} }; card.setConfig({}); }],
]) {
  test(`initialization safely returns no state with ${name}`, () => {
    const Card = nativeCard();
    assert.equal(guardMediaControlInitialization(Card), "installed");
    const card = new Card();
    prepare(card);
    assert.equal(card._stateObj, undefined);
  });
}

test("returns the original live state and follows subsequent changes", () => {
  const Card = nativeCard();
  guardMediaControlInitialization(Card);
  const card = new Card();
  card.setConfig({ entity: "media_player.test" });
  const playing = { state: "playing", attributes: { media_position: 3 } };
  card.hass = { states: { "media_player.test": playing } };
  assert.equal(card._stateObj, playing);
  const paused = { state: "paused", attributes: { media_position: 9 } };
  card.hass = { states: { "media_player.test": paused } };
  assert.equal(card._stateObj, paused);
  card.setConfig({ entity: "media_player.missing" });
  assert.equal(card._stateObj, undefined);
});

test("preserves descriptor attributes and is idempotent", () => {
  const Card = nativeCard();
  const before = Object.getOwnPropertyDescriptor(Card.prototype, "_stateObj");
  assert.equal(guardMediaControlInitialization(Card), "installed");
  const after = Object.getOwnPropertyDescriptor(Card.prototype, "_stateObj");
  assert.equal(after.configurable, before.configurable);
  assert.equal(after.enumerable, before.enumerable);
  assert.equal(after.set, before.set);
  assert.equal(guardMediaControlInitialization(Card), "already-installed");
  assert.equal(Object.getOwnPropertyDescriptor(Card.prototype, "_stateObj").get, after.get);
});

test("does not modify a changed or upstream-fixed getter", () => {
  class FixedCard {
    get _stateObj() { return this.hass?.states?.[this._config?.entity]; }
  }
  const before = Object.getOwnPropertyDescriptor(FixedCard.prototype, "_stateObj");
  assert.equal(guardMediaControlInitialization(FixedCard), "not-affected");
  assert.deepEqual(Object.getOwnPropertyDescriptor(FixedCard.prototype, "_stateObj"), before);
  assert.equal(guardMediaControlInitialization(undefined), "not-affected");
  assert.equal(guardMediaControlInitialization(class {}), "not-affected");
});

test("does not modify a non-configurable getter", () => {
  const Card = nativeCard();
  const descriptor = Object.getOwnPropertyDescriptor(Card.prototype, "_stateObj");
  Object.defineProperty(Card.prototype, "_stateObj", { ...descriptor, configurable: false });
  assert.equal(guardMediaControlInitialization(Card), "not-affected");
});

test("does not swallow unrelated failures after initialization", () => {
  const Card = nativeCard();
  guardMediaControlInitialization(Card);
  const card = new Card();
  card.setConfig({ entity: "media_player.test" });
  card.hass = { states: new Proxy({}, { get() { throw new Error("unrelated failure"); } }) };
  assert.throws(() => card._stateObj, /unrelated failure/);
});

test("uses an already-loaded native class without creating a card", async () => {
  const Card = nativeCard();
  const browser = { customElements: { get: () => Card } };
  assert.equal(await initializeMediaControlGuard(browser), "installed");
});

test("loads the native helper module without attaching a card or calling a service", async () => {
  const Card = nativeCard();
  const calls = [];
  let loaded = false;
  const browser = {
    customElements: {
      get: (tag) => { assert.equal(tag, "hui-media-control-card"); return loaded ? Card : undefined; },
      whenDefined: async (tag) => { assert.equal(tag, "hui-media-control-card"); assert.ok(loaded); },
      define: (tag, Constructor) => { assert.equal(tag, "hui-media-control-card"); assert.equal(Constructor, Card); loaded = true; },
    },
    loadCardHelpers: async () => ({ createCardElement: (config) => { calls.push(config); browser.customElements.define("hui-media-control-card", Card); } }),
  };
  assert.equal(await initializeMediaControlGuard(browser), "installed");
  assert.deepEqual(calls, [{ type: "media-control", entity: "media_player.edsys_media_guard_bootstrap" }]);
});

test("guards before definition upgrades an unconfigured element and restores define", async () => {
  const Card = nativeCard();
  const definitions = new Map();
  let atUpgrade;
  const registry = Object.create({
    get(name) { return definitions.get(name); },
    async whenDefined(name) { assert.ok(definitions.has(name)); },
    define(name, Constructor) {
      definitions.set(name, Constructor);
      if (name === "hui-media-control-card") {
        const card = new Constructor();
        card.hass = { states: {} };
        atUpgrade = card._stateObj;
      }
    },
  });
  const original = registry.define;
  const browser = { customElements: registry, loadCardHelpers: async () => ({
    createCardElement() { registry.define("hui-media-control-card", Card); },
  }) };
  assert.equal(await initializeMediaControlGuard(browser), "installed");
  assert.equal(atUpgrade, undefined);
  assert.equal(registry.define, original);
  assert.equal(Object.hasOwn(registry, "define"), false);
});

test("forwards unrelated definitions unchanged and restores an existing own descriptor", async () => {
  const Card = nativeCard();
  let loaded = false;
  const calls = [];
  const registry = {
    get: () => loaded ? Card : undefined,
    whenDefined: async () => { assert.ok(loaded); },
  };
  function define(...args) { calls.push([this, ...args]); if (args[0] === "hui-media-control-card") loaded = true; }
  Object.defineProperty(registry, "define", { value: define, configurable: true, writable: true, enumerable: false });
  const before = Object.getOwnPropertyDescriptor(registry, "define");
  const Other = class {};
  const browser = { customElements: registry, loadCardHelpers: async () => ({ createCardElement() {
    registry.define("other-card", Other, { extends: "div" });
    registry.define("hui-media-control-card", Card);
  } }) };
  assert.equal(await initializeMediaControlGuard(browser), "installed");
  assert.deepEqual(calls[0], [registry, "other-card", Other, { extends: "div" }]);
  assert.deepEqual(Object.getOwnPropertyDescriptor(registry, "define"), before);
});

test("restores normal registration when module loading fails", async () => {
  const original = () => {};
  const registry = { get: () => undefined, define: original };
  const browser = { customElements: registry, loadCardHelpers: async () => { throw new Error("module failure"); } };
  await assert.rejects(initializeMediaControlGuard(browser), /module failure/);
  assert.equal(registry.define, original);
});

test("does not overwrite another resource's concurrent registration wrapper", async () => {
  const Card = nativeCard();
  let loaded = false;
  const registry = {
    get: () => loaded ? Card : undefined,
    whenDefined: async () => {},
    define: () => { loaded = true; },
  };
  let concurrent;
  const browser = { customElements: registry, loadCardHelpers: async () => ({ createCardElement() {
    const ours = registry.define;
    concurrent = function (...args) { return ours.apply(this, args); };
    registry.define = concurrent;
    registry.define("hui-media-control-card", Card);
  } }) };
  assert.equal(await initializeMediaControlGuard(browser), "installed");
  assert.equal(registry.define, concurrent);
});
