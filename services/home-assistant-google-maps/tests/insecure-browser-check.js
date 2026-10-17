(async (page) => {
  async function checkPanelLayout(width,height) {
    await page.setViewportSize({width,height});
    await page.evaluate(()=>{window.scrollTo(0,0);window.dispatchEvent(new Event("resize"));});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const layout=await page.evaluate(()=>{
      const root=document.querySelector("edsys-google-maps-trial").shadowRoot;
      const rect=selector=>{const r=root.querySelector(selector).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height,width:r.width};};
      return {card:rect("ha-card"),stage:rect(".stage"),tools:rect(".tools"),footer:rect("footer"),fallback:rect("a"),overflow:document.documentElement.scrollWidth>innerWidth};
    });
    if(layout.overflow || layout.stage.height<239 || layout.card.top<95 || layout.footer.bottom>layout.card.bottom+1)throw Error("Panel clips or overflows: "+JSON.stringify({width,height,layout}));
    for(const item of [layout.tools,layout.footer,layout.fallback])if(item.width<=0 || item.height<=0 || item.left<0 || item.right>width+1)throw Error("Panel control is hidden or overflows");
    if(height>=800){
      if(layout.card.bottom<height-16 || layout.card.bottom>height+1 || layout.tools.top<56 || layout.footer.bottom>height+1 || layout.fallback.top<56)throw Error("Panel does not fill available viewport with visible controls: "+JSON.stringify({width,height,layout}));
    } else {
      await page.evaluate(()=>document.querySelector("edsys-google-maps-trial").shadowRoot.querySelector("footer").scrollIntoView({block:"end"}));
      const footer=await page.evaluate(()=>{const r=document.querySelector("edsys-google-maps-trial").shadowRoot.querySelector("footer").getBoundingClientRect();return {top:r.top,bottom:r.bottom};});
      if(footer.top<56 || footer.bottom>height+1)throw Error("Short-screen footer is not reachable by scrolling");
      await page.evaluate(()=>window.scrollTo(0,0));
      if(layout.tools.bottom>height || layout.fallback.bottom>height)throw Error("Short-screen toolbar or fallback is not reachable at the top");
    }
  }
  const panelStyle="body{box-sizing:border-box;margin:0;padding:96px 12px 0}body::before{content:'Synthetic HA header';position:fixed;z-index:20;top:0;left:0;right:0;height:56px;background:#ddd}edsys-google-maps-trial{max-width:none;margin:0}";
  // Synthetic non-loopback HTTP is essential: localhost is a secure context.
  const source="http://127.0.0.1:8934", parent="http://ha.maps.test", renderer="https://renderer.maps.test";
  const fixture=(await (await page.request.get(source+"/tests/fixture.html")).text()).replace("http://localhost:8934/renderer.html",renderer+"/renderer.html");
  const sdk=await (await page.request.get(source+"/tests/fake-google.js")).text();
  let sdkRequests=0,unexpectedRequests=0,referrer;
  await page.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin===parent){
      if(url.pathname==="/tests/fixture.html")return route.fulfill({contentType:"text/html",body:fixture});
      return route.fulfill({response:await page.request.get(source+url.pathname)});
    }
    if(url.origin===renderer){
      if((await request.allHeaders()).cookie)throw Error("Renderer received regular cookies");
      return route.fulfill({response:await page.request.get(source+"/renderer.html")});
    }
    if(url.hostname==="maps.googleapis.com" && url.pathname==="/maps/api/js"){
      sdkRequests++;referrer=(await request.allHeaders()).referer;
      return route.fulfill({contentType:"text/javascript",body:sdk});
    }
    unexpectedRequests++;return route.abort();
  });
  await page.context().addCookies([{name:"fixture_cookie",value:"synthetic-only",url:renderer}]);
  await page.goto(parent+"/tests/fixture.html?key");
  await page.addStyleTag({content:panelStyle});
  const context=await page.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID,random:typeof crypto.getRandomValues}));
  if(context.secure || context.uuid!=="undefined" || context.random!=="function")throw Error("Fixture did not reproduce LAN HTTP context");
  await page.evaluate(()=>{window.__HA_TEST_SECRET="synthetic-only";localStorage.removeItem("edsys-google-map-loads-v1");const {card,config}=window.__trial;window.__trial.config={...config,default_map_type:"hybrid",fallback_path:"/map-fallback"};card.setConfig(window.__trial.config);});
  if(await page.getByRole("combobox",{name:"Map style"}).inputValue()!=="hybrid")throw Error("Saved Hybrid default was not rendered");
  if(sdkRequests!==0 || await page.locator("iframe").count())throw Error("Saving defaults automatically loaded Google");
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Google map loaded"}).waitFor();
  let frame=page.frames().find(frame=>frame.url().startsWith(renderer));
  const isolation=await frame.evaluate(()=>{let parentAccess;try{parentAccess=parent.__HA_TEST_SECRET;}catch{parentAccess="denied";}return {parentAccess,credentialless:window.credentialless,cookie:document.cookie};});
  if(isolation.parentAccess!=="denied" || isolation.credentialless!==true || isolation.cookie)throw Error("HTTP parent isolation regression");
  if(referrer!==renderer+"/" || !/#[0-9a-f]{32}$/.test(frame.url()))throw Error("Referrer or nonce regression");
  await frame.waitForFunction(()=>window.__sdkStats.paths>0);
  await frame.waitForFunction(()=>window.__sdkStats.type==="hybrid");
  await page.getByRole("combobox",{name:"Map style"}).selectOption("satellite");
  await page.getByRole("checkbox",{name:"Traffic",exact:true}).check();
  await frame.waitForFunction(()=>window.__sdkStats.type==="satellite" && window.__sdkStats.traffic===true);
  if(sdkRequests!==1 || unexpectedRequests!==0 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Unexpected SDK request or Map reconstruction");
  if(await page.getByRole("link",{name:"HA Map (Fallback)",exact:true}).getAttribute("href")!=="/map-fallback")throw Error("Configured fallback changed");
  await page.getByRole("button",{name:"Close map",exact:true}).click();
  if(await page.locator("iframe").count())throw Error("Close left renderer running");
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Google map loaded"}).waitFor();
  frame=page.frames().find(frame=>frame.url().startsWith(renderer));
  await frame.waitForFunction(()=>window.__sdkStats.type==="satellite" && window.__sdkStats.traffic===true);
  if(await page.getByRole("combobox",{name:"Map style"}).inputValue()!=="satellite")throw Error("Close/reopen reset the selected style");
  if(sdkRequests!==2 || unexpectedRequests!==0 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Reopen reconstructed or unexpectedly loaded the map");
  for(const [width,height] of [[1920,1200],[1280,900],[390,844],[844,390]])await checkPanelLayout(width,height);
  if(sdkRequests!==2 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Resizing reloaded the SDK or reconstructed the Map");
  await page.getByRole("button",{name:"Close map",exact:true}).click();
  if(await page.locator("iframe").count())throw Error("Close left reopened renderer running");
  return {insecure_parent_checks:"passed",secure_context:false,randomUUID:"unavailable",credentialless:true,parent_access:"denied",sdk_requests_mocked:sdkRequests,external_requests:0};
})
