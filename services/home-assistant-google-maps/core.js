// Read-only projections: never give the Google frame a Home Assistant object.
export const MAX_ENTITIES = 10;
export const DAILY_BROWSER_LOADS = 20;
export const CHANNEL = "edsys-google-map-v1";
export const KEY_PATTERN = /^AIza[A-Za-z0-9_-]{35}$/;
const ENTITY = /^(person|device_tracker|zone)\.[a-z0-9_]+$/;

export function createNonce(cryptoSource=globalThis.crypto) {
  try {
    if(typeof cryptoSource?.getRandomValues!=="function")throw Error("unavailable");
    const bytes=new Uint8Array(16);cryptoSource.getRandomValues(bytes);
    return Array.from(bytes,byte=>byte.toString(16).padStart(2,"0")).join("");
  } catch {throw Error("Secure random generation is unavailable. Use the existing map or a current browser.");}
}

export function rendererURL(value, parentOrigin) {
  const url = new URL(value);
  const loopback = ["127.0.0.1","localhost"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(loopback && url.protocol === "http:")) || url.username || url.password || url.search || url.hash || !url.pathname.endsWith("/renderer.html") || url.origin === parentOrigin) throw Error("Use a dedicated HTTPS renderer origin, separate from Home Assistant");
  return url;
}

export function validateConfig(config) {
  if (!config || config.type !== "custom:edsys-google-maps-trial") throw Error("Invalid map card type");
  if (!Array.isArray(config.entities) || config.entities.length < 1 || config.entities.length > MAX_ENTITIES) throw Error("Select 1–10 location entities");
  const entities = config.entities.map(item => typeof item === "string" ? {entity: item} : item);
  if (entities.some(item => !item || !ENTITY.test(item.entity) || (item.name !== undefined && (typeof item.name !== "string" || item.name.length > 80)))) throw Error("Use explicit people, trackers or zones");
  if (new Set(entities.map(item => item.entity)).size !== entities.length) throw Error("Duplicate location entity");
  const key = (config.api_key || "").trim();
  if (key && !KEY_PATTERN.test(key)) throw Error("Check the Google browser key format");
  if (config.hours_to_show !== undefined && config.hours_to_show !== 2) throw Error("This trial is limited to two hours of history");
  const mapType = config.default_map_type === undefined ? "roadmap" : config.default_map_type;
  if (!["roadmap","satellite","hybrid"].includes(mapType)) throw Error("Choose Road, Satellite or Hybrid as the default map style");
  const fallback = config.fallback_path === undefined ? "/map" : config.fallback_path;
  if (!["/map","/map-fallback"].includes(fallback)) throw Error("Use /map or /map-fallback as the HA map fallback");
  const renderer = config.renderer_url ? rendererURL(config.renderer_url).href : "";
  return {type: config.type, title: String(config.title || "Google Maps Trial").slice(0,80), entities: entities.map(item => ({entity: item.entity, ...(item.name ? {name:item.name} : {})})), hours_to_show: 2, api_key: key, renderer_url: renderer, default_map_type: mapType, fallback_path: fallback};
}

export function coordinates(attributes) {
  const lat = attributes?.latitude, lng = attributes?.longitude;
  return typeof lat === "number" && typeof lng === "number" && Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? {lat,lng} : null;
}

export function projectStates(states, selected, now=Date.now()) {
  return selected.map((item,index) => {
    const state = states?.[item.entity];
    const attributes = state?.attributes || {};
    const available = state && !["unavailable","unknown"].includes(state.state);
    const position = available ? coordinates(attributes) : null;
    const updated = Date.parse(state?.last_updated || "");
    const age = Number.isFinite(updated) && updated <= now ? Math.floor((now-updated)/60000) : null;
    return {ref: index, label: String(item.name || attributes.friendly_name || `Location ${index+1}`).slice(0,80),
      state: String(state?.state || "unavailable").slice(0,80), position,
      zone: item.entity.startsWith("zone."),
      radius: item.entity.startsWith("zone.") && Number.isFinite(attributes.radius) ? Math.min(Math.max(attributes.radius,0),100000) : null,
      accuracy: !item.entity.startsWith("zone.") && Number.isFinite(attributes.gps_accuracy) ? Math.min(Math.max(attributes.gps_accuracy,0),100000) : null,
      age_minutes: age};
  });
}

export function historyCommand(selected, now=Date.now()) {
  return {type:"history/history_during_period", start_time:new Date(now-7200000).toISOString(), end_time:new Date(now).toISOString(),
    entity_ids:selected.map(item=>item.entity).filter(id=>!id.startsWith("zone.")),
    minimal_response:false,no_attributes:false,significant_changes_only:false};
}

export function projectHistory(result, selected, now=Date.now()) {
  return selected.map((item,ref) => {
    const rows=Array.isArray(result?.[item.entity]) ? result[item.entity].slice(0,20000) : [];
    const candidates=[];
    for(const row of rows) {
      const time=typeof row.lu === "number" ? row.lu*1000 : Date.parse(row.last_updated || row.last_changed || "");
      const position=coordinates(row.a || row.attributes);
      if(position && Number.isFinite(time) && time >= now-7200000 && time <= now && !["unknown","unavailable"].includes(row.s || row.state)) candidates.push({...position,time});
    }
    candidates.sort((a,b)=>a.time-b.time);
    const sampled=candidates.filter((_,index)=>index===candidates.length-1 || index % Math.max(1,Math.ceil(candidates.length/100)) === 0).slice(0,101);
    const segments=[];let segment=[];
    for(const point of sampled) {
      if(segment.length && point.time-segment.at(-1).time > 900000) { if(segment.length>1) segments.push(segment);segment=[]; }
      segment.push(point);
    }
    if(segment.length>1) segments.push(segment);
    return {ref,segments};
  });
}

export function reserveBrowserLoad(storage, now=Date.now()) {
  const day = new Date(now).toISOString().slice(0,10);
  const key="edsys-google-map-loads-v1";
  let entry;
  try {
    const raw=storage.getItem(key);entry=raw ? JSON.parse(raw) : {day,used:0};
    if(entry.day !== day) entry={day,used:0};
    if(!Number.isInteger(entry.used) || entry.used < 0 || entry.used >= DAILY_BROWSER_LOADS) throw Error("limit");
    const next={day,used:entry.used+1};storage.setItem(key,JSON.stringify(next));
    return next.used;
  } catch { throw Error("Browser load guard unavailable or daily limit reached. Use the existing map."); }
}
