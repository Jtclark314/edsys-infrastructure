'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const artifactDir = process.env.FLEET_BENCHMARK_ARTIFACT_DIR;
  const privateUrl = process.env.FLEET_PRIVATE_CANARY;
  const publicUrl = process.env.FLEET_PUBLIC_CANARY || 'https://example.com';
  if (!artifactDir || !privateUrl) throw new Error('benchmark environment is incomplete');
  fs.mkdirSync(artifactDir, { recursive: true, mode: 0o700 });
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'edsys-fleet-browser-'));
  const upload = path.join(temp, 'upload-canary.txt');
  fs.writeFileSync(upload, 'EdSys Fleet browser upload canary\n', { mode: 0o600 });
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: '/usr/bin/google-chrome',
      headless: true,
      args: [
        '--use-angle=vulkan',
        '--enable-features=Vulkan,WebGPU,UnsafeWebGPU',
        '--ignore-gpu-blocklist',
        '--enable-zero-copy',
      ],
    });
    const context = await browser.newContext({ acceptDownloads: true });
    const page = await context.newPage();
    const privateResponse = await page.goto(privateUrl, { waitUntil: 'domcontentloaded' });
    const privateText = await page.textContent('body');
    if (!privateResponse || !privateResponse.ok() || !String(privateText).includes('ok')) {
      throw new Error('private canary did not return an observable healthy body');
    }
    const publicResponse = await page.goto(publicUrl, { waitUntil: 'domcontentloaded' });
    const heading = await page.textContent('h1');
    if (!publicResponse || !publicResponse.ok() || heading !== 'Example Domain') {
      throw new Error('public canary DOM check failed');
    }
    await page.setContent(`<!doctype html><input id="upload" type="file"><a id="download" download="fleet-download.txt" href="data:text/plain,EdSys%20Fleet%20download%20canary">download</a><canvas id="gpu"></canvas>`);
    await page.locator('#upload').setInputFiles(upload);
    const uploadedName = await page.locator('#upload').evaluate((node) => node.files[0].name);
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download').click();
    const download = await downloadPromise;
    const downloadPath = path.join(temp, 'download.txt');
    await download.saveAs(downloadPath);
    const downloadText = fs.readFileSync(downloadPath, 'utf8');
    const gpu = await page.evaluate(async () => {
      const canvas = document.querySelector('#gpu');
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      const debug = gl && gl.getExtension('WEBGL_debug_renderer_info');
      const renderer = gl && debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : '';
      let webgpu = false;
      let adapter = '';
      if (navigator.gpu) {
        const selected = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
        webgpu = Boolean(selected);
        adapter = selected && selected.info ? `${selected.info.vendor || ''} ${selected.info.architecture || ''}`.trim() : '';
      }
      return { webgpu, adapter, renderer: String(renderer || '') };
    });
    const screenshot = path.join(artifactDir, 'chrome-canary.webp');
    await page.screenshot({ path: screenshot, type: 'webp', quality: 82, fullPage: true });
    const physicalGpu = Boolean(gpu.renderer) && !/swiftshader|llvmpipe/i.test(gpu.renderer);
    const result = {
      status: gpu.webgpu && physicalGpu ? 'passed' : 'failed',
      private_canary: true,
      public_canary: true,
      heading,
      upload: uploadedName === 'upload-canary.txt',
      download: downloadText === 'EdSys Fleet download canary',
      webgpu: gpu.webgpu,
      adapter: gpu.adapter,
      renderer: gpu.renderer,
      physical_gpu: physicalGpu,
      screenshot,
    };
    process.stdout.write(`${JSON.stringify(result)}\n`);
    if (result.status !== 'passed') process.exitCode = 2;
  } finally {
    if (browser) await browser.close();
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch((error) => {
  process.stderr.write(`${error.name}: ${error.message}\n`);
  process.exitCode = 2;
});
