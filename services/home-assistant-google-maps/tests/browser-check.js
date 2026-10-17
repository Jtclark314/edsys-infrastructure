(async (page) => {
  let sdkRequests=0,unexpectedRequests=0,referrer;
  await page.unroute("**/*");
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
      return route.fulfill({contentType:"text/javascript",path:"/home/jeremy/code/worktrees/edsys-infrastructure-google-maps-trial/services/home-assistant-google-maps/tests/fake-google.js"});
    }
    unexpectedRequests++;return route.abort();
  });
  await page.goto("http://127.0.0.1:8934/tests/fixture.html");
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  if(sdkRequests!==0 || await page.locator("iframe").count())throw Error("Google/frame loaded before key setup");
  await page.evaluate(()=>{window.__HA_TEST_SECRET="synthetic-parent-only";const {card,config}=window.__trial;card.setConfig({...config,api_key:"AI"+"za"+"A".repeat(35)});});
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Google map loaded"}).waitFor();
  let frame=page.frames().find(frame=>frame.url().includes("renderer.html"));
  const isolation=await frame.evaluate(()=>{try{return parent.__HA_TEST_SECRET;}catch{return "denied";}});
  if(isolation!=="denied")throw Error("Sandbox could read parent HA realm");
  if(!(await frame.evaluate(()=>window.credentialless===true && !document.cookie.includes("fixture_cookie") && localStorage.getItem("fixture_storage")===null)))throw Error("Renderer inherited regular browser storage");
  if(referrer!=="http://localhost:8934/")throw Error("Renderer origin referrer not retained in SDK request");
  await frame.waitForFunction(()=>window.__sdkStats.paths>0);
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
  await frame.waitForFunction(()=>window.__sdkStats.markers>=4);
  await page.evaluate(()=>{window.__trial.card.hass=window.__trial.hass;});
  await frame.waitForFunction(()=>window.__sdkStats.markers>=6);
  if(sdkRequests!==2 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Reopen/queued update regression");
  if(await page.getByRole("link",{name:"Existing map",exact:true}).getAttribute("href")!=="/map")throw Error("Fallback link changed");
  await page.setViewportSize({width:1280,height:900});await page.screenshot({path:"output/playwright/desktop-offline.png"});
  await page.setViewportSize({width:390,height:844});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error("Phone layout overflows horizontally");
  await page.screenshot({path:"output/playwright/phone-offline.png"});
  if(unexpectedRequests!==0)throw Error("Unexpected external service requested");
  await page.getByRole("button",{name:"Close map",exact:true}).click();
  if(await page.locator("iframe").count())throw Error("Google frame persisted after Close");
  return {offline_browser_checks:"passed",sdk_requests_mocked:sdkRequests,external_requests:0,parent_access:"denied",sdk_referrer:"origin-only",google_live_acceptance:"pending actual restricted key"};
})
