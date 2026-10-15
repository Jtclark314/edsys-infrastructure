# Dimensional Phil animation source

Owner-selected design B, original Phil character identity. The built-in image
generation tool produced matching idle, blink, listening, thinking, speaking,
greeting and error source poses. `source-prompts.json` records prompts and exact
source hashes. Source artwork and generated media stay outside Git. Copy the
approved seven PNGs into ignored `public/phil/` (or link the reviewed source
folder there); both render scripts reject hashes that differ from the selection.
The deployed animation is a 2D puppet of the dimensional illustration, not a 3D
model or audio-driven lip sync.

The original poses are 1024×1536. Remotion 4.0.489 renders six 512×768 RGBA loops,
48 frames/4 seconds each at 12 fps. A deterministic continuous image-space rig
animates the head, hair, breathing and hand gestures. Masked matching edit frames
provide the idle blink and varied speaking mouth. Triangle clip overlap and
near-opaque interior normalization remove mesh seams while retaining hair alpha.
No remote assets, runtime household data, TTS or provider credentials are used.

```bash
npm ci
# Restore the reviewed source PNGs into public/phil/ first.
npm run check
npm run render
node review.mjs
```

Exact packages/lock derive from the canonical 9950x Remotion toolkit; its
installed dependencies can be reused with an ignored node_modules link. Keep
default managed Headless Shell/gl=null and concurrency six. AI Store must be
mounted. Frame outputs/manifest/ZIP/review are under
`/mnt/ai-store/remotion/outputs/bedroom-phil/`; bundles use its sibling tmp tree.
The review uses NVENC H.264, no audio, limited-range BT.709/yuv420p and fast start.

Full rendering is the delivery build; single-state rendering is iteration only.
Run the parent `tools/build_kodi.py` with `--animation-dir` pointing to the full
output folder. It verifies exactly 288 frame names, manifest digests and frame
dimensions before constructing the closed 310-file add-on. Retain the output
manifest and reviewed source art with private project assets, outside Git/RAG.

The Pi displays cached local PNGs, not this Remotion/browser runtime or a second
video. Manual Show butler greets once then idles; other manual cards use idle.
Synthetic local scenes exercise listening/thinking/speaking/error. Speaking
requires the existing responding-plus-audio flag. Actual Nimo audio timing and
automatic HA presentation events remain a separate HA-owned integration.
