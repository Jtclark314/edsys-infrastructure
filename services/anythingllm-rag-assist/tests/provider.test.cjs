const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "..", "provider.cjs"), "utf8");
const pinnedHash = "3208973be792e4b616c2048945fa6541c836c7acd97025081c34f43858b86305";

function fixture({ hash = pinnedHash, realCrypto = false, readFailure = false } = {}) {
  const calls = { order: [], nativeConstructors: [], assistConstructors: [], generations: 0, staticCalls: [] };
  const pinnedBytes = Buffer.from("synthetic upstream bytes; never executed");
  class UpstreamLiteLLM {
    constructor(embedder = null, modelPreference = null) {
      this.embedder = embedder;
      this.modelPreference = modelPreference;
      this.nativeSentinel = {};
      calls.nativeConstructors.push({ target: this, embedder, modelPreference });
    }
    static promptWindowLimit(model) {
      calls.staticCalls.push({ target: this, model });
      return 4096;
    }
    static inheritedSetting = "synthetic-upstream-setting";
    streamingEnabled() { return true; }
    async getChatCompletion() { calls.generations++; throw Error("Constructor must not generate"); }
  }
  class RagAssistConnector extends UpstreamLiteLLM {
    constructor(options) {
      super(options.embedder, "edsys-chat-local");
      this.assistSentinel = {};
      calls.assistConstructors.push({ target: this, options });
    }
    streamingEnabled() { return false; }
  }
  const checkedCrypto = {
    createHash(algorithm) {
      assert.equal(algorithm, "sha256");
      calls.order.push("hash");
      return {
        update(bytes) { assert.equal(bytes, pinnedBytes); return this; },
        digest(encoding) { assert.equal(encoding, "hex"); return hash; },
      };
    },
  };
  const sandbox = {
    __dirname: "/synthetic/liteLLM",
    module: { exports: {} },
    require(name) {
      if (name === "node:fs") return {
        readFileSync(filename) {
          assert.equal(filename, "/synthetic/liteLLM/edsys-upstream-litellm.cjs");
          calls.order.push("read");
          if (readFailure) throw Error("Private filesystem detail");
          return pinnedBytes;
        },
      };
      if (name === "node:path") return path;
      if (name === "node:crypto") return realCrypto ? crypto : checkedCrypto;
      if (name === "./edsys-upstream-litellm.cjs") {
        calls.order.push("upstream");
        return { LiteLLM: UpstreamLiteLLM };
      }
      if (name === "./edsys-rag-assist.cjs") {
        calls.order.push("assist");
        return { RagAssistConnector };
      }
      throw Error(`Unexpected dependency: ${name}`);
    },
  };
  function load() {
    vm.runInNewContext(source, sandbox, { filename: "provider.cjs" });
    return sandbox.module.exports.LiteLLM;
  }
  return { load, calls, UpstreamLiteLLM, RagAssistConnector };
}

test("the exact upstream hash is checked before execution and assistance stays lazy", () => {
  const f = fixture();
  const Provider = f.load();
  assert.ok(Provider.prototype instanceof f.UpstreamLiteLLM);
  assert.deepEqual(f.calls.order, ["read", "hash", "upstream"]);
  assert.equal(f.calls.nativeConstructors.length, 0);
  assert.equal(f.calls.assistConstructors.length, 0);
});

test("changed or unreadable upstream bytes cannot execute either provider module", () => {
  for (const options of [{ realCrypto: true }, { hash: "0".repeat(64) }, { readFailure: true }]) {
    const f = fixture(options);
    assert.throws(f.load, /Pinned AnythingLLM LiteLLM provider (hash differs|is unavailable)/);
    assert.ok(!f.calls.order.includes("upstream"));
    assert.ok(!f.calls.order.includes("assist"));
  }
});

