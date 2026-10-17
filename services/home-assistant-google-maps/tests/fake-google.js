// Offline SDK fixture only; never deployed. No Google or other network calls.
window.__sdkStats={maps:0,markers:0,paths:0,options:null};
class FakeMap {constructor(node,options){window.__sdkStats.maps++;window.__sdkStats.options=options;node.textContent='OFFLINE SDK FIXTURE — no Google map or charges';node.style.cssText='background:linear-gradient(145deg,#d9ead3,#d0e0e3);display:grid;place-items:center;padding:20px';}fitBounds(){}setMapTypeId(type){window.__sdkStats.type=type;}}
class FakeMarker extends HTMLElement {constructor(options){super();window.__sdkStats.markers++;this.map=options.map;}}
customElements.define('fake-map-marker',FakeMarker);
class Layer {constructor(options){if(options?.path)window.__sdkStats.paths++;}setMap(map){window.__sdkStats.traffic=Boolean(map);}}
class Bounds {extend(){}}
window.google={maps:{Map:FakeMap,marker:{AdvancedMarkerElement:FakeMarker},Circle:Layer,Polyline:Layer,TrafficLayer:Layer,LatLngBounds:Bounds,RenderingType:{RASTER:'raster'}}};
queueMicrotask(()=>window.edsysMapReady());
