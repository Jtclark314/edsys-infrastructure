import {CHANNEL,KEY_PATTERN,validateConfig,rendererURL,projectStates,projectHistory,historyCommand,createNonce,reserveBrowserLoad,DAILY_BROWSER_LOADS} from "./core.js";

class GoogleMapTrial extends HTMLElement {
  constructor() { super();this.attachShadow({mode:"open"});this._receive=this._receive.bind(this); }
  static getConfigElement() { return document.createElement("edsys-google-map-editor"); }
  static getStubConfig() { return {type:"custom:edsys-google-maps-trial",entities:[],hours_to_show:2,api_key:""}; }
  setConfig(value) {
    const config=validateConfig(value);
    if(JSON.stringify(config)===JSON.stringify(this._config))return;
    this.stop();this._config=config;this.render();
  }
  set hass(value) { this._hass=value;this.queueUpdate(); }
  getCardSize() { return 9; }
  getGridOptions() { return {columns:"full",rows:10,min_rows:6}; }
  connectedCallback() { window.addEventListener("message",this._receive);if(this._config && !this.shadowRoot.firstChild)this.render(); }
  disconnectedCallback() { window.removeEventListener("message",this._receive);this.stop(); }
  render() {
    if(!this._config)return;
    this.shadowRoot.innerHTML=`<style>
      ha-card{display:block;overflow:hidden}header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:16px 16px 8px}h2{font-size:20px;font-weight:500;margin:0;flex:1}
      button,select{font:inherit;border:1px solid var(--divider-color,#888);border-radius:8px;padding:8px 12px;color:var(--primary-text-color,#222);background:var(--card-background-color,#fff)}button{cursor:pointer}button:disabled{opacity:.5;cursor:default}
      .tools{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:0 16px 12px}.status{padding:0 16px 10px;color:var(--secondary-text-color,#555)}.stage{height:clamp(330px,62vh,650px);background:var(--secondary-background-color,#eee);display:grid;place-items:center}.stage p{max-width:480px;padding:20px;line-height:1.5}iframe{border:0;width:100%;height:100%}footer{padding:12px 16px;font-size:13px}a{color:var(--primary-color,#1976d2)}
    </style><ha-card><header><h2></h2><a href="/map">Existing map</a></header><div class="tools"><button class="load">Load Google map</button><button class="stop" disabled>Close map</button><select aria-label="Map style"><option value="roadmap">Road</option><option value="satellite">Satellite</option><option value="hybrid">Hybrid</option></select><label><input class="traffic" type="checkbox"> Traffic</label><button class="fit" disabled>Fit locations</button></div><div class="status" role="status"></div><div class="stage"><p>Google Maps loads on request. Location tracking continues in Home Assistant. Add the restricted browser key in this card’s settings to begin.</p></div><footer>Two-hour HA history · Street View and route/search features excluded · Google budget alerts notify; they do not cap spending.</footer></ha-card>`;
    this.shadowRoot.querySelector("h2").textContent=this._config.title;
    this.shadowRoot.querySelector(".load").onclick=()=>this.start();
    this.shadowRoot.querySelector(".stop").onclick=()=>this.stop();
    this.shadowRoot.querySelector("select").onchange=()=>this.send("controls",this.controls());
    this.shadowRoot.querySelector(".traffic").onchange=()=>this.send("controls",this.controls());
    this.shadowRoot.querySelector(".fit").onclick=()=>this.send("fit",{});
    this.status(this._config.api_key ? "Ready. Press Load Google map to start." : "Setup needed: add the restricted Google Maps browser key in card settings.");
  }
  status(text) { const node=this.shadowRoot.querySelector(".status");if(node)node.textContent=text; }
  controls() { return {map_type:this.shadowRoot.querySelector("select").value,traffic:this.shadowRoot.querySelector(".traffic").checked}; }
  start() {
    if(this._frame || !this._config?.api_key)return;
    let url;
    try {url=rendererURL(this._config.renderer_url,window.location.origin);}catch{this.status("Setup needed: configure a dedicated HTTPS renderer, separate from Home Assistant.");return;}
    if(!("credentialless" in HTMLIFrameElement.prototype)){this.status("This isolated trial needs a current Chromium browser (Chrome or Edge). Use the existing map in this browser.");return;}
    if(!this._hass?.connected){this.status("Home Assistant is disconnected. Use the existing map and try a fresh load when connected.");return;}
    const points=projectStates(this._hass.states,this._config.entities);
    if(!points.some(point=>point.position)){this.status("No selected location is available. Use the existing map.");return;}
    let nonce,count;
    try{nonce=createNonce();count=reserveBrowserLoad(localStorage);}catch(error){this.status(error.message);return;}
    this._nonce=nonce;this._loaded=false;this._history=[];
    const frame=document.createElement("iframe");frame.title="Google Maps trial";frame.credentialless=true;frame.setAttribute("sandbox","allow-scripts allow-same-origin allow-popups");frame.referrerPolicy="origin";
    this._rendererOrigin=url.origin;url.hash=this._nonce;frame.src=url.href;this._frame=frame;
    this.shadowRoot.querySelector(".stage").replaceChildren(frame);this.shadowRoot.querySelector(".load").disabled=true;this.shadowRoot.querySelector(".stop").disabled=false;
    this.status(`Loading map (${count}/${DAILY_BROWSER_LOADS} starts from this browser today).`);
    this._timeout=setTimeout(()=>{if(!this._loaded){this.status("Map loading timed out. Check the key, restrictions and Google billing. There is no automatic retry.");this.stop(false);}},25000);
  }
  stop(showStatus=true) {
    clearTimeout(this._timeout);clearTimeout(this._update);this._update=null;clearInterval(this._historyTimer);this._epoch=(this._epoch||0)+1;
    this._frame?.remove();this._frame=null;this._nonce=null;this._loaded=false;this._loadingHistory=false;this._history=[];
    const load=this.shadowRoot.querySelector(".load"),stop=this.shadowRoot.querySelector(".stop"),fit=this.shadowRoot.querySelector(".fit");
    if(load)load.disabled=false;if(stop)stop.disabled=true;if(fit)fit.disabled=true;
    const stage=this.shadowRoot.querySelector(".stage");
    if(stage){const p=document.createElement("p");p.textContent="Google Maps is closed. The existing HA map remains available.";stage.replaceChildren(p);}
    if(showStatus)this.status("Map closed. Press Load Google map for a fresh session.");
  }
  send(type,payload) { if(this._frame)this._frame.contentWindow?.postMessage({channel:CHANNEL,nonce:this._nonce,type,payload},this._rendererOrigin); }
  _receive(event) {
    const message=event.data;
    if(!this._frame || event.source!==this._frame.contentWindow || event.origin!==this._rendererOrigin || message?.channel!==CHANNEL || message.nonce!==this._nonce)return;
    if(message.type==="ready")this.send("init",{api_key:this._config.api_key,points:projectStates(this._hass.states,this._config.entities),...this.controls()});
    else if(message.type==="loaded") {
      if(this._loaded)return;
      this._loaded=true;clearTimeout(this._timeout);this.shadowRoot.querySelector(".fit").disabled=false;
      this.status("Google map loaded. Markers use HA positions; map imagery does not improve GPS accuracy.");
      this.queueUpdate();this.loadHistory();this._historyTimer=setInterval(()=>{if(!document.hidden)this.loadHistory();},300000);
    } else if(message.type==="failed") {this.status("Google Maps could not load. Check the key restrictions, enabled API and billing. Existing map remains available.");this.stop(false);}
  }
  queueUpdate() {
    if(!this._loaded || this._update)return;
    this._update=setTimeout(()=>{this._update=null;if(this._loaded)this.send("data",{points:projectStates(this._hass?.states,this._config.entities),history:this._history||[]});},250);
  }
  async loadHistory() {
    if(!this._loaded || this._loadingHistory || !this._hass?.connected)return;
    const command=historyCommand(this._config.entities);if(!command.entity_ids.length)return;
    this._loadingHistory=true;const epoch=this._epoch;
    try {
      const result=await this._hass.callWS(command);
      if(epoch!==this._epoch || !this._loaded)return;
      this._history=projectHistory(result,this._config.entities,Date.parse(command.end_time));this.queueUpdate();
    } catch { if(epoch===this._epoch && this._loaded)this.status("Current positions are shown; HA location history is unavailable."); }
    finally{if(epoch===this._epoch)this._loadingHistory=false;}
  }
}

