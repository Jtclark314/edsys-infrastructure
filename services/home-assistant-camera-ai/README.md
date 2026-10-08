# Home Assistant Camera AI

Camera AI is a manual question-and-answer tab in the existing Cameras dashboard.
Choose a camera, inspect its preview, type a question, and press **Analyze now**.
The answer shows its camera and time. Recent analyses opens the saved question,
answer, and snapshot through the official LLM Vision Timeline Card.

## Ownership and current deployment

- Source: this directory in `edsys-infrastructure`.
- Host: Home Assistant OS VM300 on pve-node3; Core 2026.10.0.
- Live path: `http://192.168.50.75/edsys-cameras/camera-ai`.
- Custom control/history wrapper: version 1.0.1, served from
  `/config/www/edsys-camera-ai/v1.0.1/`.
- Stock timeline: manually deployed, checksum-pinned v1.7.2 from
  [valentinfrlch/llmvision-card](https://github.com/valentinfrlch/llmvision-card/tree/v1.7.2/dist).
  It is not registered as a HACS-managed frontend package.
- Existing LLM Vision v1.7.2 OpenAI provider; its configured default was
  GPT-4o mini at acceptance. The card does not override the model.
- Existing Settings entry retains timeline records for seven days. Retention
  and provider billing remain the existing integration/provider settings.

The view lists eight currently registered camera entities. It adds no service,
port, container, automation, motion trigger, notification, or credential.
Native HA card helpers handle previews with the signed-in HA session. The
browser sends an opaque provider entry identifier; its API key remains in HA.
Runtime dashboard/registry copies and captured media belong outside Git/RAG.

## Request behavior

One button press calls `llmvision.image_analyzer` for one selected camera,
1280-pixel target width and at most 500 output tokens. Additional title
inference and memory are disabled. A separate local `llmvision.create_event`
write saves the full question, answer, camera, timestamp and exposed key frame
without a second OpenAI analysis.

Inputs remain locked until the action finishes, with no automatic retry.
Unavailable/missing cameras and empty questions cannot submit. An analysis
failure keeps the prior answer. A history-write failure keeps the new answer
visible and says its persistence is unconfirmed. Inspect history before
submitting another paid analysis after an ambiguous result.

Model output is displayed as plain text. On reload, the latest manual Camera AI
answer is recovered from the existing calendar's attributes. The history
wrapper recreates the stock timeline only when those attributes change,
avoiding its fetch-cache delay after a save. Each camera preview is a current
preview; the history key frame is the image associated with the analysis.

## Deployment and recovery

1. Obtain explicit change authority. Read the current dashboard/resources via
   the existing route documented in [HOME_ASSISTANT_CONTROL.md](../../docs/HOME_ASSISTANT_CONTROL.md).
   Save exact recovery copies privately and verify a VM snapshot completes.
2. Stage the stock frontend outside this repository:
   `python3 prepare-assets.py /PRIVATE/STAGING/timeline-dist`.
   Every file must match `timeline-lock.json`, including its imported sibling
   modules. Keep the distribution build outside Git.
3. Copy the two custom JavaScript modules to the versioned directory above.
   Copy all four verified stock files, preserving paths, to
   `/config/www/edsys-camera-ai/vendor/llmvision-card-v1.7.2/`.
   Use atomic writes and read back file digests. Existing root SSH/QEMU guest
   execution is the operator route; do not export HA authentication material.
4. Register two module resources through HA's native resource API:
   `/local/edsys-camera-ai/v1.0.1/edsys-camera-ai-card.js` and
   `/local/edsys-camera-ai/vendor/llmvision-card-v1.7.2/llmvision-card.js`.
5. Replace only the placeholder in `camera-ai-view.example.json` with the
   existing loaded OpenAI configuration entry identifier in a private copy.
   Review the camera list against live HA states. Append the view to the latest
   Cameras dashboard via `lovelace/config/save`, preserving the existing views.
   Refuse an unexpected concurrent change to the dashboard.
6. Reload the browser; a Home Assistant restart is unnecessary. Use a new
   versioned directory/resource URL for subsequent custom-card revisions so
   imported modules cannot retain old cache contents.

Narrow rollback removes the Camera AI view and its two newly added resources,
then removes only this feature's frontend files. Compare the current dashboard
with its backup before restoring: do not overwrite intervening owner edits.
Existing provider configuration and saved analyses can remain. The pre-change
VM snapshot is a broader recovery option and restores unrelated HA state too;
review it with the owner before later use. Back up `/config` and the existing
LLM Vision database/media with the normal HA backup policy. Do not place these
backups in this source repository.

## Verification

```sh
node --check edsys-camera-ai-card.js
node --test tests/*.test.js
python3 -m py_compile prepare-assets.py
```

Eight regression checks cover a single paid request, full answer persistence,
double activation, invalid camera/question rejection, prior-answer retention,
local-save uncertainty, empty answers and immediate history refresh/races.
Acceptance also requires live browser tests for camera selection, a custom
question, busy controls, answer display, saved history detail/media and reload
recovery. Package/configuration success alone is not sufficient.

References: [Image Analyzer](https://llmvision.gitbook.io/getting-started/usage/image-analyzer),
[Timeline Card](https://github.com/valentinfrlch/llmvision-card),
[HA WebSocket service calls](https://developers.home-assistant.io/docs/api/websocket/).
