/* Public rendering and pure calculations. CommonJS exports support offline tests. */
"use strict";
const DAY = 86400000;
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}
function parseTs(ts) {
  return new Date(String(ts).replace(" IRST", "+03:30").replace(" UTC+03:30", "+03:30")
    .replace(" UTC", "Z").replace(" ", "T"));
}
function fmtLocal(value) {
  const date = parseTs(value);
  return Number.isFinite(+date) ? new Date(+date + 210 * 60000).toISOString().slice(0, 19)
    .replace("T", " ") + " UTC+03:30" : "unknown";
}
function age(ms) { return `${Math.max(0, Math.floor(ms / 60000))} min`; }
function freshness(lastRun, settings, now = Date.now()) {
  const elapsed = now - +parseTs(lastRun);
  if (!lastRun || !settings || !Number.isFinite(elapsed) || elapsed < 0 ||
      !Number.isFinite(settings.delayed_after_seconds) || !Number.isFinite(settings.stale_after_seconds) ||
      !(settings.delayed_after_seconds > 0 && settings.stale_after_seconds > settings.delayed_after_seconds)) return "unknown";
  if (elapsed > settings.stale_after_seconds * 1000) return "stale";
  if (elapsed > settings.delayed_after_seconds * 1000) return "delayed";
  return "fresh";
}
function confirmedUp(result) {
  // Never infer a confirmed outage from legacy raw observations.
  return typeof result?.confirmed_up === "boolean" ? result.confirmed_up : null;
}
function availability(history, name, windowMs, settings, now = Date.now()) {
  const interval = settings.expected_interval_seconds * 1000;
  const expected = Math.ceil(windowMs / interval), cutoff = now - windowMs;
  const slots = new Map();
  for (const sample of history) {
    const time = +parseTs(sample.ts), result = sample.results[name];
    if (!result || time <= cutoff || time > now || sample.boundary_anchor) continue;
    const slot = Math.min(expected - 1, Math.ceil((time - cutoff) / interval) - 1);
    // Extra manual checks cannot conceal missing scheduled observations.
    slots.set(slot, (slots.get(slot) ?? true) && result.ok === true);
  }
  const observed = slots.size, successful = [...slots.values()].filter(Boolean).length;
  const coverage = observed / expected;
  return {expected, observed, successful, coverage, missing: expected - observed,
    percent: observed && coverage >= settings.minimum_coverage ? successful / observed * 100 : null};
}
function incidentsFor(history, name) {
  const incidents = [];
  let active = null;
  for (const sample of history) {
    const result = sample.results[name];
    if (!result) continue;
    const up = confirmedUp(result), observed = +parseTs(sample.ts);
    if (up === false) {
      if (!active) active = {name, start: result.incident_started_utc ? +parseTs(result.incident_started_utc) : observed,
        end: observed, ongoing: true, boundary: !!sample.boundary_anchor || !result.incident_started_utc};
      active.end = observed;
    } else if (up === true && active) {
      active.end = observed; // duration ends at recovery, not at the last failed check
      active.ongoing = false;
      incidents.push(active);
      active = null;
    }
  }
  if (active) incidents.push(active);
  return incidents;
}
function gapsFor(history, name, settings, now = Date.now(), windowMs = DAY) {
  const cutoff = now - windowMs, interval = settings.expected_interval_seconds * 1000;
  const observations = history.filter(s => s.results[name] && +parseTs(s.ts) > cutoff && +parseTs(s.ts) <= now)
    .map(s => +parseTs(s.ts)).sort((a, b) => a - b);
  const points = [cutoff, ...observations, now], gaps = [];
  for (let i = 1; i < points.length; i++) {
    if (points[i] - points[i - 1] > interval * 2) gaps.push({start: points[i - 1], end: points[i]});
  }
  return gaps;
}
function availabilityHtml(value, label) {
  const result = value.percent === null ? "Insufficient data" : `${value.percent.toFixed(2)}% observed availability`;
  return `<span>${esc(label)} <b>${result}</b><br>${(value.coverage * 100).toFixed(1)}% coverage ` +
    `(${value.observed}/${value.expected} slots; ${value.missing} missing)</span>`;
}
// Public labels describe what is checked, without implying authenticated workflows are tested.
const SERVICE_LABELS = {
  "Landing (mahansco.ir)": {label: "Website", description: "mahansco.ir", size: "primary", order: 0},
  "App SPA (app.mahansco.ir)": {label: "Web app", description: "app.mahansco.ir", size: "primary", order: 1},
  "App health API": {label: "App API", description: "Application health", order: 2},
  "SSO discovery": {label: "Sign-in service", description: "Public sign-in configuration", order: 3},
  "GraphQL endpoint": {label: "GraphQL", description: "API access check", size: "supporting", order: 4},
  "Marketing API": {label: "Website API", description: "Website content", size: "supporting", order: 5},
  "robots.txt canary": {label: "Site access", description: "Search engine access file", size: "supporting", order: 6}
};
function serviceInfo(name) {
  return Object.hasOwn(SERVICE_LABELS, name) ? SERVICE_LABELS[name] : {label: name, description: "Service check", order: 7};
}
function humanAge(ms) {
  const minutes = Math.max(0, Math.floor(ms / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? 'day' : 'days'} ago`;
}
function tileStatus(current, fresh) {
  const up = confirmedUp(current);
  if (up === null || fresh === "unknown") return {tone: "unknown", text: "Status unavailable"};
  if (fresh !== "fresh") return {tone: "unknown", text: current.ok === false && up ? "Last check failed" : up ? "Previously available" : "Previously unavailable"};
  if (!up) return {tone: "bad", text: "Unavailable"};
  if (current.ok === false) return {tone: "warn", text: "Checking an issue"};
  return {tone: "ok", text: "Operational"};
}
function hourlyHealth(history, names, settings, now) {
  const hour = DAY / 24, cutoff = now - DAY, interval = settings.expected_interval_seconds * 1000;
  const buckets = Array.from({length: 24}, (_, i) => ({start: cutoff + i * hour, slots: new Map(), failed: false}));
  for (const sample of history) {
    const time = +parseTs(sample.ts);
    if (sample.boundary_anchor || time <= cutoff || time > now) continue;
    const bucket = buckets[Math.min(23, Math.ceil((time - cutoff) / hour) - 1)];
    for (const name of names) {
      const result = sample.results[name];
      if (!result || typeof result.ok !== "boolean") continue;
      if (result.ok === false) bucket.failed = true;
      if (!bucket.slots.has(name)) bucket.slots.set(name, new Set());
      bucket.slots.get(name).add(Math.ceil((time - bucket.start) / interval) - 1);
    }
  }
  return buckets.map(bucket => ({start: bucket.start, end: bucket.start + hour,
    tone: bucket.failed ? "warn" : names.every(name =>
      (bucket.slots.get(name)?.size || 0) / Math.ceil(hour / interval) >= settings.minimum_coverage) ? "ok" : "unknown"}));
}
function setHtml(id, html) {
  const node = document.getElementById(id);
  if (node.innerHTML === html) return;
  // Refreshing data must not collapse a diagnostic the reader has opened or lose keyboard focus.
  const openIds = [...(node.querySelectorAll?.("details[open]") || [])].map(item => item.id);
  const focusedId = node.contains?.(document.activeElement) ? document.activeElement.id : null;
  node.innerHTML = html;
  for (const openId of openIds) {
    const item = document.getElementById(openId);
    if (item) item.open = true;
  }
  if (focusedId) document.getElementById(focusedId)?.focus({preventScroll: true});
}
function setSummary(message, tone) {
  document.getElementById("summary").textContent = message;
  const icon = document.getElementById("summary-icon");
  icon.className = `summary-icon ${tone}`;
  icon.textContent = {ok: "✓", warn: "!", bad: "!", unknown: "–"}[tone];
}
function showUnavailable(message = "We couldn’t load the latest checks. Trying again shortly.") {
  setSummary("Service status unavailable", "unknown");
  document.getElementById("since").textContent = message;
  document.getElementById("service-count").textContent = "Awaiting update";
  setHtml("targets", '<p class="muted">Service checks are temporarily unavailable.</p>');
  document.getElementById("service-caption").textContent = "Please check back shortly.";
  for (const id of ["timeline", "diagnostics", "cadence", "history-note"]) setHtml(id, "");
  for (const id of ["history-summary", "incidents", "incident-history", "gaps"]) setHtml(id, '<p class="muted">Monitoring data is unavailable.</p>');
  document.getElementById("updated").textContent = "Waiting for a verified update";
}
function incidentHtml(incident, detailed = false) {
  const info = serviceInfo(incident.name);
  const status = incident.removed ? "Retired service" : incident.ongoing ? "Unresolved at last check" : "Resolved";
  return `<div class="incident"><div class="incident-title"><strong>${esc(info.label)}</strong>` +
    `<span class="${incident.ongoing ? 'incident-state' : 'muted'}">${status}</span></div>` +
    `<p>${esc(fmtLocal(new Date(incident.start).toISOString()))}${!incident.ongoing ? ` · ${age(incident.end - incident.start)}` : ''}</p>` +
    `${detailed && incident.boundary ? '<p>The incident may have started before the retained history.</p>' : ''}</div>`;
}
function render(history, state, now = Date.now()) {
  const el = id => document.getElementById(id);
  const meta = state?._meta, config = meta?.config, settings = config?.settings;
  if (!Array.isArray(history) || !history.length || !settings || !Array.isArray(config.targets) || !config.targets.length ||
      !config.targets.every(t => t && typeof t.name === "string") ||
      !Number.isFinite(settings.expected_interval_seconds) || settings.expected_interval_seconds <= 0 ||
      !Number.isFinite(settings.minimum_coverage) || settings.minimum_coverage <= 0 || settings.minimum_coverage > 1 ||
      !history.every(s => s && s.results && typeof s.results === "object" && Number.isFinite(+parseTs(s.ts)))) {
    showUnavailable();
    return;
  }
  const latest = history[history.length - 1];
  let fresh = freshness(meta.last_run_utc, settings, now);
  if (meta.latest_observation_utc !== latest.ts || +parseTs(latest.ts) > now ||
      +parseTs(latest.ts) > +parseTs(meta.last_run_utc)) fresh = "unknown";
  if (fresh === "unknown") {
    showUnavailable("Waiting for a complete update. Trying again shortly.");
    return;
  }
  const names = config.targets.map(t => t.name).sort((a, b) => serviceInfo(a).order - serviceInfo(b).order);
  const known = names.every(n => confirmedUp(latest.results[n]) !== null);
  const down = names.filter(n => confirmedUp(latest.results[n]) === false).length;
  const pending = names.some(n => latest.results[n]?.ok === false && confirmedUp(latest.results[n]) === true);
  const overall = fresh === "stale" ? ["Waiting for a fresh update", "warn"] :
    fresh === "delayed" ? ["Status update delayed", "warn"] :
    down ? [down === 1 ? "One service has a problem" : `${down} services have problems`, "bad"] :
    !known ? ["Some service statuses are unavailable", "unknown"] :
    pending ? ["Checking a possible issue", "warn"] : ["All systems operational", "ok"];
  setSummary(...overall);
  el("since").textContent = `Last checked ${humanAge(now - +parseTs(latest.ts))}. ` +
    (fresh !== "fresh" ? "Current service status is unconfirmed." : pending ? "We’re checking again to confirm." : "This page updates automatically.");
  el("updated").textContent = `Last update ${fmtLocal(meta.last_run_utc)}`;
  el("service-count").textContent = `${names.length} services`;
  el("service-caption").textContent = fresh === "fresh" ? "Service status at the latest check. More detail is available below." :
    "These are past results. New checks are taking longer than usual.";
  el("cadence").textContent = `Checks are scheduled every ${settings.expected_interval_seconds / 60} minutes; scheduling delays can happen. ` +
    `The page refreshes every ${settings.refresh_seconds} seconds. An outage is confirmed after ${settings.failures_before_down} consecutive failed checks. ` +
    `Data is considered out of date after ${settings.stale_after_seconds / 60} minutes.`;
  const allIncidents = [], allGaps = [], diagnostics = [];
  setHtml("targets", names.map((name, index) => {
    const current = latest.results[name], st = state[name] || {}, info = serviceInfo(name);
    const status = tileStatus(current, fresh);
    const values = [[DAY, "24 hours"], [7 * DAY, "7 days"], [30 * DAY, "30 days"]]
      .map(([duration, label]) => availabilityHtml(availability(history, name, duration, settings, now), label)).join("");
    allIncidents.push(...incidentsFor(history, name));
    const gaps = gapsFor(history, name, settings, now);
    if (gaps.length) allGaps.push(`<p class="gap">${esc(info.label)}: ${gaps.length} gap${gaps.length === 1 ? '' : 's'}; latest ` +
      `${esc(fmtLocal(new Date(gaps.at(-1).start).toISOString()))} — ${esc(fmtLocal(new Date(gaps.at(-1).end).toISOString()))}</p>`);
    let cert = "";
    if (st.cert_days_left != null) cert = `<p>Certificate: ${esc(st.cert_days_left)} days remaining at last inspection. ` +
      `Expires ${esc(st.cert_not_after)} · ${esc(st.cert_issuer)} · inspected ${esc(fmtLocal(st.cert_inspected_utc))}</p>`;
    if (st.cert_failure_streak) cert += `<p>Certificate inspection is unavailable. Retained certificate information may be old.</p>`;
    diagnostics.push(`<details class="target-detail" id="detail-${index}"><summary id="detail-toggle-${index}">${esc(info.label)}</summary>` +
      `<div class="diagnostic-content"><p>${esc(name)} · ${esc(status.text)}</p>` +
      `<p>${esc(current?.detail || 'No observation')} · checked ${esc(fmtLocal(st.last_check_utc || st.last_check))}</p>` +
      `${current?.dns_warning ? `<p>${esc(current.dns_warning)}</p>` : ''}` +
      `${current?.final_url ? `<p>Checked URL: ${esc(current.final_url)}</p>` : ''}` +
      `<div class="uptimes">${values}</div>${cert}</div></details>`);
    return `<article class="service-tile ${status.tone} ${info.size || ''}" aria-labelledby="service-${index}">` +
      `<div><h3 class="tile-heading" id="service-${index}">${esc(info.label)}</h3><p class="tile-description">${esc(info.description)}</p></div>` +
      `<p class="tile-status"><span class="status-dot" aria-hidden="true"></span>${status.text}</p></article>`;
  }).join(""));
  setHtml("diagnostics", diagnostics.join(""));
  // Keep incidents for retired endpoints until the underlying history expires.
  const oldNames = new Set(history.flatMap(s => Object.keys(s.results)));
  for (const name of oldNames) if (!names.includes(name)) allIncidents.push(...incidentsFor(history, name)
    .map(i => ({...i, removed: true})));
  setHtml("gaps", allGaps.join("") || "No gaps longer than two scheduled intervals were recorded.");
  allIncidents.sort((a, b) => b.start - a.start);
  const recent = allIncidents.filter(i => i.end > now - DAY && i.start <= now);
  setHtml("history-summary", `<strong>${recent.length}</strong>confirmed incident${recent.length === 1 ? '' : 's'}`);
  const hours = hourlyHealth(history, names, settings, now);
  const hourLabels = {ok: "Checks passed", warn: "A check failed", unknown: "Some checks are missing"};
  setHtml("timeline", hours.map(h => {
    const label = `${fmtLocal(new Date(h.start).toISOString())} — ${fmtLocal(new Date(h.end).toISOString())}: ${hourLabels[h.tone]}`;
    return `<span class="hour ${h.tone}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></span>`;
  }).join(""));
  el("history-note").textContent = "Each bar is one hour. Missing checks don’t mean downtime; a failed check may be temporary.";
  const priorityIncidents = [...allIncidents].sort((a, b) => Number(b.ongoing && !b.removed) - Number(a.ongoing && !a.removed) || b.start - a.start);
  setHtml("incidents", priorityIncidents.length ? priorityIncidents.slice(0, 3).map(i => incidentHtml(i)).join("") :
    '<div class="quiet-state"><span class="quiet-icon" aria-hidden="true">○</span><div><strong>No confirmed incidents</strong><p>In the available monitoring history.</p></div></div>');
  setHtml("incident-history", allIncidents.length ? allIncidents.map(i => incidentHtml(i, true)).join("") : "No confirmed incidents in the available history.");
}
async function loadPage() {
  clearTimeout(globalThis.statusRefresh);
  let refreshSeconds = 60;
  try {
    const data = await Promise.all(["history.json", "state.json"].map(async path => {
      const response = await fetch(`${path}?_=${Date.now()}`, {cache: "no-store", signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error("unavailable");
      return response.json();
    }));
    render(...data);
    const interval = data[1]?._meta?.config?.settings?.refresh_seconds;
    if (Number.isFinite(interval) && interval > 0) refreshSeconds = interval;
    // Re-evaluate freshness locally even before the next network refresh.
    clearInterval(globalThis.statusClock);
    globalThis.statusClock = setInterval(() => render(...data), Math.min(refreshSeconds, 10) * 1000);
  } catch (_) {
    clearInterval(globalThis.statusClock);
    showUnavailable();
  } finally {
    // Temporary network failures must recover without requiring a manual reload.
    globalThis.statusRefresh = setTimeout(loadPage, refreshSeconds * 1000);
  }
}
if (typeof module !== "undefined") module.exports = {esc, parseTs, freshness, availability, incidentsFor, gapsFor, render, hourlyHealth, tileStatus, showUnavailable, loadPage};
else loadPage();