class MapEditor extends HTMLElement {
  constructor(){super();this.attachShadow({mode:"open"});}
  setConfig(config) {
    this._config={...config};this.shadowRoot.innerHTML=`<style>label{display:block;margin:12px 0}input{display:block;width:95%;padding:10px;font:inherit}p{line-height:1.5}</style><label>Restricted Google Maps browser key<input type="password" autocomplete="off" spellcheck="false" aria-label="Restricted Google Maps browser key"></label><p>Use a dedicated key restricted to the renderer website below and Maps JavaScript API. Save the card after entering it. Never paste it into chat.</p><p>Selected HA location entities and two-hour history are retained. The isolated renderer requires Tailscale on this device. Google billing and quota settings are configured in Google Cloud.</p>`;
    const origin=document.createElement("p");origin.textContent="Website restriction (origin only): "+(config.renderer_url ? rendererURL(config.renderer_url).origin : "Renderer setup needed");this.shadowRoot.append(origin);
    const input=this.shadowRoot.querySelector("input");input.value=config.api_key||"";
    input.addEventListener("change",()=>{
      const value=input.value.trim();input.setCustomValidity(value && !KEY_PATTERN.test(value)?"Check the browser key format":"");if(!input.reportValidity())return;
      this._config={...this._config,api_key:value};this.dispatchEvent(new CustomEvent("config-changed",{detail:{config:this._config},bubbles:true,composed:true}));
    });
  }
  set hass(value){this._hass=value;}
}
if(!customElements.get("edsys-google-maps-trial"))customElements.define("edsys-google-maps-trial",GoogleMapTrial);
if(!customElements.get("edsys-google-map-editor"))customElements.define("edsys-google-map-editor",MapEditor);
window.customCards=window.customCards||[];window.customCards.push({type:"edsys-google-maps-trial",name:"EdSys Google Maps Trial",description:"Read-only on-demand Google map; restricted key required."});
