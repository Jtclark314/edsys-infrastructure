// Only the explicit application-local alias opts in to answer assistance.
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

const UPSTREAM_SHA256 = "3208973be792e4b616c2048945fa6541c836c7acd97025081c34f43858b86305";
const upstreamPath = path.join(__dirname, "edsys-upstream-litellm.cjs");
let upstreamBytes;
try {
  upstreamBytes = fs.readFileSync(upstreamPath);
} catch {
  throw new Error("Pinned AnythingLLM LiteLLM provider is unavailable.");
}
if (createHash("sha256").update(upstreamBytes).digest("hex") !== UPSTREAM_SHA256) {
  throw new Error("Pinned AnythingLLM LiteLLM provider hash differs; refusing to load.");
}

const { LiteLLM: UpstreamLiteLLM } = require("./edsys-upstream-litellm.cjs");

class LiteLLM extends UpstreamLiteLLM {
  constructor(embedder = null, modelPreference = null) {
    if (modelPreference === "edsys-knowledge-local-first") {
      const { RagAssistConnector } = require("./edsys-rag-assist.cjs");
      return new RagAssistConnector({ embedder });
    }
    super(embedder, modelPreference);
  }
}

module.exports = { LiteLLM };
