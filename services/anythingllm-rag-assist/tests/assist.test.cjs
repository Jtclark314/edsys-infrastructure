const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

function response(status = "ready", answer = "Source-backed answer.", extra = {}) {
  return { model: "gpt-oss:20b", choices: [{ finish_reason: "stop", message: { content: JSON.stringify({ status, answer }) } }], usage: { prompt_tokens: 30, completion_tokens: 10 }, ...extra };
}

function harness(t, outcomes = []) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "edsys-rag-assist-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const calls = [];
  class LiteLLM {
    constructor(embedder) {
      this.embedder = embedder;
      this.openai = { chat: { completions: { create: async (body, options) => {
        calls.push({ body, options });
        const outcome = outcomes.shift();
        if (typeof outcome === "function") return outcome(body, options);
        if (outcome instanceof Error) throw outcome;
        if (outcome === undefined) throw new Error("Unexpected completion call");
        return outcome;
      } } } };
    }
    constructPrompt(args) { return [{ role: "system", content: args.contextTexts.join("\n") }, { role: "user", content: args.userPrompt || "What is node3?" }]; }
  }
  const sandbox = { module: { exports: {} }, process: { env: { STORAGE_DIR: directory } }, Date, setTimeout, console,
    require: name => ({ "node:fs": fs, "node:path": path, "node:crypto": crypto, "./edsys-upstream-litellm.cjs": { LiteLLM }, "../../helpers": { getEmbeddingEngineSelection: () => ({ configured: "litellm-infinity" }) }, "../../helpers/abortSignals": { isAbortError: error => error?.name === "AbortError" || error?.constructor?.name === "APIUserAbortError" } })[name] };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../assist.cjs"), "utf8"), sandbox);
  const api = sandbox.module.exports;
  const connector = new api.RagAssistConnector({ workspace: { slug: "edsys-rag-current" } });
  const messages = connector.constructPrompt({ contextTexts: ["Current node3 source context."], userPrompt: "What is node3?" });
  return { ...api, connector, messages, calls, directory };
}

test("a ready local answer remains local and never reserves cloud spend", async t => {
  const h = harness(t, [response()]);
  const answer = await h.connector.getChatCompletion(h.messages);
  assert.equal(answer.textResponse, "Source-backed answer.");
  assert.equal(answer.metrics.assistance, "local");
  assert.equal(answer.metrics.model, "edsys-chat-local");
  assert.equal(answer.metrics.cloud_attempted, false);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].body.model, "edsys-chat-local");
  assert.equal(h.calls[0].body.response_format.type, "json_object");
  assert.equal(h.connector.streamingEnabled(), false);
  assert.equal(h.connector.embedder.configured, "litellm-infinity");
  assert.equal(fs.existsSync(h.connector.budgetDirectory), false);
});

for (const status of ["insufficient_evidence", "clarification"]) {
  test(`${status} returns the local explanation without buying missing facts`, async t => {
    const h = harness(t, [response(status, "The evidence needs clarification.")]);
    const answer = await h.connector.getChatCompletion(h.messages);
    assert.equal(answer.textResponse, "The evidence needs clarification.");
    assert.equal(h.calls.length, 1);
  });
}

test("local help requests send original context once to the existing OpenAI route", async t => {
  const h = harness(t, [response("needs_help", "DO NOT FORWARD THIS DRAFT"), { model: "edsys-knowledge-openai", choices: [{ finish_reason: "stop", message: { content: "Supported cloud answer." } }], usage: { prompt_tokens: 40, completion_tokens: 15 } }]);
  const answer = await h.connector.getChatCompletion(h.messages);
  assert.equal(h.calls.length, 2);
  const cloud = h.calls[1];
  assert.equal(cloud.body.model, "edsys-knowledge-openai");
  assert.equal(JSON.stringify(cloud.body.messages).includes("DO NOT FORWARD"), false);
  assert.equal(JSON.stringify(cloud.body.messages).includes('"status"'), false);
  assert.equal(cloud.body.messages[0].content, h.messages[0].content);
  assert.equal(cloud.body.max_completion_tokens, 1200);
  assert.equal(cloud.body.reasoning_effort, "low");
  assert.equal(cloud.body.allowed_openai_params.join(","), "reasoning_effort");
  assert.equal(cloud.body.store, false);
  assert.equal(cloud.body.num_retries, 0);
  assert.equal(cloud.body.fallbacks.length, 0);
  assert.equal(cloud.options.maxRetries, 0);
  assert.equal(cloud.options.timeout, 60000);
  assert.equal(cloud.options.headers["no-log"], "true");
  assert.equal(answer.textResponse, "OpenAI assistance\n\nSupported cloud answer.");
  assert.equal(answer.metrics.model, "edsys-knowledge-openai");
  assert.equal(answer.metrics.assistance, "cloud");
  assert.equal(answer.metrics.cloud_attempted, true);
  assert.equal(answer.metrics.prompt_tokens, 70);
});

for (const [name, outcome] of [
  ["local failure", new Error("local offline")],
  ["truncation", response("ready", "Partial", { choices: [{ finish_reason: "length", message: { content: "partial" } }] })],
  ["invalid JSON", response("ready", "", { choices: [{ finish_reason: "stop", message: { content: "unstructured" } }] })],
  ["empty answer", response("ready", "")],
]) {
  test(`${name} allows at most one cloud attempt with no recursive retry`, async t => {
    const h = harness(t, [outcome, new Error("cloud offline")]);
    const answer = await h.connector.getChatCompletion(h.messages);
    assert.equal(h.calls.length, 2);
    assert.equal(answer.metrics.assistance, "blocked");
    assert.equal(answer.metrics.cloud_attempted, true);
    assert.equal(answer.metrics.blocked_reason, "cloud_unavailable");
    assert.match(answer.textResponse, /no automatic retry/);
  });
}

