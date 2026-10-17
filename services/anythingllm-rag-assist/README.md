# AnythingLLM local-first knowledge assistance

Status: v1.0.0 deployed and native local document-Q&A verified on 2026-10-17.
Installed cloud-handoff mechanics also passed with a controlled local-help signal.

This extension lets the managed `EdSys-RAG-Current` document-Q&A workspace
attempt a source-backed local answer before requesting bounded OpenAI help.
It adds no service, listener, web search or home/infrastructure control tools.
HA conversational voice and the Portal's separate router retain their own policies.

## Selection and integration

The verified workspace settings are strict Query mode, `chatProvider=litellm`,
`chatModel=edsys-knowledge-local-first`, and `router_id=null`. Deployment changed
only the workspace's `chatModel` field; the prior selection was `edsys-chat-local`.

Only the exact application-local alias `edsys-knowledge-local-first` opts in.
It is not a broker model. Other model selections retain native LiteLLM behavior.
The wrapper inherits native static methods and uses the configured embedder;
`edsys-embeddings-fast` continues through LiteLLM to Infinity, with LanceDB
remaining AnythingLLM's vector store. Retrieval and prompt assembly occur
before the local/cloud answer decision. No native model-router records,
classifier, sticky route cache, sidecar or global broker fallback are added.

The existing AnythingLLM 1.15.0 image remains pinned. Three read-only files
from `/opt/edsys-rag-assist/releases/v1.0.0` are mounted into its native
`/app/server/utils/AiProviders/liteLLM` directory:

| Release file | Native target |
| --- | --- |
| `provider.cjs` | `index.js` |
| `assist.cjs` | `edsys-rag-assist.cjs` |
| `upstream.cjs` | `edsys-upstream-litellm.cjs` |

The wrapper verifies upstream SHA-256
`3208973be792e4b616c2048945fa6541c836c7acd97025081c34f43858b86305`
before loading it. An upstream change fails closed and needs a separate
qualification; do not remove the guard to accommodate an image upgrade.
The deployment source is [the AnythingLLM Compose definition](../../docker/anythingllm/compose.yaml).

## Answer and privacy policy

- Attempt `edsys-chat-local` first, with a bounded JSON verdict and answer.
- Return a ready answer, insufficient-evidence explanation or clarification
  locally. With no retrieved source context, refuse before any generation.
- For `needs_help`, a local error, truncated output, invalid JSON or an empty
  answer, permit at most one OpenAI attempt. Failed cloud help returns a clear
  unavailable response; neither SDK nor recursive fallback retries are made.
- Cloud receives only the original assembled RAG context, question and retained
  chat history. The local draft/verdict protocol and the whole vault are not sent.
- Retained local-only instructions, connector-visible image/non-text
  attachments and credential-shaped material block cloud handoff. Phrase and
  credential detection are best-effort; they are not a complete secret classifier. Keep
  secrets out of chats and source documents. A retained local-only instruction
  blocks later handoff while it remains in the assembled history.
- Native AnythingLLM preprocessing turns parsed document uploads into ordinary
  source context before this connector sees them. Those documents may be sent
  with a handoff unless an explicit retained **local only** instruction blocks
  it. This release does not certify document-attachment privacy.
- Bound handoff to 32,000 assembled characters, 1,200 cloud completion tokens
  and 20 cloud attempts per UTC day. Local/cloud request timeouts are 120/60
  seconds. Reservations persist before sending, so failed attempts still count.
  Corrupt state or an existing budget lock fails closed.
- Request `store:false` and suppress broker callbacks with `no-log:true`.
  These request settings are not a provider zero-retention contract.

The scoped broker alias `edsys-knowledge-openai` targets `openai/gpt-6.1-sol`
using the existing OpenAI connection. Provisioning/readback and real cloud
qualification passed, including the installed connector with a controlled
local-help signal. Only the existing AnythingLLM service-key model
allowlist is extended. The legacy `gpt-5-chat-latest` route returned 404 during
qualification and is retained unchanged; other applications' routes are not
retargeted by this work. No provider credential is exported or embedded here.

Responses are buffered: the UI receives one complete reply after format checks
rather than token streaming. Cloud answers carry a server-generated **OpenAI assistance**
prefix and final routing/model/usage metrics. The decision relies on local
self-assessment and format/error checks; it cannot guarantee detection of a
confidently wrong answer. Live web answers, agents and control actions are
outside this qualification.

## Verification

```bash
node --test services/anythingllm-rag-assist/tests/provider.test.cjs services/anythingllm-rag-assist/tests/assist.test.cjs
node --check services/anythingllm-rag-assist/provider.cjs
node --check services/anythingllm-rag-assist/assist.cjs
python3 -m py_compile services/anythingllm-rag-assist/provision-broker.py
git diff --check
```

The 31 isolated Node checks and five focused Python verifier checks pass
(Python functions invoked directly with a temporary fixture; pytest is not
installed). The Node checks cover exact opt-in and upstream hash
gates, native/default/embedding preservation, local and cloud outcomes,
cancellation, connector-visible privacy restrictions, no-evidence refusal and durable
daily limits.

Deployment readback confirmed exact bytes for the three mounts and the pinned
upstream hash, preserving the image, environment, ports, networks and configured
`LiteLLMEmbedder`. The full retrieval gate passed 14 positive and 10 negative
controls. A real native workspace `/chat` request returned a useful correct
local answer about embeddings, Infinity and LanceDB with six sources and
`assistance=local`, `cloud_attempted=false`. An unrelated Silo question returned
the native no-evidence refusal with zero sources; its empty metrics do not
independently prove cloud-attempt counts.

The installed connector's controlled-help test returned the correct synthetic
fact from real OpenAI, made exactly one cloud call, retained `LiteLLMEmbedder`
and reported the prefix/model `edsys-knowledge-openai`. This qualifies handoff
mechanics, not the local model's ability to recognize every difficult real
question. No browser/owner
acceptance or rollback drill is claimed; broad answer-quality evaluation
remains separate. Routine retrieval/sync checks must not add background cloud
generations.

## State, backup and recovery

No new ports are opened. Existing AnythingLLM application storage remains at
`/mnt/ai-store/anythingllm` on the host. The private ledger directory is
`/app/server/storage/edsys-rag-assist` in the container; its counter is not a
dollar spending cap. Preserve it across restarts and include it in protected
application-state backups. Release files, exact prior workspace settings,
Compose metadata, the matching strict verifier and broker/key allowlist metadata need private recovery
checkpoints. Never publish keys, prompts, ledger contents or database backups.

For narrow rollback, restore the workspace's prior explicit
`litellm` / `edsys-chat-local` Query selection, restore the prior Compose
definition without the three mounts, then recreate only AnythingLLM with its
unchanged pinned image. Also atomically restore the matching prior verifier
from the private `verifier-before.py` checkpoint: the new strict checker
intentionally rejects the old model selection. Preserve application data, documents, vectors and the
ledger; do not restore the whole database to undo this connector. Remove the
dedicated broker alias/allowlist addition only after checking dependencies and
authorization. Verify local answers, embedding/vector retrieval and the full
RAG gate afterward. A rollback drill is pending.

High-level policy: `EdSys-Master/docs/RAG_HYGIENE.md` and
`docs/LITELLM_BROKER.md`.
