// Only the explicit application-local alias opts in to answer assistance.
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

// AnythingLLM 1.19.0 native LiteLLM provider. Retain the immutable v1.0.0
// release alongside this adapter for recovery to the prior image.
const UPSTREAM_SHA256 = "f45c64b9a2334523aca1fb637825eee4b6a8ed7bcbb91bf2ba5c970da76ae30f";
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
