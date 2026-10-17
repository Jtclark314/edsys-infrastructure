(async (page) => {
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
  const context=await page.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID,random:typeof crypto.getRandomValues}));
  if(context.secure || context.uuid!=="undefined" || context.random!=="function")throw Error("Fixture did not reproduce LAN HTTP context");
  await page.evaluate(()=>{window.__HA_TEST_SECRET="synthetic-only";localStorage.removeItem("edsys-google-map-loads-v1");});
  await page.getByRole("button",{name:"Load Google map",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Google map loaded"}).waitFor();
  const frame=page.frames().find(frame=>frame.url().startsWith(renderer));
  const isolation=await frame.evaluate(()=>{let parentAccess;try{parentAccess=parent.__HA_TEST_SECRET;}catch{parentAccess="denied";}return {parentAccess,credentialless:window.credentialless,cookie:document.cookie};});
  if(isolation.parentAccess!=="denied" || isolation.credentialless!==true || isolation.cookie)throw Error("HTTP parent isolation regression");
  if(referrer!==renderer+"/" || !/#[0-9a-f]{32}$/.test(frame.url()))throw Error("Referrer or nonce regression");
  await frame.waitForFunction(()=>window.__sdkStats.paths>0);
  await page.getByRole("combobox",{name:"Map style"}).selectOption("hybrid");
  await page.getByRole("checkbox",{name:"Traffic",exact:true}).check();
  await frame.waitForFunction(()=>window.__sdkStats.type==="hybrid" && window.__sdkStats.traffic===true);
  if(sdkRequests!==1 || unexpectedRequests!==0 || (await frame.evaluate(()=>window.__sdkStats.maps))!==1)throw Error("Unexpected SDK request or Map reconstruction");
  if(await page.getByRole("link",{name:"Existing map",exact:true}).getAttribute("href")!=="/map")throw Error("Fallback changed");
  await page.getByRole("button",{name:"Close map",exact:true}).click();
  if(await page.locator("iframe").count())throw Error("Close left renderer running");
  return {insecure_parent_checks:"passed",secure_context:false,randomUUID:"unavailable",credentialless:true,parent_access:"denied",sdk_requests_mocked:sdkRequests,external_requests:0};
})
