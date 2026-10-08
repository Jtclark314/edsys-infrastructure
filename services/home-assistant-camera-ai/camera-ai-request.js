// All requests use the signed-in Home Assistant session and its existing provider.
export const DEFAULT_QUESTION = "Describe what's happening. Mention visible people, vehicles, and anything unusual. If the image is unclear, say so.";
export const responseData = result => result?.response ?? result;

export class CameraAIRequest {
  constructor(onChange = () => {}) {
    this.onChange = onChange;
    this.busy = false;
    this.result = null;
    this.status = '';
    this.error = '';
  }

  notify() { this.onChange(this); }

  async run(hass, {provider, camera, question, allowedCameras}) {
    if (this.busy) return false;
    if (!provider || !allowedCameras.includes(camera) ||
        !hass.states[camera] || ['unavailable', 'unknown'].includes(hass.states[camera].state)) {
      this.error = 'Choose an available camera before analyzing.';
      this.notify();
      return false;
    }
    question = question.trim();
    if (!question || question.length > 2000) {
      this.error = 'Enter a question of up to 2,000 characters.';
      this.notify();
      return false;
    }
    this.busy = true;
    this.error = '';
    this.status = 'Analyzing this camera snapshot…';
    this.notify();
    const start = new Date().toISOString();
    const name = hass.states[camera].attributes.friendly_name || camera;
    try {
      const response = responseData(await hass.callWS({
        type: 'call_service', domain: 'llmvision', service: 'image_analyzer',
        service_data: {
          provider, image_entity: [camera], message: question,
          include_filename: true, target_width: 1280, max_tokens: 500,
          generate_title: false, use_memory: false, expose_images: true,
          store_in_timeline: false, response_format: 'text'
        }, return_response: true
      }));
      if (typeof response?.response_text !== 'string' || !response.response_text.trim()) {
        throw new Error('Empty response');
      }
      this.result = {camera, name, question, answer: response.response_text,
        start, end: new Date().toISOString(), saved: false};
      this.status = 'Saving the answer to history…';
      this.notify();
      try {
        const event = {
          title: `${name} · Camera AI`,
          description: `Question: ${question}\n\nAnswer:\n${this.result.answer}`,
          camera_entity: camera, start_time: start, end_time: this.result.end,
          label: 'Camera'
        };
        if (typeof response.key_frame === 'string' && response.key_frame.startsWith('/media/')) {
          event.image_path = response.key_frame;
        }
        await hass.callWS({type: 'call_service', domain: 'llmvision',
          service: 'create_event', service_data: event});
        this.result.saved = true;
        this.status = 'Saved to history.';
      } catch {
        // Preserve a successful paid answer when the separate local history write fails.
        this.status = '';
        this.error = 'The answer is ready, but history could not be confirmed. Keep or copy the answer below and check Recent analyses before running again.';
      }
      return true;
    } catch (error) {
      this.status = '';
      this.error = error?.code === 'unauthorized'
        ? 'Your account cannot run this action. Ask a Home Assistant administrator to check permissions.'
        : 'The analysis did not return an answer. Check the camera and LLM Vision provider in Settings → Devices & services. No automatic retry was sent.';
      return false;
    } finally {
      this.busy = false;
      this.notify();
    }
  }
}
