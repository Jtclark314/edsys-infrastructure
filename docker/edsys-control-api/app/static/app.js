const state = {
  services: [],
  devices: [],
  live: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function getJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json();
}

function badge(text, type = "neutral") {
  return `<span class="badge ${type}">${escapeHtml(text || "unspecified")}</span>`;
}

function healthBadge(service) {
  const result = state.live?.results?.find((item) => item.slug === service.slug);
  if (!result) return badge("not checked");
  if (result.status === "up" || result.status === "reachable_auth_required") return badge(result.status, "good");
  if (result.status === "down") return badge("down", "bad");
  return badge(result.status, "neutral");
}

function serviceMatches(service, query) {
  if (!query) return true;
  const text = [
    service.name,
    service.host,
    service.ip,
    service.category,
    service.status,
    service.criticality,
    service.url,
    service.container_name,
    service.image,
    service.notes,
  ]
    .join(" ")
    .toLowerCase();
  return text.includes(query.toLowerCase());
}

function deviceMatches(device, query) {
  if (!query) return true;
  const text = [
    device.hostname,
    device.aliases?.join(" "),
    device.ip,
    device.mac,
    device.category,
    device.status,
    device.os,
    device.role,
    device.notes,
  ]
    .join(" ")
    .toLowerCase();
  return text.includes(query.toLowerCase());
}

function renderServices() {
  const query = document.querySelector("#serviceFilter").value.trim();
  const rows = state.services
    .filter((service) => serviceMatches(service, query))
    .map((service) => {
      const link = service.url?.startsWith("http")
        ? `<a href="${escapeHtml(service.url)}" target="_blank" rel="noreferrer">Open</a>`
        : `<span class="muted">${escapeHtml(service.url || "")}</span>`;
      return `
        <tr>
          <td><strong>${escapeHtml(service.name)}</strong><br><span class="muted">${escapeHtml(service.ip || "")}${service.port ? `:${escapeHtml(service.port)}` : ""}</span></td>
          <td>${escapeHtml(service.host || "")}</td>
          <td>${escapeHtml(service.category || "")}</td>
          <td>${badge(service.criticality || "unspecified", ["critical", "high"].includes(String(service.criticality).toLowerCase()) ? "warn" : "neutral")}</td>
          <td>${healthBadge(service)}<br><span class="muted">${escapeHtml(service.status || "")}</span></td>
          <td>${link}</td>
        </tr>`;
    })
    .join("");
  document.querySelector("#servicesTable").innerHTML = rows || `<tr><td colspan="6" class="muted">No matching services.</td></tr>`;
}

function renderDevices() {
  const query = document.querySelector("#deviceFilter").value.trim();
  const rows = state.devices
    .filter((device) => deviceMatches(device, query))
    .map((device) => {
      const firstUrl = (device.management_urls || []).find((url) => String(url).startsWith("http"));
      const link = firstUrl
        ? `<a href="${escapeHtml(firstUrl)}" target="_blank" rel="noreferrer">Open</a>`
        : `<span class="muted">${escapeHtml((device.management_urls || [])[0] || "")}</span>`;
      return `
        <tr>
          <td><strong>${escapeHtml(device.hostname)}</strong><br><span class="muted">${escapeHtml((device.aliases || []).join(", "))}</span></td>
          <td>${escapeHtml(device.ip || "")}</td>
          <td>${escapeHtml(device.category || "")}</td>
          <td>${escapeHtml(device.os || "")}</td>
          <td>${badge(device.status || "unknown", String(device.status || "").includes("verified") ? "good" : "neutral")}</td>
          <td>${link}</td>
        </tr>`;
    })
    .join("");
  document.querySelector("#devicesTable").innerHTML = rows || `<tr><td colspan="6" class="muted">No matching devices.</td></tr>`;
}

function renderSummary(summary) {
  document.querySelector("#totalServices").textContent = summary.total_services;
  document.querySelector("#totalDevices").textContent = summary.total_devices;
  document.querySelector("#criticalServices").textContent = summary.critical_services.length;
  document.querySelector("#backupRequired").textContent = summary.services_requiring_backup.count;
}

function renderHealth(live) {
  document.querySelector("#downServices").textContent = live.down_count;
  document.querySelector("#apiStatus").outerHTML = live.down_count > 0
    ? `<span id="apiStatus" class="badge warn">Some checks down</span>`
    : `<span id="apiStatus" class="badge good">Checks clean</span>`;
  document.querySelector("#healthSubtext").textContent =
    `${live.checked_count} checked, ${live.up_count} reachable, ${live.down_count} down, ${live.skipped_count} skipped.`;

  const criticalDown = live.results.filter((item) => item.status === "down" && ["critical", "high"].includes(String(item.criticality || "").toLowerCase()));
  const box = document.querySelector("#criticalDown");
  if (criticalDown.length) {
    box.classList.remove("hidden");
    box.innerHTML = `<strong>Critical or high services down:</strong> ${criticalDown.map((item) => escapeHtml(item.name)).join(", ")}`;
  } else {
    box.classList.add("hidden");
    box.innerHTML = "";
  }
}

async function refresh() {
  const refreshBtn = document.querySelector("#refreshBtn");
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Refreshing";
  try {
    const [summary, services, devices, live] = await Promise.all([
      getJson("/api/summary"),
      getJson("/api/services"),
      getJson("/api/devices"),
      getJson("/api/health/live"),
    ]);
    state.services = services;
    state.devices = devices;
    state.live = live;
    renderSummary(summary);
    renderHealth(live);
    renderServices();
    renderDevices();
  } catch (error) {
    document.querySelector("#apiStatus").outerHTML = `<span id="apiStatus" class="badge bad">Load failed</span>`;
    document.querySelector("#healthSubtext").textContent = error.message;
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh";
  }
}

document.querySelector("#refreshBtn").addEventListener("click", refresh);
document.querySelector("#serviceFilter").addEventListener("input", renderServices);
document.querySelector("#deviceFilter").addEventListener("input", renderDevices);
refresh();
