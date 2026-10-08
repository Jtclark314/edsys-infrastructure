import {CameraAIRequest, DEFAULT_QUESTION} from './camera-ai-request.js';

class EdsysCameraAICard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this.request = new CameraAIRequest(() => this.update());
    this.previewGeneration = 0;
    this.restored = false;
  }

  setConfig(config) {
    if (!config.provider || !Array.isArray(config.cameras) || !config.cameras.length ||
        config.cameras.some(camera => typeof camera !== 'string' || !camera.startsWith('camera.'))) {
      throw new Error('Camera AI needs an existing LLM Vision provider and camera list.');
    }
    this.config = {...config};
    this.selected = config.default_camera || config.cameras[0];
    if (!config.cameras.includes(this.selected)) this.selected = config.cameras[0];
    this.build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    if (!this.restored && !this.request.busy && !this.request.result) {
      const latest = hass.states[this.config.timeline_entity || 'calendar.llm_vision_timeline']?.attributes;
      if (latest?.description && latest.title?.endsWith(' · Camera AI') &&
          this.config.cameras.includes(latest.camera_name)) {
        this.request.result = {camera: latest.camera_name,
          name: this.cameraName(latest.camera_name), answer: latest.description,
          start: latest.starts, saved: true};
      }
      this.restored = true;
    }
    this.update();
    if (this.preview) this.preview.hass = hass;
    if (!this.preview && !this.previewLoading) this.makePreview();
  }

  cameraName(entity) { return this._hass?.states[entity]?.attributes.friendly_name || entity; }
  getCardSize() { return 12; }
  getGridOptions() { return {columns: 12, rows: 'auto'}; }

  build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,inherit)}
        ha-card{display:block;overflow:hidden} .content{padding:20px;display:grid;gap:16px}
        h2{font-size:22px;margin:0} p{margin:0;line-height:1.5} .muted{color:var(--secondary-text-color);font-size:14px}
        label{font-size:14px;font-weight:600;display:grid;gap:8px}
        select,textarea,button{font:inherit;box-sizing:border-box;border-radius:10px}
        select,textarea{width:100%;padding:12px;border:1px solid var(--divider-color,#888);background:var(--card-background-color,#fff);color:var(--primary-text-color,#222)}
        textarea{resize:vertical;min-height:104px;line-height:1.5}
        button{min-height:44px;padding:10px 16px;border:0;cursor:pointer;background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff);font-weight:600}
        button.secondary{background:transparent;color:var(--primary-color,#03a9f4);border:1px solid var(--divider-color,#888)}
        button:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:default}
        button:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid var(--primary-color);outline-offset:3px}
        .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap} .row button:first-child{flex:1}
        #preview{border-radius:12px;overflow:hidden;min-height:80px}
        #status{font-size:14px} #error{color:var(--error-color,#db4437);font-size:14px;line-height:1.5}
        .result{border-top:1px solid var(--divider-color);padding-top:16px;display:grid;gap:10px}
        .result h3{font-size:17px;margin:0} #answer{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6;user-select:text}
        #result-question{white-space:pre-wrap;overflow-wrap:anywhere} [hidden]{display:none!important}
        @media(max-width:400px){.content{padding:16px}.row{gap:8px}}
      </style>
      <ha-card><div class="content">
        <div><h2>Ask about a camera</h2><p class="muted">Choose a camera, ask a question, and analyze a fresh snapshot.</p></div>
        <label>Camera<select id="camera" aria-label="Camera"></select></label>
        <div id="preview" aria-label="Camera preview"><p class="muted">Loading camera preview…</p></div>
        <label>Your question<textarea id="question" aria-label="Your question" maxlength="2000"></textarea></label>
        <div class="row"><button id="analyze">Analyze now</button><button id="refresh" class="secondary">Refresh preview</button></div>
        <p class="muted">Each analysis sends one snapshot to your configured OpenAI provider. Results and snapshots follow LLM Vision’s history retention.</p>
        <p id="status" role="status" aria-live="polite" hidden></p>
        <p id="error" role="alert" hidden></p>
        <section id="result" class="result" aria-label="Latest answer" hidden>
          <h3>Latest answer</h3><p id="result-meta" class="muted"></p><p id="result-question" class="muted" hidden></p><div id="answer"></div>
        </section>
      </div></ha-card>`;
    this.el = Object.fromEntries(['camera','question','preview','analyze','refresh','status','error','result','result-meta','result-question','answer']
      .map(id => [id, this.shadowRoot.getElementById(id)]));
    this.el.question.value = this.config.default_question || DEFAULT_QUESTION;
    for (const camera of this.config.cameras) {
      const option = document.createElement('option');
      option.value = camera;
      this.el.camera.append(option);
    }
    this.el.camera.value = this.selected;
    this.el.camera.addEventListener('change', () => {
      this.selected = this.el.camera.value;
      this.request.error = '';
      this.update();
      this.makePreview();
    });
    this.el.question.addEventListener('input', () => this.update());
    this.el.refresh.addEventListener('click', () => this.makePreview());
    this.el.analyze.addEventListener('click', () => this.request.run(this._hass, {
      provider: this.config.provider, camera: this.selected,
      question: this.el.question.value, allowedCameras: this.config.cameras
    }));
    this.update();
  }

  async makePreview() {
    if (!this._hass || !this.config) return;
    const generation = ++this.previewGeneration;
    this.previewLoading = true;
    try {
      const helpers = await window.loadCardHelpers();
      const preview = helpers.createCardElement({type: 'picture-entity', entity: this.selected,
        camera_view: 'auto', show_name: false, show_state: false, aspect_ratio: '16:9',
        tap_action: {action: 'more-info'}, hold_action: {action: 'none'}});
      if (generation !== this.previewGeneration) return;
      preview.hass = this._hass;
      this.preview = preview;
      this.el.preview.replaceChildren(preview);
    } catch {
      if (generation !== this.previewGeneration) return;
      this.preview = null;
      this.el.preview.textContent = 'Preview could not load. Use Refresh preview to try again.';
    } finally {
      if (generation === this.previewGeneration) this.previewLoading = false;
    }
  }

  update() {
    if (!this.el || !this.config) return;
    const busy = this.request.busy;
    for (const option of this.el.camera.options) {
      const entity = this._hass?.states[option.value];
      const unavailable = !entity || ['unavailable','unknown'].includes(entity.state);
      option.disabled = unavailable;
      option.textContent = this.cameraName(option.value) + (unavailable ? ' (unavailable)' : '');
    }
    const selected = this._hass?.states[this.selected];
    const available = selected && !['unavailable','unknown'].includes(selected.state);
    this.el.camera.disabled = busy;
    this.el.question.disabled = busy;
    this.el.refresh.disabled = busy;
    this.el.analyze.disabled = busy || !available || !this.el.question.value.trim();
    this.el.analyze.textContent = busy ? 'Working…' : 'Analyze now';
    this.el.analyze.setAttribute('aria-busy', String(busy));
    this.el.status.textContent = this.request.status;
    this.el.status.hidden = !this.request.status;
    this.el.error.textContent = this.request.error;
    this.el.error.hidden = !this.request.error;
    const result = this.request.result;
    this.el.result.hidden = !result;
    if (result) {
      const time = result.start ? new Date(result.start).toLocaleString() : '';
      this.el['result-meta'].textContent = `${result.name} · ${time}${result.saved ? ' · Saved' : ''}`;
      this.el['result-question'].textContent = result.question ? `Question: ${result.question}` : '';
      this.el['result-question'].hidden = !result.question;
      this.el.answer.textContent = result.answer;
    }
  }
}
if (!customElements.get('edsys-camera-ai-card')) customElements.define('edsys-camera-ai-card', EdsysCameraAICard);
window.customCards = window.customCards || [];
window.customCards.push({type: 'edsys-camera-ai-card', name: 'EdSys Camera AI',
  description: 'Ask about a camera snapshot using the existing LLM Vision provider.'});

// The stock timeline caches fetches. Recreate it only when HA publishes a new
// timeline event, so a manual answer appears without requiring a page reload.
class EdsysCameraAIHistory extends HTMLElement {
  setConfig(config) {
    this.config = {...config, type: 'custom:llmvision-card'};
    delete this.config.timeline_entity;
    this.generation = 0;
  }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const latest = hass.states['calendar.llm_vision_timeline']?.attributes;
    const key = JSON.stringify([latest?.starts, latest?.title, latest?.description]);
    if (key !== this.key) {
      this.key = key;
      this.makeCard();
    } else if (this.card) this.card.hass = hass;
  }
  async makeCard() {
    const generation = ++this.generation;
    try {
      await customElements.whenDefined('llmvision-card');
      const helpers = await window.loadCardHelpers();
      if (generation !== this.generation) return;
      this.card = helpers.createCardElement(this.config);
      this.card.hass = this._hass;
      this.replaceChildren(this.card);
    } catch {
      if (generation === this.generation) this.textContent = 'History could not load. Reload this page to try again.';
    }
  }
  getCardSize() { return 8; }
  getGridOptions() { return {columns: 12, rows: 'auto'}; }
}
if (!customElements.get('edsys-camera-ai-history')) customElements.define('edsys-camera-ai-history', EdsysCameraAIHistory);