test("a broker local fallback is never labeled successful OpenAI assistance", async t => {
  const h = harness(t, [response("needs_help"), { model: "gpt-oss:20b", choices: [{ finish_reason: "stop", message: { content: "not cloud" } }] }]);
  const answer = await h.connector.getChatCompletion(h.messages);
  assert.equal(answer.metrics.assistance, "blocked");
  assert.equal(answer.textResponse.includes("not cloud"), false);
});

test("user cancellation during the local request never escalates", async t => {
  const controller = new AbortController();
  const h = harness(t, [() => { controller.abort(); throw new Error("closed"); }]);
  h.connector.abortSignal = controller.signal;
  await assert.rejects(() => h.connector.getChatCompletion(h.messages));
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].options.signal, controller.signal);
  assert.equal(fs.existsSync(h.connector.budgetDirectory), false);
});

test("a stopped request cannot start a local or cloud generation", async t => {
  const h = harness(t);
  const controller = new AbortController(); controller.abort();
  h.connector.abortSignal = controller.signal;
  await assert.rejects(() => h.connector.getChatCompletion(h.messages));
  assert.equal(h.calls.length, 0);
});

for (const [name, change, expected] of [
  ["earlier local-only turn", h => h.messages.splice(1, 0, { role: "user", content: "Keep this chat local only." }, { role: "assistant", content: "Private local answer." }), "local_only_history"],
  ["current no-cloud instruction", h => h.messages.push({ role: "user", content: "Don't send this question to OpenAI." }), "local_only_history"],
  ["plain no-cloud wording", h => h.messages.push({ role: "user", content: "No cloud for this question." }), "local_only_history"],
  ["only-local wording", h => h.messages.push({ role: "user", content: "Use only the local model." }), "local_only_history"],
  ["curly apostrophe", h => h.messages.push({ role: "user", content: "Don’t send this to OpenAI." }), "local_only_history"],
  ["attachment", h => { h.connector.attachments = [{ name: "private.pdf" }]; }, "attachments_local_only"],
  ["image content part", h => h.messages.push({ role: "user", content: [{ type: "image_url", image_url: { url: "data:image/png;base64,synthetic" } }] }), "attachments_local_only"],
  ["credential-shaped context", h => { h.messages[0].content = "sk-" + "z".repeat(30); }, "sensitive_content"],
  ["overlong context", h => { h.messages[0].content = "x".repeat(32001); }, "context_limit"],
  ["no retrieved evidence", h => { h.connector.hasContext = false; }, "no_evidence"],
]) {
  test(`${name} blocks cloud even when the local model asks for help`, async t => {
    const h = harness(t, [response("needs_help", "unreliable draft")]); change(h);
    const answer = await h.connector.getChatCompletion(h.messages);
    assert.equal(h.calls.length, expected === "no_evidence" ? 0 : 1);
    assert.equal(answer.metrics.blocked_reason, expected);
    assert.equal(answer.metrics.cloud_attempted, false);
    assert.equal(answer.textResponse.includes("unreliable draft"), false);
  });
}

test("the persistent daily limit survives connectors and resets only on a later UTC day", t => {
  const h = harness(t), day = new Date("2026-10-17T10:00:00Z");
  for (let i = 0; i < 20; i++) h.reserveCloud(h.directory, day);
  assert.throws(() => h.reserveCloud(h.directory, day), /daily_cloud_limit/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(h.directory, "cloud-budget.json"))).used, 20);
  h.reserveCloud(h.directory, new Date("2026-10-18T00:00:00Z"));
  assert.equal(JSON.parse(fs.readFileSync(path.join(h.directory, "cloud-budget.json"))).used, 1);
  assert.equal(fs.statSync(path.join(h.directory, "cloud-budget.json")).mode & 0o777, 0o600);
});

test("corrupt/future budget state, symlinks and an existing lock fail closed", t => {
  const h = harness(t), file = path.join(h.directory, "cloud-budget.json"), day = new Date("2026-10-17T10:00:00Z");
  fs.writeFileSync(file, "corrupt"); assert.throws(() => h.reserveCloud(h.directory, day));
  fs.writeFileSync(file, JSON.stringify({ version: 1, date: "2027-01-01", used: 0 })); assert.throws(() => h.reserveCloud(h.directory, day));
  fs.writeFileSync(file, JSON.stringify({ version: 1, date: "2026-10-00", used: 20 })); assert.throws(() => h.reserveCloud(h.directory, day));
  fs.unlinkSync(file); fs.symlinkSync(path.join(h.directory, "missing"), file); assert.throws(() => h.reserveCloud(h.directory, day));
  fs.unlinkSync(file); fs.writeFileSync(path.join(h.directory, "cloud-budget.lock"), "held"); assert.throws(() => h.reserveCloud(h.directory, day));
  assert.equal(fs.existsSync(file), false);
});

test("extra verdict fields cannot turn malformed local output into a ready answer", t => {
  const h = harness(t);
  const result = h.verdictFrom(response("ready", "should not pass", { choices: [{ finish_reason: "stop", message: { content: JSON.stringify({ status: "ready", answer: "bad", route: "arbitrary" }) } }] }));
  assert.equal(result.status, "needs_help");
  assert.equal(result.reason, "local_invalid_format");
});
