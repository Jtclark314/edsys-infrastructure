// EdSys Family Hub 1.0.0 — native authenticated HA APIs; no external service/storage.
export const ASSET_VERSION = '1.0.0';
const DAY = 86400000;
const pad = value => String(value).padStart(2, '0');
const entityPattern = domain => new RegExp(`^${domain}\\.[a-z0-9_]+$`);
export function available(hass, entity) {
  return Boolean(hass?.callWS && hass.connected !== false && hass.connection?.connected !== false &&
    hass.states?.[entity] && !['unavailable', 'unknown'].includes(hass.states[entity].state));
}
function supports(hass, entity, bit) {
  const features = hass?.states?.[entity]?.attributes?.supported_features;
  return Number.isInteger(features) && features >= 0 && Boolean(features & bit);
}
function assertEntity(entity, domain) {
  if (!entityPattern(domain).test(entity || '')) throw Error(`Choose one configured ${domain} entity.`);
}
export function dateParts(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) throw Error('Choose a valid date.');
  const [year, month, day] = value.split('-').map(Number);
  if (year < 1970 || year > 2099 || month < 1 || month > 12 || day < 1 ||
      new Date(Date.UTC(year, month - 1, day)).getUTCMonth() !== month - 1) throw Error('Choose a valid date from 1970–2099.');
  return {year, month, day};
}
function dateString(ms) { return new Date(ms).toISOString().slice(0, 10); }
export function nextDay(value) {
  const {year, month, day} = dateParts(value);
  return dateString(Date.UTC(year, month - 1, day) + DAY);
}
function zonedParts(ms, timeZone) {
  const formatter = new Intl.DateTimeFormat('en-CA', {timeZone, year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
  return Object.fromEntries(formatter.formatToParts(new Date(ms)).filter(part => part.type !== 'literal').map(part => [part.type, Number(part.value)]));
}
export function floatingTime(ms, timeZone) {
  const p = zonedParts(ms, timeZone);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`;
}
export function wallTimeInstant(date, time, timeZone) {
  const {year, month, day} = dateParts(date);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time || '')) throw Error('Choose a valid time.');
  if (!timeZone) throw Error('Home’s time zone is unavailable. Try again when connected.');
  const [hour, minute] = time.split(':').map(Number);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  const offsets = new Set();
  try {
    for (let delta = -36; delta <= 36; delta += 6) {
      const sample = wall + delta * 3600000, p = zonedParts(sample, timeZone);
      offsets.add(Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - sample);
    }
  } catch { throw Error('Home’s time zone is unavailable.'); }
  const wanted = `${date}T${time}:00`;
  const matches = [...offsets].map(offset => wall - offset).filter(ms => floatingTime(ms, timeZone) === wanted);
  if (!matches.length) throw Error('That time is skipped by a daylight-saving clock change. Choose another time.');
  if (matches.length > 1) throw Error('That time occurs twice during a clock change. Choose an unambiguous time.');
  return matches[0];
}
export function buildCalendarEvent(draft, timeZone, allowRecurrence = false) {
  const summary = String(draft.summary || '').trim();
  if (!summary || summary.length > 200) throw Error('Enter a title of 1–200 characters.');
  dateParts(draft.date);
  const event = {summary, description: String(draft.description || '').trim(), location: String(draft.location || '').trim()};
  if (event.description.length > 2000 || event.location.length > 200) throw Error('Shorten the notes or location.');
  if (draft.allDay) {
    event.dtstart = draft.date;
    event.dtend = nextDay(draft.date);
  } else {
    const minutes = Number(draft.duration === '' || draft.duration == null ? 60 : draft.duration);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) throw Error('Choose a duration of 1–1440 minutes.');
    const start = wallTimeInstant(draft.date, draft.time, timeZone);
    const end = start + minutes * 60000;
    const endWall = floatingTime(end, timeZone);
    // The native create command takes floating home-zone values, so a fold at
    // either endpoint cannot express which occurrence was intended safely.
    wallTimeInstant(endWall.slice(0, 10), endWall.slice(11, 16), timeZone);
    event.dtstart = floatingTime(start, timeZone);
    event.dtend = endWall;
  }
  if (draft.repeat && draft.repeat !== 'none') {
    if (!allowRecurrence || !['DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'].includes(draft.repeat)) throw Error('This calendar has no configured repeat option.');
    if (!draft.allDay) {
      const minutes = Number(draft.duration === '' || draft.duration == null ? 60 : draft.duration);
      const wallSpan = Date.parse(`${event.dtend}Z`) - Date.parse(`${event.dtstart}Z`);
      if (wallSpan !== minutes * 60000) throw Error('This repeating event crosses a clock change. Use the full calendar editor for it.');
    }
    const count = Number(draft.count);
    if (!Number.isInteger(count) || count < 2 || count > 52) throw Error('Choose 2–52 occurrences.');
    event.rrule = `FREQ=${draft.repeat};COUNT=${count}`;
  }
  return event;
}
export function calendarEventMatches(item, event, timeZone) {
  const endpoint = value => {
    const point = typeof value === 'string' ? value : value?.dateTime || value?.date;
    if (!point) return null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(point)) return point;
    if (/([zZ]|[+-]\d\d:\d\d)$/.test(point)) {
      const instant = Date.parse(point);
      return Number.isFinite(instant) ? floatingTime(instant, timeZone) : null;
    }
    return point.replace(/\.\d+$/, '');
  };
  return item?.summary === event.summary && endpoint(item.start) === event.dtstart && endpoint(item.end) === event.dtend &&
    (item.location || '') === event.location && (item.description || '') === event.description;
}
export async function readCalendar(hass, entity, event, timeZone) {
  if (!available(hass, entity) || typeof hass.callApi !== 'function') throw Error('Calendar readback is unavailable.');
  const {year, month, day} = dateParts(event.dtstart.slice(0, 10));
  const start = new Date(Date.UTC(year, month - 1, day) - 2 * DAY).toISOString();
  const end = new Date(Date.UTC(year, month - 1, day) + 3 * DAY).toISOString();
  const rows = await hass.callApi('GET', `calendars/${entity}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
  if (!Array.isArray(rows)) throw Error('Calendar readback was not an event list.');
  return rows.filter(item => calendarEventMatches(item, event, timeZone));
}
export class EventSession {
  constructor(entity, onChange = () => {}) {
    assertEntity(entity, 'calendar');
    this.entity = entity; this.changed = onChange; this.busy = false; this.outcome = 'idle'; this.message = ''; this.error = '';
  }
  async submit(hass, draft, allowRecurrence) {
    if (this.busy || this.outcome !== 'idle') return false;
    if (!available(hass, this.entity) || !supports(hass, this.entity, 1)) { this.error = 'This calendar is unavailable for adding events.'; this.changed(); return false; }
    let event;
    try { event = buildCalendarEvent(draft, hass.config?.time_zone, allowRecurrence); }
    catch (error) { this.error = error.message; this.changed(); return false; }
    this.busy = true; this.error = ''; this.message = 'Adding event…'; this.changed();
    const timeZone = hass.config?.time_zone;
    let before = null;
    try { before = await readCalendar(hass, this.entity, event, timeZone); } catch { /* Read permission/outage must not prevent the explicit create. */ }
    this.event = event; this.timeZone = timeZone; this.before = before;
    try {
      if (!available(hass, this.entity) || !supports(hass, this.entity, 1)) {
        this.message = ''; this.error = 'Connection changed before this event was sent. Your draft is kept.'; return false;
      }
      // One attempt only. A transport failure can happen after persistence.
      await hass.callWS({type: 'calendar/event/create', entity_id: this.entity, event});
      this.outcome = 'accepted';
      const verified = await this.readback(hass);
      return verified;
    } catch {
      this.outcome = 'uncertain'; this.message = '';
      this.error = 'We could not confirm whether this event was saved. Check the calendar before trying again. Your draft is kept.';
      return false;
    } finally { this.busy = false; this.changed(); }
  }
  async readback(hass) {
    try {
      const matches = await readCalendar(hass, this.entity, this.event, this.timeZone);
      const beforeIds = new Set((this.before || []).map(item => item.uid).filter(Boolean));
      const newlyVisible = this.before !== null && (matches.length > this.before.length || matches.some(item => item.uid && !beforeIds.has(item.uid)));
      if (newlyVisible) {
        this.outcome = 'verified'; this.message = 'Event added and visible on the family calendar.'; this.error = ''; return true;
      }
    } catch { /* Read failure never triggers another create, including uncertain writes. */ }
    this.message = this.outcome === 'accepted'
      ? 'The calendar accepted this event, but its readback is not confirmed. Check the calendar before starting another.'
      : 'This event is still unconfirmed. Check the calendar before trying again. Your draft is kept.';
    return false;
  }
  async check(hass) {
    if (this.busy || !['accepted','uncertain'].includes(this.outcome)) return false;
    this.busy = true; this.changed();
    try { return await this.readback(hass); } finally { this.busy = false; this.changed(); }
  }
  allowEditing() { if (!this.busy && this.outcome === 'uncertain') { this.outcome = 'idle'; this.error = ''; this.changed(); } }
}
export function draftLines(text) {
  return String(text).split(/\r?\n/).map((text, index) => ({text: text.trim(), index})).filter(line => line.text);
}
export function remainingLines(text, successfulIndices) {
  return String(text).split(/\r?\n/).filter((_, index) => !successfulIndices.has(index)).join('\n');
}
export function parseTodoResponse(result, entity) {
  const rows = result?.response?.[entity]?.items;
  if (!Array.isArray(rows) || rows.some(item => typeof item?.uid !== 'string' || !item.uid ||
    typeof item.summary !== 'string' || !['needs_action', 'completed'].includes(item.status))) throw Error('The list did not return readable items.');
  return rows.map(item => ({...item}));
}
export class ShoppingSession {
  constructor(entities, onChange = () => {}) {
    this.entities = [...entities]; this.entities.forEach(entity => assertEntity(entity, 'todo'));
    this.entity = this.entities[0]; this.changed = onChange; this.draft = ''; this.items = [];
    this.busy = false; this.reading = false; this.lastRead = null; this.uncertain = false; this.refreshNeeded = false;
    this.message = ''; this.error = ''; this.generation = 0; this.pendingStatus = null;
  }
  select(entity) {
    if (!this.entities.includes(entity) || this.busy || this.reading || this.uncertain || this.refreshNeeded) return false;
    this.entity = entity; this.items = []; this.lastRead = null; this.generation++; this.error = ''; this.message = ''; this.changed(); return true;
  }
  service(hass, service, data, response = false) {
    return hass.callWS({type: 'call_service', domain: 'todo', service, target: {entity_id: this.entity}, service_data: data, return_response: response});
  }
  canWrite(hass, bit = 1) { return available(hass, this.entity) && supports(hass, this.entity, bit) && !this.busy && !this.reading && !this.uncertain && !this.refreshNeeded; }
  async read(hass) {
    const generation = this.generation, entity = this.entity;
    if (!available(hass, entity)) return false;
    this.reading = true; this.changed();
    try {
      const result = await this.service(hass, 'get_items', {status: ['needs_action', 'completed']}, true);
      const items = parseTodoResponse(result, entity);
      if (generation !== this.generation) return false;
      this.items = items; this.lastRead = Date.now(); this.error = '';
      if (this.pendingStatus) {
        const pending = this.pendingStatus;
        const confirmed = pending.entity === entity && items.some(item => item.uid === pending.uid && item.status === pending.status);
        if (confirmed) {
          this.pendingStatus = null; this.refreshNeeded = false; this.message = 'Item updated and confirmed in the list.';
        } else {
          this.refreshNeeded = true; this.message = 'Change accepted; this item’s status is not confirmed. Refresh the list to check again.';
        }
      } else this.refreshNeeded = false;
      return true;
    } catch {
      if (generation === this.generation) this.error = 'The list could not refresh. Previously read items may be out of date.';
      return false;
    } finally { if (generation === this.generation) { this.reading = false; this.changed(); } }
  }
  async refresh(hass) { if (this.busy || this.reading) return false; return this.read(hass); }
  async add(hass) {
    if (!this.canWrite(hass)) return false;
    const original = this.draft, lines = draftLines(original);
    if (!lines.length) return false;
    if (lines.length > 30 || lines.some(line => line.text.length > 250)) {
      this.error = 'Add up to 30 lines, with at most 250 characters on each line.'; this.changed(); return false;
    }
    this.busy = true; this.error = ''; this.message = 'Adding items…'; this.changed();
    const succeeded = new Set();
    try {
      for (const line of lines) {
        if (!available(hass, this.entity)) throw Error('Disconnected');
        await this.service(hass, 'add_item', {item: line.text});
        succeeded.add(line.index);
        this.draft = remainingLines(original, succeeded);
      }
      this.message = `${succeeded.size} ${succeeded.size === 1 ? 'item' : 'items'} accepted. Refreshing the list…`;
    } catch {
      this.uncertain = true;
      this.message = `${succeeded.size} ${succeeded.size === 1 ? 'addition' : 'additions'} accepted. Remaining lines are kept.`;
      this.error = 'The next addition could not be confirmed. Check the list before adding those lines again.';
    }
    this.refreshNeeded = true;
    const error = this.error;
    const refreshed = await this.read(hass);
    if (error) this.error = error;
    this.message = `${succeeded.size} ${succeeded.size === 1 ? 'addition' : 'additions'} accepted${refreshed ? '; list refreshed.' : '; refresh pending.'}${this.uncertain ? ' Check remaining lines before retrying.' : ''}`;
    if (!this.draft.trim()) this.draft = '';
    this.busy = false; this.changed(); return !this.uncertain;
  }
  async setStatus(hass, uid, status) {
    if (!this.canWrite(hass, 4) || !this.items.some(item => item.uid === uid) || !['needs_action', 'completed'].includes(status)) return false;
    this.busy = true; this.error = ''; this.message = 'Updating item…'; this.changed();
    try {
      await this.service(hass, 'update_item', {item: uid, status});
      this.pendingStatus = {entity: this.entity, uid, status}; this.refreshNeeded = true;
      this.message = 'Change accepted; this item’s status is not confirmed. Refresh the list to check again.';
      const refreshed = await this.read(hass);
      return refreshed && this.pendingStatus === null;
    } catch {
      this.uncertain = true; this.error = 'We could not confirm the change. Refresh and check the list before making another change.'; this.message = ''; return false;
    } finally { this.busy = false; this.changed(); }
  }
  acknowledge() { if (!this.busy && !this.reading) { this.uncertain = false; this.error = ''; this.changed(); } }
}
const STYLE = `
:host{display:block;color:var(--primary-text-color,#222);font-family:var(--paper-font-body1_-_font-family,inherit)}
ha-card{display:block;background:var(--ha-card-background,var(--card-background-color,#fff));border-radius:var(--ha-card-border-radius,12px);border:1px solid var(--ha-card-border-color,var(--divider-color,#ccc));overflow:hidden}
.content{padding:20px;display:grid;gap:16px;min-width:0}h2,h3,p{margin:0}h2{font-size:22px}h3{font-size:17px}p{line-height:1.5}.muted{color:var(--secondary-text-color,#666);font-size:14px}
form,.fields{display:grid;gap:16px;min-width:0}.row{display:flex;gap:12px;flex-wrap:wrap}.row>*{flex:1;min-width:0}.row label{min-width:125px}
label{display:grid;gap:7px;font-weight:600;font-size:15px;min-width:0}input,textarea,select,button{box-sizing:border-box;font:inherit;border-radius:9px;min-height:46px;max-width:100%}
input,textarea,select{width:100%;padding:11px;border:1px solid var(--divider-color,#999);background:var(--input-fill-color,var(--card-background-color,#fff));color:var(--primary-text-color,#222)}
textarea{min-height:88px;resize:vertical;line-height:1.5}button{padding:11px 16px;border:0;background:var(--primary-color,#087fa6);color:var(--text-primary-color,#fff);font-weight:600;cursor:pointer}
button.secondary{background:transparent;border:1px solid var(--divider-color,#999);color:var(--primary-color,#087fa6)}button:disabled,input:disabled,textarea:disabled,select:disabled{opacity:.6;cursor:default}
button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid var(--primary-color,#087fa6);outline-offset:3px}.check{display:flex;align-items:center;gap:10px;min-height:46px}.check input{width:24px;min-height:24px;height:24px;margin:10px}
.status{font-size:14px;overflow-wrap:anywhere}.error{color:var(--error-color,#b32121)}[hidden]{display:none!important}.list{display:grid;gap:3px}.item{display:flex;align-items:flex-start;gap:7px;min-width:0;border-bottom:1px solid var(--divider-color,#ccc);padding:8px 0}
.item label{display:flex;gap:7px;align-items:flex-start;flex:1;font-weight:400;min-height:46px}.item input{flex:none;width:24px;min-height:24px;height:24px;margin:11px 10px}.item-text{display:grid;gap:5px;min-width:0;padding-top:12px;padding-bottom:10px;overflow-wrap:anywhere;white-space:pre-wrap}.completed .summary{text-decoration:line-through;color:var(--secondary-text-color,#666)}
details>summary{min-height:46px;display:flex;align-items:center;cursor:pointer;font-weight:600}.note{font-size:14px;color:var(--secondary-text-color,#666)}
@media(max-width:420px){.content{padding:16px}.row{gap:10px}.row label{min-width:110px}}`;
const BaseElement = globalThis.HTMLElement || class {};
class HubCard extends BaseElement {
  constructor() { super(); this.attachShadow({mode: 'open'}); }
  getCardSize() { return 6; }
  getGridOptions() { return {columns: 12, rows: 'auto'}; }
  text(id, value) { const node = this.shadowRoot.getElementById(id); if (node.textContent !== value) node.textContent = value; return node; }
  entityName(entity) { return this._hass?.states?.[entity]?.attributes?.friendly_name || this.config?.title || 'Family'; }
  status(message, error) { this.text('status', message).hidden = !message; this.text('error', error).hidden = !error; }
}
export class EdsysFamilyEventCard extends HubCard {
  setConfig(config) {
    assertEntity(config.entity, 'calendar');
    if (this.config?.entity === config.entity && this.config.allow_recurrence === Boolean(config.allow_recurrence)) { this.config = {...config, allow_recurrence: Boolean(config.allow_recurrence)}; this.update(); return; }
    this.config = {...config, allow_recurrence: Boolean(config.allow_recurrence)};
    const session = new EventSession(config.entity, () => { if (this.session === session) this.update(); }); this.session = session;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="content">
      <div><h2 id="heading"></h2><p class="muted" id="destination"></p></div>
      <form><label>What’s happening?<input id="summary" required maxlength="200" autocomplete="off"></label>
      <label>Date<input id="date" type="date" required min="1970-01-01" max="2099-12-31"></label>
      <label class="check"><input id="allDay" type="checkbox" checked>All day</label>
      <div id="timed" class="row" hidden><label>Time<input id="time" type="time" value="09:00"></label><label>Minutes<input id="duration" type="number" min="1" max="1440" value="60"></label></div>
      <p id="zone" class="muted"></p>
      <div id="repeats" class="row" hidden><label>Repeat<select id="repeat"><option value="none">Does not repeat</option value="WEEKLY">Every week</option><option value="DAILY">Every day</option><option value="MONTHLY">Every month</option><option value="YEARLY">Every year</option></select></label><label id="count-label" hidden>Occurrences<input id="count" type="number" min="2" max="52" value="4"></label></div>
      <p id="repeat-note" class="muted" hidden>Repeats follow calendar dates. Dates missing from a month or year are skipped.</p>
      <label>Location <span class="muted">Optional</span><input id="location" maxlength="200" autocomplete="off"></label>
      <label>Notes <span class="muted">Optional</span><textarea id="description" maxlength="2000"></textarea></label>
      <button id="save" type="submit">Add to family calendar</button></form>
      <div class="row"><button id="check" type="button" class="secondary" hidden>Check calendar again</button><button id="another" type="button" class="secondary" hidden>Start another event</button><button id="reviewed" type="button" class="secondary" hidden>I checked the calendar — keep editing</button></div>
      <p id="status" class="status" role="status" aria-live="polite" hidden></p><p id="error" class="status error" role="alert" hidden></p>
    </div></ha-card>`;
    this.el = Object.fromEntries(['summary','date','allDay','time','duration','repeat','count','location','description','timed','save','check','another','reviewed'].map(id => [id, this.shadowRoot.getElementById(id)]));
    this.shadowRoot.querySelector('form').addEventListener('submit', event => { event.preventDefault(); this.save(); });
    this.el.allDay.addEventListener('change', () => this.update());
    this.el.repeat.addEventListener('change', () => this.update());
    this.el.check.addEventListener('click', async () => { const current = this.session; if (await current.check(this._hass) && this.session === current) this.clearDraft(); });
    this.el.another.addEventListener('click', () => { if (!this.session.busy) { this.clearDraft(); this.session.outcome = 'idle'; this.session.message = ''; this.update(); this.el.summary.focus(); } });
    this.el.reviewed.addEventListener('click', () => this.session.allowEditing());
    this.update();
  }
  set hass(hass) { this._hass = hass; this.update(); }
  draft() { return Object.fromEntries(['summary','date','time','duration','repeat','count','location','description'].map(key => [key, this.el[key].value]).concat([['allDay', this.el.allDay.checked]])); }
  clearDraft() { this.shadowRoot.querySelector('form').reset(); this.update(); }
  async save() { const current = this.session; if (await current.submit(this._hass, this.draft(), this.config.allow_recurrence) && this.session === current) this.clearDraft(); }
  update() {
    if (!this.el) return;
    const s = this.session, online = available(this._hass, this.config.entity), writable = online && supports(this._hass, this.config.entity, 1);
    this.text('heading', this.config.title || 'Add a family event'); this.text('destination', `Calendar: ${this.entityName(this.config.entity)}`);
    const durationNote = this.el.allDay.checked ? '' : this.el.repeat.value === 'none' ? ' Duration is elapsed time.' : ' Repeats keep the same Home clock times; elapsed duration may change at future clock changes.';
    this.text('zone', `Times use Home’s time zone${this._hass?.config?.time_zone ? ` (${this._hass.config.time_zone})` : ''}.${durationNote}`);
    this.el.timed.hidden = this.el.allDay.checked;
    this.shadowRoot.getElementById('repeats').hidden = !this.config.allow_recurrence;
    this.shadowRoot.getElementById('count-label').hidden = this.el.repeat.value === 'none';
    this.shadowRoot.getElementById('repeat-note').hidden = !this.config.allow_recurrence || this.el.repeat.value === 'none';
    for (const field of this.shadowRoot.querySelectorAll('form input, form textarea, form select')) field.disabled = s.busy || !writable || s.outcome !== 'idle';
    this.el.save.disabled = s.busy || !writable || s.outcome !== 'idle'; this.el.save.textContent = s.busy ? 'Working…' : 'Add to family calendar';
    this.el.save.setAttribute('aria-busy', String(s.busy));
    this.el.check.hidden = !['accepted','uncertain'].includes(s.outcome); this.el.check.disabled = s.busy || !online;
    this.el.another.hidden = !['accepted','verified'].includes(s.outcome); this.el.another.disabled = s.busy;
    this.el.reviewed.hidden = s.outcome !== 'uncertain'; this.el.reviewed.disabled = s.busy;
    this.status(s.message, s.error || (!writable ? 'Calendar is disconnected, unavailable, or does not allow adding events. Your draft stays here.' : ''));
  }
}
export class EdsysFamilyShoppingCard extends HubCard {
  setConfig(config) {
    const entities = config.entities || (config.entity ? [config.entity] : []);
    if (!Array.isArray(entities) || new Set(entities).size !== entities.length) throw Error('Configure a fixed list of to-do entities.');
    entities.forEach(entity => assertEntity(entity, 'todo'));
    if (this.config && JSON.stringify(this.config.entities) === JSON.stringify(entities)) { this.config = {...config, entities: [...entities]}; this.update(); return; }
    this.config = {...config, entities: [...entities]};
    const session = new ShoppingSession(entities, () => { if (this.session === session) this.update(); }); this.session = session;
    this.readKey = undefined;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="content"><div><h2 id="heading"></h2><p id="pending" class="muted" hidden>AnyList setup is pending. Your shared shopping list will appear here when it is connected.</p></div>
      <div id="list-content" class="fields"><label id="selector-label" hidden>Shopping list<select id="entity"></select></label>
      <form><label>Add items <span class="muted">One item per line; commas stay in the item.</span><textarea id="draft" aria-label="Items to add" maxlength="7500" placeholder="Milk&#10;Apples, green"></textarea></label><button id="add" type="submit">Add items</button></form>
      <div class="row"><button id="refresh" class="secondary" type="button">Refresh list</button><button id="reviewed" class="secondary" type="button" hidden>I checked the list — continue</button></div>
      <p id="read-time" class="muted"></p><p id="empty" class="muted"></p><div id="active" class="list" aria-label="Items to get"></div>
      <details id="completed-section"><summary id="completed-heading">Completed</summary><div id="completed" class="list completed"></div></details>
      </div><p id="status" class="status" role="status" aria-live="polite" hidden></p><p id="error" class="status error" role="alert" hidden></p></div></ha-card>`;
    this.el = Object.fromEntries(['entity','draft','add','refresh','reviewed','active','completed','empty'].map(id => [id, this.shadowRoot.getElementById(id)]));
    for (const entity of entities) { const option = document.createElement('option'); option.value = entity; this.el.entity.append(option); }
    this.el.entity.addEventListener('change', () => { if (this.session.select(this.el.entity.value)) { this.renderedItems = undefined; this.session.refresh(this._hass); } });
    this.el.draft.addEventListener('input', () => { this.session.draft = this.el.draft.value; this.update(); });
    this.shadowRoot.querySelector('form').addEventListener('submit', event => { event.preventDefault(); this.session.add(this._hass); });
    this.el.refresh.addEventListener('click', () => this.session.refresh(this._hass));
    this.el.reviewed.addEventListener('click', () => this.session.acknowledge());
    this.update(); this.maybeRead();
  }
  set hass(hass) { this._hass = hass; this.update(); this.maybeRead(); }
  connectedCallback() { this.maybeRead(); }
  maybeRead() {
    const s = this.session;
    if (!s?.entity || !available(this._hass, s.entity)) { this.readKey = undefined; return; }
    if (s.refreshNeeded) return; // Pending readback requires explicit Refresh; never replay a write.
    const state = this._hass.states[s.entity];
    const key = JSON.stringify([s.entity, state.state, state.last_updated]);
    if (key !== this.readKey && !s.busy && !s.reading) { this.readKey = key; s.refresh(this._hass); }
  }
  renderItems() {
    const s = this.session;
    if (this.renderedItems === s.items) return;
    this.renderedItems = s.items;
    this.el.active.replaceChildren(); this.el.completed.replaceChildren();
    for (const item of s.items) {
      const row = document.createElement('div'); row.className = 'item';
      const label = document.createElement('label'), checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = item.status === 'completed';
      checkbox.dataset.uid = item.uid;
      checkbox.addEventListener('change', () => { const desired = checkbox.checked ? 'completed' : 'needs_action'; checkbox.checked = item.status === 'completed'; s.setStatus(this._hass, item.uid, desired); });
      const text = document.createElement('span'); text.className = 'item-text';
      const summary = document.createElement('span'); summary.className = 'summary'; summary.textContent = item.summary; text.append(summary);
      const notes = [];
      if (typeof item.description === 'string' && item.description) notes.push(item.description);
      if (['string','number'].includes(typeof item.quantity)) notes.push(`Quantity: ${item.quantity}${typeof item.quantity_unit === 'string' ? ` ${item.quantity_unit}` : ''}`);
      if (notes.length) { const note = document.createElement('span'); note.className = 'note'; note.textContent = notes.join('\n'); text.append(note); }
      label.append(checkbox, text); row.append(label); (item.status === 'completed' ? this.el.completed : this.el.active).append(row);
    }
  }
  update() {
    if (!this.el) return;
    const s = this.session, configured = Boolean(s.entity), online = available(this._hass, s.entity);
    this.text('heading', this.config.title || 'Family shopping'); this.shadowRoot.getElementById('pending').hidden = configured;
    this.shadowRoot.getElementById('list-content').hidden = !configured;
    if (!configured) { this.status('', ''); return; }
    this.shadowRoot.getElementById('selector-label').hidden = this.config.entities.length < 2;
    for (const option of this.el.entity.options) option.textContent = this.entityName(option.value);
    this.el.entity.value = s.entity; this.el.entity.disabled = s.busy || s.reading || s.uncertain || s.refreshNeeded;
    if (this.el.draft.value !== s.draft) this.el.draft.value = s.draft;
    this.el.draft.disabled = s.busy || !online || s.uncertain || s.refreshNeeded;
    this.el.add.disabled = !s.canWrite(this._hass) || !draftLines(s.draft).length;
    this.el.add.textContent = s.busy ? 'Working…' : 'Add items'; this.el.add.setAttribute('aria-busy', String(s.busy));
    this.el.refresh.disabled = s.busy || s.reading || !online;
    this.el.refresh.textContent = s.reading ? 'Refreshing…' : 'Refresh list';
    this.el.reviewed.hidden = !s.uncertain; this.el.reviewed.disabled = s.busy || s.reading;
    this.text('read-time', s.lastRead ? `Last successful read: ${new Date(s.lastRead).toLocaleString()}` : 'Not read yet.');
    this.renderItems();
    for (const checkbox of this.shadowRoot.querySelectorAll('.item input')) checkbox.disabled = !s.canWrite(this._hass, 4);
    const active = s.items.filter(item => item.status === 'needs_action').length, completed = s.items.length - active;
    this.el.empty.textContent = !s.lastRead ? (s.reading ? 'Loading your list…' : 'Refresh to read your list.') : active ? '' : 'Nothing to get on the last successful read.';
    this.el.empty.hidden = !this.el.empty.textContent;
    this.text('completed-heading', `Completed (${completed})`);
    this.status(s.message, s.error || (!online ? 'Shopping list is disconnected or unavailable. Your draft stays here.' : ''));
  }
}
if (globalThis.customElements) {
  for (const [name, element] of [['edsys-family-event-card', EdsysFamilyEventCard], ['edsys-family-shopping-card', EdsysFamilyShoppingCard]]) {
    if (!customElements.get(name)) customElements.define(name, element);
  }
  window.customCards = window.customCards || [];
  for (const [type, name] of [['edsys-family-event-card','Family event'],['edsys-family-shopping-card','Family shopping']]) {
    if (!window.customCards.some(card => card.type === type)) window.customCards.push({type, name, description: 'Family Hub · existing Home Assistant data'});
  }
}
