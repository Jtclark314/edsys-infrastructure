// Runs on a dedicated origin, separate from HA. Receives no HA auth or service access.
(() => {
  const CHANNEL="edsys-google-map-v1", nonce=location.hash.slice(1);
  if(window.credentialless!==true || !document.referrer || parent===window)return;
  const origin=new URL(document.referrer).origin;
  if(origin===location.origin)return;
  const COLORS=["#1976d2","#c62828","#2e7d32","#7b1fa2","#ef6c00","#00838f","#455a64","#ad1457","#5d4037","#4527a0"];
  let started=false,map,traffic,points=[],history=[],initialFit=false;
  const locations=new Map(),paths=new Map();
  const post=type=>parent.postMessage({channel:CHANNEL,nonce,type},origin);
  function validPosition(p){return p && typeof p.lat==="number" && typeof p.lng==="number" && Number.isFinite(p.lat) && Number.isFinite(p.lng) && Math.abs(p.lat)<=90 && Math.abs(p.lng)<=180;}
  function fit(){const positions=points.filter(p=>validPosition(p.position));if(!positions.length)return;const bounds=new google.maps.LatLngBounds();positions.forEach(p=>bounds.extend(p.position));map.fitBounds(bounds,36);}
  function controls(data){if(!map)return;if(["roadmap","satellite","hybrid"].includes(data.map_type))map.setMapTypeId(data.map_type);traffic.setMap(data.traffic===true?map:null);}
  const samePosition=(a,b)=>a?.lat===b.lat && a?.lng===b.lng;
  function removeOverlays(item){if(item.marker)item.marker.map=null;item.circle?.setMap(null);item.marker=null;item.circle=null;item.position=null;}
  function draw(){
    if(!map)return;
    // HA sends updates for unrelated entities too. Keep attached overlays keyed
    // by the selected-location ref; recreating them on every update causes flicker.
    const list=document.querySelector("#locations"),seen=new Set();
    for(const [index,point] of points.entries()){
      seen.add(point.ref);
      let item=locations.get(point.ref);
      if(!item){item={li:document.createElement("li")};locations.set(point.ref,item);}
      const text=String(point.label||"Location").slice(0,80)+" — "+String(point.state||"unknown").slice(0,80)+(point.age_minutes===null?"":" · HA updated "+point.age_minutes+" min ago");
      if(item.li.textContent!==text)item.li.textContent=text;
      if(list.children[index]!==item.li)list.insertBefore(item.li,list.children[index]||null);
      if(!validPosition(point.position)){removeOverlays(item);continue;}
      const color=COLORS[point.ref%COLORS.length];
      const moved=!samePosition(item.position,point.position);
      if(!item.marker){
        item.badge=document.createElement("div");item.badge.style.cssText=`background:${color};color:white;border:2px solid white;border-radius:50%;width:24px;height:24px;display:grid;place-items:center;font:bold 13px system-ui;box-shadow:0 1px 4px #3336`;
        item.marker=new google.maps.marker.AdvancedMarkerElement({map,position:point.position,title:point.label});item.marker.append(item.badge);
      } else {
        if(moved)item.marker.position=point.position;
        if(item.marker.title!==point.label)item.marker.title=point.label;
      }
      const label=point.zone?"H":String(point.ref+1);
      if(item.badge.textContent!==label)item.badge.textContent=label;
      const radius=point.zone?point.radius:point.accuracy;
      if(Number.isFinite(radius) && radius>0 && radius<=100000){
        if(!item.circle)item.circle=new google.maps.Circle({map,center:point.position,radius,strokeColor:color,strokeOpacity:.6,strokeWeight:1,fillColor:color,fillOpacity:.08,clickable:false});
        else {if(moved)item.circle.setCenter(point.position);if(item.radius!==radius)item.circle.setRadius(radius);}
      } else {item.circle?.setMap(null);item.circle=null;}
      item.position={...point.position};item.radius=radius;
    }
    for(const [ref,item] of locations){if(!seen.has(ref)){removeOverlays(item);item.li.remove();locations.delete(ref);}}
    const seenPaths=new Set();
    for(const trail of history){for(const [index,segment] of (trail.segments||[]).entries()){
      const path=segment.filter(validPosition).slice(0,101);if(path.length<2)continue;
      const key=trail.ref+":"+index,signature=JSON.stringify(path);seenPaths.add(key);
      const item=paths.get(key);
      if(!item)paths.set(key,{line:new google.maps.Polyline({map,path,strokeColor:COLORS[trail.ref%COLORS.length],strokeOpacity:.65,strokeWeight:3,clickable:false}),signature});
      else if(item.signature!==signature){item.line.setPath(path);item.signature=signature;}
    }}
    for(const [key,item] of paths){if(!seenPaths.has(key)){item.line.setMap(null);paths.delete(key);}}
    if(!initialFit){fit();initialFit=true;}
  }
  window.gm_authFailure=()=>post("failed");
  window.edsysMapReady=()=>{
    try {
      const first=points.find(p=>validPosition(p.position));if(!first){post("failed");return;}
      map=new google.maps.Map(document.querySelector("#map"),{center:first.position,zoom:14,maxZoom:22,mapId:"DEMO_MAP_ID",mapTypeId:"roadmap",streetViewControl:false,mapTypeControl:false,fullscreenControl:true,clickableIcons:false,gestureHandling:"greedy",keyboardShortcuts:false,renderingType:google.maps.RenderingType.RASTER});
      traffic=new google.maps.TrafficLayer();controls(window.edsysInitialControls);draw();post("loaded");
    } catch {post("failed");}
  };
  window.addEventListener("message",event=>{
    const message=event.data;
    if(event.source!==parent || event.origin!==origin || message?.channel!==CHANNEL || message.nonce!==nonce)return;
    const data=message.payload;
    if(message.type==="init" && !started){
      if(!data || !/^AIza[A-Za-z0-9_-]{35}$/.test(data.api_key) || !Array.isArray(data.points) || data.points.length>10){post("failed");return;}
      started=true;points=data.points;window.edsysInitialControls={map_type:data.map_type,traffic:data.traffic===true};
      const url=new URL("https://maps.googleapis.com/maps/api/js");url.search=new URLSearchParams({key:data.api_key,v:"quarterly",libraries:"marker",loading:"async",callback:"edsysMapReady",auth_referrer_policy:"origin"}).toString();
      const script=document.createElement("script");script.src=url.href;script.async=true;script.referrerPolicy="origin";script.onerror=()=>post("failed");document.head.append(script);
    } else if(message.type==="data" && map && Array.isArray(data?.points) && data.points.length<=10 && Array.isArray(data.history) && data.history.length<=10){points=data.points;history=data.history;draw();}
    else if(message.type==="controls")controls(data||{});
    else if(message.type==="fit" && map)fit();
  });
  post("ready");
})();
