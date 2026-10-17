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
  let sdkRequests=0,unexpectedRequests=0,referrer;
  await page.unroute("**/*");
  const fakeGoogle=await (await page.request.get("http://127.0.0.1:8934/tests/fake-google.js")).text();
  await page.context().addCookies([{name:"fixture_cookie",value:"synthetic-parent-only",domain:"localhost",path:"/"}]);
  await page.goto("http://localhost:8934/tests/fixture.html");
  await page.evaluate(()=>localStorage.setItem("fixture_storage","synthetic-parent-only"));
  await page.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    if(["127.0.0.1","localhost"].includes(url.hostname)){
      if(url.hostname==="localhost" && url.pathname.endsWith("renderer.html") && (await request.allHeaders()).cookie)throw Error("Renderer received regular browser cookies");
      return route.continue();
    }
    if(url.hostname==="maps.googleapis.com" && url.pathname==="/maps/api/js"){
      sdkRequests++;referrer=(await request.allHeaders()).referer;
      if(url.searchParams.get("libraries")!=="marker" || url.searchParams.get("auth_referrer_policy")!=="origin")throw Error("SDK scope changed");
      return route.fulfill({contentType:"text/javascript",body:fakeGoogle});
    }
    unexpectedRequests++;return route.abort();
  });
  await page.goto("http://127.0.0.1:8934/tests/fixture.html");
  await page.addStyleTag({content:panelStyle});
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  if(sdkRequests!==0 || await page.locator("iframe").count())throw Error("Google/frame loaded before key setup");
  await page.evaluate(()=>{window.__HA_TEST_SECRET="synthetic-parent-only";const {card,config}=window.__trial;window.__trial.config={...config,api_key:"AI"+"za"+"A".repeat(35),default_map_type:"hybrid",fallback_path:"/map-fallback"};card.setConfig(window.__trial.config);});
  if(await page.getByRole("combobox",{name:"Map style"}).inputValue()!=="hybrid")throw Error("Saved Hybrid default was not rendered");
  if(sdkRequests!==0 || await page.locator("iframe").count())throw Error("Saving defaults automatically loaded Google");
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Google map loaded"}).waitFor();
  let frame=page.frames().find(frame=>frame.url().includes("renderer.html"));
  const isolation=await frame.evaluate(()=>{try{return parent.__HA_TEST_SECRET;}catch{return "denied";}});
  if(isolation!=="denied")throw Error("Sandbox could read parent HA realm");
  if(!(await frame.evaluate(()=>window.credentialless===true && !document.cookie.includes("fixture_cookie") && localStorage.getItem("fixture_storage")===null)))throw Error("Renderer inherited regular browser storage");
  if(referrer!=="http://localhost:8934/")throw Error("Renderer origin referrer not retained in SDK request");
  await frame.waitForFunction(()=>window.__sdkStats.paths>0);
  await frame.waitForFunction(()=>window.__sdkStats.type==="hybrid");
  await page.getByRole("combobox",{name:"Map style"}).selectOption("satellite");
  await page.getByRole("checkbox",{name:"Traffic",exact:true}).check();
  await frame.waitForFunction(()=>window.__sdkStats.type==="satellite" && window.__sdkStats.traffic===true);
  await page.evaluate(()=>{window.__trial.hass.states["person.synthetic"].attributes.latitude=1.1;window.__trial.card.hass=window.__trial.hass;});
  await frame.waitForFunction(()=>window.__sdkStats.markers>=6);
  if((await frame.evaluate(()=>window.__sdkStats.maps))!==1 || sdkRequests!==1)throw Error("Updates rebuilt/reloaded the map");
  await page.evaluate(()=>{window.__trial.card.hass=window.__trial.hass;window.__trial.card.stop();});
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Google map loaded"}).waitFor();
  frame=page.frames().find(frame=>frame.url().includes("renderer.html"));
  await frame.waitForFunction(()=>window.__sdkStats.type==="satellite" && window.__sdkStats.traffic===true);
  if(await page.getByRole("combobox",{name:"Map style"}).inputValue()!=="satellite")throw Error("Close/reopen reset the selected style");
  await frame.waitForFunction(()=>window.__sdkStats.markers>=4);
  await page.evaluate(()=>{window.__trial.card.hass=window.__trial.hass;});
  await frame.waitForFunction(()=>window.__sdkStats.markers>=6);
  if(sdkRequests!==2 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Reopen/queued update regression");
  if(await page.getByRole("link",{name:"HA Map (Fallback)",exact:true}).getAttribute("href")!=="/map-fallback")throw Error("Configured fallback link changed");
  for(const [width,height] of [[1920,1200],[1280,900],[390,844],[844,390]]) {
    await checkPanelLayout(width,height);
    if(width===1280)await page.screenshot({path:"output/playwright/desktop-offline.png"});
    if(width===390)await page.screenshot({path:"output/playwright/phone-offline.png"});
  }
  if(sdkRequests!==2 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Resizing reloaded the SDK or reconstructed the Map");
  if(unexpectedRequests!==0)throw Error("Unexpected external service requested");
  await page.getByRole("button",{name:"Close map",exact:true}).click();
  if(await page.locator("iframe").count())throw Error("Google frame persisted after Close");
  return {offline_browser_checks:"passed",sdk_requests_mocked:sdkRequests,external_requests:0,parent_access:"denied",sdk_referrer:"origin-only",google_live_acceptance:"pending actual restricted key"};
})