test("only the exact explicit alias constructs assistance without generating or double initialization", () => {
  const f = fixture(), embedder = { synthetic: "configured-embedder" };
  const instance = new (f.load())(embedder, "edsys-knowledge-local-first");
  assert.ok(instance instanceof f.RagAssistConnector);
  assert.equal(f.calls.assistConstructors.length, 1);
  assert.equal(f.calls.assistConstructors[0].target, instance);
  assert.equal(f.calls.assistConstructors[0].options.embedder, embedder);
  assert.equal(Object.keys(f.calls.assistConstructors[0].options).join(","), "embedder");
  assert.equal(f.calls.nativeConstructors.length, 1);
  assert.equal(f.calls.nativeConstructors[0].modelPreference, "edsys-chat-local");
  assert.equal(instance.streamingEnabled(), false);
  assert.equal(f.calls.generations, 0);
});

test("all other explicit models preserve upstream construction, embedder and behavior", () => {
  for (const model of ["edsys-chat-local", "edsys-chat-cloud", "gpt-5-chat-latest", "EDSYS-KNOWLEDGE-LOCAL-FIRST", "edsys-knowledge-local-first-extra", " edsys-knowledge-local-first", ""]) {
    const f = fixture(), embedder = { synthetic: model };
    const Provider = f.load();
    const instance = new Provider(embedder, model);
    assert.ok(instance instanceof f.UpstreamLiteLLM);
    assert.ok(instance instanceof Provider);
    assert.equal(f.calls.nativeConstructors.length, 1);
    assert.equal(f.calls.nativeConstructors[0].target, instance);
    assert.equal(f.calls.nativeConstructors[0].embedder, embedder);
    assert.equal(f.calls.nativeConstructors[0].modelPreference, model);
    assert.equal(instance.embedder, embedder);
    assert.equal(instance.streamingEnabled(), true);
    assert.equal(f.calls.assistConstructors.length, 0);
    assert.ok(!f.calls.order.includes("assist"));
    assert.equal(f.calls.generations, 0);
  }
});

test("omitted or null defaults remain upstream defaults, not an implicit assistance opt-in", () => {
  for (const args of [[], [null], [undefined, undefined], [null, null], [{ synthetic: "embedder" }, null]]) {
    const f = fixture(), Provider = f.load();
    const instance = new Provider(...args);
    assert.ok(instance instanceof f.UpstreamLiteLLM);
    assert.equal(f.calls.nativeConstructors.length, 1);
    assert.equal(instance.embedder, args[0] ?? null);
    assert.equal(instance.modelPreference, null);
    assert.equal(f.calls.assistConstructors.length, 0);
    assert.ok(!f.calls.order.includes("assist"));
  }
});

test("native static methods and settings are inherited without an assist constructor", () => {
  const f = fixture(), Provider = f.load();
  assert.equal(Provider.promptWindowLimit, f.UpstreamLiteLLM.promptWindowLimit);
  assert.equal(Provider.promptWindowLimit("synthetic-model"), 4096);
  assert.equal(f.calls.staticCalls[0].target, Provider);
  assert.equal(f.calls.staticCalls[0].model, "synthetic-model");
  assert.equal(Provider.inheritedSetting, "synthetic-upstream-setting");
  assert.equal(f.calls.nativeConstructors.length, 0);
  assert.equal(f.calls.assistConstructors.length, 0);
});

test("each opted-in construction gets a fresh connector without shared model state", () => {
  const f = fixture(), Provider = f.load();
  const firstEmbedder = { synthetic: "first" }, secondEmbedder = { synthetic: "second" };
  const first = new Provider(firstEmbedder, "edsys-knowledge-local-first");
  const second = new Provider(secondEmbedder, "edsys-knowledge-local-first");
  assert.notEqual(first, second);
  assert.notEqual(first.assistSentinel, second.assistSentinel);
  assert.equal(first.embedder, firstEmbedder);
  assert.equal(second.embedder, secondEmbedder);
  assert.equal(f.calls.assistConstructors.length, 2);
  assert.equal(f.calls.generations, 0);
});
