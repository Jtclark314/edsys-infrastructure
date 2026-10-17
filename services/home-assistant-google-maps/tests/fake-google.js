// Offline SDK fixture only; never deployed. No Google or other network calls.
window.__sdkStats={maps:0,markers:0,paths:0,options:null,markerItems:[],circleItems:[],pathItems:[],markerDetaches:0,layerDetaches:0,positionWrites:0,centerWrites:0,radiusWrites:0,pathWrites:0,fits:0};
class FakeMap {constructor(node,options){window.__sdkStats.maps++;window.__sdkStats.options=options;node.textContent='OFFLINE SDK FIXTURE — no Google map or charges';node.style.cssText='background:linear-gradient(145deg,#d9ead3,#d0e0e3);display:grid;place-items:center;padding:20px';}fitBounds(){window.__sdkStats.fits++;}setMapTypeId(type){window.__sdkStats.type=type;}}
class FakeMarker extends HTMLElement {
  constructor(options){super();window.__sdkStats.markers++;window.__sdkStats.markerItems.push(this);this.map=options.map;this.position=options.position;this.title=options.title;}
  set map(value){if(this._map && !value)window.__sdkStats.markerDetaches++;this._map=value;}get map(){return this._map;}
  set position(value){window.__sdkStats.positionWrites++;this._position=value;}get position(){return this._position;}
}
customElements.define('fake-map-marker',FakeMarker);
class Layer {constructor(options){this.map=options?.map;}setMap(map){if(this.map && !map)window.__sdkStats.layerDetaches++;this.map=map;}}
class Circle extends Layer {constructor(options){super(options);window.__sdkStats.circleItems.push(this);this.center=options.center;this.radius=options.radius;}setCenter(value){window.__sdkStats.centerWrites++;this.center=value;}setRadius(value){window.__sdkStats.radiusWrites++;this.radius=value;}}
class Polyline extends Layer {constructor(options){super(options);window.__sdkStats.paths++;window.__sdkStats.pathItems.push(this);this.path=options.path;}setPath(value){window.__sdkStats.pathWrites++;this.path=value;}}
class TrafficLayer extends Layer {setMap(map){super.setMap(map);window.__sdkStats.traffic=Boolean(map);}}
class Bounds {extend(){}}
window.google={maps:{Map:FakeMap,marker:{AdvancedMarkerElement:FakeMarker},Circle,Polyline,TrafficLayer,LatLngBounds:Bounds,RenderingType:{RASTER:'raster'}}};
queueMicrotask(()=>window.edsysMapReady());
