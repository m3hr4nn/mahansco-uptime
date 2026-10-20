/* Public rendering and pure calculations. CommonJS exports support offline tests. */
"use strict";
const DAY = 86400000;
const UI = {query: "", filter: "all", rangeDays: 1, model: null};

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
  return Number.isFinite(+date) ? new Date(+date + 210 * 60000).toISOString().slice(0, 16)
    .replace("T", " ") + " UTC+03:30" : "unknown";
}
function shortLocal(value) {
  const date = parseTs(value);
  return Number.isFinite(+date) ? new Date(+date + 210 * 60000).toISOString().slice(5, 16)
    .replace("T", " ") : "unknown";
}
function age(ms) {
  const minutes = Math.max(0, Math.floor(ms / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
function duration(ms) {
  const minutes = Math.max(0, Math.floor(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} hr`;
  return `${Math.floor(hours / 24)} days`;
}
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
    slots.set(slot, (slots.get(slot) ?? true) && result.ok === true);
  }
  const observed = slots.size, successful = [...slots.values()].filter(Boolean).length;
  const coverage = observed / expected;
  return {expected, observed, successful, coverage, missing: expected - observed,
    percent: observed && coverage >= settings.minimum_coverage ? successful / observed * 100 : null};
}
function incidentsFor(history, name) {
  const incidents = [];
  let active = null, pending = null;
  for (const sample of history) {
    const result = sample.results[name];
    if (!result) continue;
    const up = confirmedUp(result), observed = +parseTs(sample.ts);
    if (up === false) {
      if (!active) active = {name,
        start: result.incident_started_utc ? +parseTs(result.incident_started_utc) : observed,
        firstObserved: pending?.observed || observed, end: observed, ongoing: true,
        firstDetail: pending?.detail || result.detail, latestDetail: result.detail,
        boundary: !!sample.boundary_anchor || !result.incident_started_utc};
      active.end = observed;
      active.latestDetail = result.detail || active.latestDetail;
    } else if (up === true && active) {
      active.end = observed;
      active.ongoing = false;
      active.recoveryDetail = result.detail;
      incidents.push(active);
      active = null;
      pending = null;
    } else if (up === true && result.ok === false) {
      pending = {observed, detail: result.detail};
    } else if (up === true) {
      pending = null;
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
const SERVICE_LABELS = {
  "Landing (mahansco.ir)": {label: "Website", description: "Marketing website", hostname: "mahansco.ir", order: 0},
  "App SPA (app.mahansco.ir)": {label: "Web app", description: "Customer application shell", hostname: "app.mahansco.ir", order: 1},
  "App health API": {label: "App API", description: "Application health response", hostname: "api.mahansco.ir", order: 2},
  "Application access check": {label: "Application access", description: "Unauthenticated access boundary", hostname: "api.mahansco.ir", order: 3},
  "Marketing API": {label: "Website API", description: "Published content API", hostname: "mahansco.ir", order: 4},
  "robots.txt canary": {label: "Site access", description: "Public robots canary", hostname: "mahansco.ir", order: 5}
};
function serviceInfo(name, target = {}) {
  const known = Object.hasOwn(SERVICE_LABELS, name) ? SERVICE_LABELS[name] : {};
  return {label: known.label || name, description: known.description || "Generic HTTPS reachability",
    hostname: target.hostname || known.hostname || "", order: known.order ?? 99};
}
function tileStatus(current, fresh) {
  const up = confirmedUp(current);
  if (up === null || fresh === "unknown") return {tone: "unknown", text: "Status unavailable", group: "unknown"};
  if (fresh !== "fresh") return {tone: "unknown",
    text: current?.ok === false && up ? "Last check failed" : up ? "Previously available" : "Previously unavailable",
    group: "unknown"};
  if (!up) return {tone: "bad", text: "Confirmed outage", group: "attention"};
  if (current.ok === false) return {tone: "warn", text: "Pending failure", group: "attention"};
  return {tone: "ok", text: "Healthy", group: "healthy"};
}
function filterServices(items, query = "", filter = "all") {
  const needle = query.trim().toLocaleLowerCase();
  return items.filter(item => (!needle || `${item.name} ${item.label} ${item.hostname}`.toLocaleLowerCase().includes(needle)) &&
    (filter === "all" || item.group === filter));
}
function historyBuckets(history, names, windowMs, settings, now = Date.now(), maxBars = 48) {
  const interval = settings.expected_interval_seconds * 1000;
  const expected = Math.ceil(windowMs / interval);
  const bucketSlots = Math.max(1, Math.ceil(expected / maxBars));
  const bucketMs = interval * bucketSlots;
  const count = Math.ceil(windowMs / bucketMs), cutoff = now - windowMs;
  const buckets = Array.from({length: count}, (_, index) => ({
    start: cutoff + index * bucketMs, end: Math.min(now, cutoff + (index + 1) * bucketMs),
    slots: new Map(), failed: false, latencies: []
  }));
  for (const sample of history) {
    const time = +parseTs(sample.ts);
    if (sample.boundary_anchor || time <= cutoff || time > now) continue;
    const bucket = buckets[Math.min(count - 1, Math.floor((time - cutoff) / bucketMs))];
    const slot = Math.min(bucketSlots - 1, Math.floor((time - bucket.start) / interval));
    for (const name of names) {
      const result = sample.results[name];
      if (!result || typeof result.ok !== "boolean") continue;
      if (result.ok === false) bucket.failed = true;
      if (Number.isFinite(result.latency_ms)) bucket.latencies.push(result.latency_ms);
      if (!bucket.slots.has(name)) bucket.slots.set(name, new Set());
      bucket.slots.get(name).add(slot);
    }
  }
  return buckets.map(bucket => {
    const possible = Math.max(1, Math.ceil((bucket.end - bucket.start) / interval));
    const complete = names.every(name => (bucket.slots.get(name)?.size || 0) / possible >= settings.minimum_coverage);
    return {...bucket, tone: bucket.failed ? "warn" : complete ? "ok" : "unknown",
      latency: bucket.latencies.length ? bucket.latencies.reduce((a, b) => a + b, 0) / bucket.latencies.length : null};
  });
}
function hourlyHealth(history, names, settings, now) {
  return historyBuckets(history, names, DAY, settings, now, 24);
}
function setHtml(id, html) {
  const node = document.getElementById(id);
  if (!node || node.innerHTML === html) return;
  const openIds = [...(node.querySelectorAll?.("details[open]") || [])].map(item => item.id);
  const focusedId = node.contains?.(document.activeElement) ? document.activeElement.id : null;
  node.innerHTML = html;
  for (const openId of openIds) {
    const item = document.getElementById(openId);
    if (item) item.open = true;
  }
  if (focusedId) document.getElementById(focusedId)?.focus?.({preventScroll: true});
}
function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}
function availabilityHtml(value, label) {
  const result = value.percent === null ? "Insufficient data" : `${value.percent.toFixed(2)}%`;
  return `<div class="availability"><span>${esc(label)}</span><strong>${result}</strong><small>${(value.coverage * 100).toFixed(1)}% coverage · ${value.observed}/${value.expected} slots</small></div>`;
}
function trendHtml(history, name, windowMs, settings, now) {
  const buckets = historyBuckets(history, [name], windowMs, settings, now, 36);
  const observed = buckets.filter(bucket => bucket.latency !== null);
  if (!observed.length) return '<div class="trend-empty">No response-time observations in this range.</div>';
  const max = Math.max(...observed.map(bucket => bucket.latency), 1);
  const points = buckets.map((bucket, index) => bucket.latency === null ? null :
    `${(index / Math.max(1, buckets.length - 1) * 100).toFixed(2)},${(30 - bucket.latency / max * 25).toFixed(2)}`)
    .filter(Boolean).join(" ");
  const latest = observed.at(-1).latency;
  return `<div class="trend-head"><span>Response time trend</span><strong>${Math.round(latest)} ms latest</strong></div>
    <svg class="trend" viewBox="0 0 100 34" preserveAspectRatio="none" role="img" aria-label="Recorded response times; latest ${Math.round(latest)} milliseconds">
      <path d="M0 31 H100" class="trend-base"></path><polyline points="${points}" class="trend-line"></polyline>
    </svg>`;
}
function statusEvidence(current) {
  if (!current) return "No observation was recorded.";
  if (current.status_code != null && current.detail === "Response failed semantic assertion") {
    return `HTTP ${current.status_code}; expected response content was missing or different.`;
  }
  return current.detail || (current.status_code != null ? `HTTP ${current.status_code}` : "Request did not complete.");
}
function weeklyHtml(weekly) {
  const report = weekly?.latest;
  if (!report || report.schema_version !== 1) return null;
  const availability = Number.isFinite(report.availability_percent) ? `${report.availability_percent.toFixed(2)}%` : "Insufficient data";
  const latency = Number.isFinite(report.median_ms) ? `${Math.round(report.median_ms)} / ${Math.round(report.p95_ms)} ms` : "Unavailable";
  return {window: `${fmtLocal(report.window_start_utc)} – ${fmtLocal(report.window_end_utc)}`, health: report.health,
    metrics: `<div><span>Availability</span><strong>${availability}</strong><small>${Number(report.coverage_percent).toFixed(0)}% coverage</small></div>
      <div><span>HTTP median / p95</span><strong>${latency}</strong><small>${esc(report.latency_samples)} valid samples</small></div>
      <div><span>Incidents</span><strong>${esc(report.incident_count)}</strong><small>${duration(report.longest_incident_seconds * 1000)} longest</small></div>
      <div><span>Missing slots</span><strong>${esc(report.missing_slots)}</strong><small>${esc(report.observed_slots)}/${esc(report.expected_slots)} observed</small></div>`,
    observations: (report.observations || []).map(value => `<li>${esc(value)}</li>`).join("")};
}
function incidentHtml(incident, detailed = false) {
  const info = serviceInfo(incident.name);
  const status = incident.removed ? "Retired service" : incident.ongoing ? "Unresolved at latest observation" : "Recovered";
  return `<article class="incident-row ${incident.ongoing ? "bad" : "resolved"}">
    <div class="incident-marker" aria-hidden="true">${incident.ongoing ? "!" : "✓"}</div>
    <div><div class="incident-title"><strong>${esc(info.label)}</strong><span>${status}</span></div>
    <p>${esc(incident.latestDetail || "Confirmed failure")}</p>
    <dl><div><dt>First detected</dt><dd>${esc(fmtLocal(new Date(incident.firstObserved || incident.start).toISOString()))}</dd></div>
    <div><dt>Latest observation</dt><dd>${esc(fmtLocal(new Date(incident.end).toISOString()))}</dd></div>
    ${!incident.ongoing ? `<div><dt>Observed duration</dt><dd>${duration(incident.end - incident.start)}</dd></div>` : ""}</dl>
    ${detailed && incident.boundary ? "<small>Start may predate retained history.</small>" : ""}</div></article>`;
}
function serviceCard(item, history, state, settings, now, index) {
  const current = item.current, st = state[item.name] || {}, rangeMs = UI.rangeDays * DAY;
  const avail = availability(history, item.name, rangeMs, settings, now);
  const buckets = historyBuckets(history, [item.name], rangeMs, settings, now, 36);
  const barLabels = {ok: "passed", warn: "failed", unknown: "missing"};
  const cert = st.cert_days_left == null ? '<span class="datum muted">Not collected</span>' :
    `<span class="datum ${st.cert_days_left <= 21 ? "warn-text" : ""}">${esc(st.cert_days_left)} days · ${esc(st.cert_not_after)}</span>`;
  return `<article class="service-card ${item.tone}" data-search="${esc(`${item.name} ${item.label} ${item.hostname}`.toLocaleLowerCase())}" data-group="${item.group}">
    <div class="service-main">
      <div class="service-identity"><span class="status-icon" aria-hidden="true">${{ok: "✓", bad: "×", warn: "!", unknown: "?"}[item.tone]}</span>
        <div><h3>${esc(item.label)}</h3><p>${esc(item.hostname || item.description)}</p></div></div>
      <span class="status-pill ${item.tone}">${esc(item.statusText)}</span>
    </div>
    <div class="service-data">
      <div><span>HTTP</span><strong>${current?.status_code ?? "—"}</strong></div>
      <div><span>HTTP total</span><strong>${Number.isFinite(current?.latency_ms) ? `${current.latency_ms} ms` : "—"}</strong></div>
      <div><span>Last check</span><strong>${esc(age(now - +parseTs(st.last_check_utc || st.last_check || "")))}</strong></div>
      <div><span>TLS certificate</span>${cert}</div>
      <div><span>Observed availability</span><strong>${avail.percent === null ? "Insufficient data" : `${avail.percent.toFixed(2)}%`}</strong><small>${(avail.coverage * 100).toFixed(0)}% coverage</small></div>
    </div>
    <div class="mini-history" aria-label="${UI.rangeDays}-day observation history">${buckets.map(bucket => {
      const label = `${fmtLocal(new Date(bucket.start).toISOString())}: ${barLabels[bucket.tone]}`;
      return `<i class="${bucket.tone}" title="${esc(label)}" aria-label="${esc(label)}"></i>`;
    }).join("")}</div>
    <details class="service-detail" id="service-detail-${index}">
      <summary>Investigate <span aria-hidden="true">↓</span></summary>
      <div class="detail-grid">
        <div class="evidence"><span class="detail-label">Latest evidence</span><strong>${esc(statusEvidence(current))}</strong>
          <p>Observed ${esc(fmtLocal(st.last_check_utc || st.last_check))}</p>
          <p>DNS preflight ${Number.isFinite(current?.dns_ms) ? `${current.dns_ms} ms` : "unavailable"} · redirects ${Number.isFinite(current?.redirect_count) ? current.redirect_count : "not recorded"} · body ${Number.isFinite(current?.response_bytes) ? `${current.response_bytes} bytes` : "not recorded"}</p>
          ${current?.dns_warning ? `<p class="warning-copy">${esc(current.dns_warning)}</p>` : ""}
          ${st.cert_failure_streak ? '<p class="warning-copy">TLS inspection is unavailable; certificate data may be old.</p>' : ""}
        </div>
        <div>${trendHtml(history, item.name, rangeMs, settings, now)}</div>
      </div>
      <div class="availability-grid">${[[DAY, "24 hours"], [7 * DAY, "7 days"], [30 * DAY, "30 days"]]
        .map(([span, label]) => availabilityHtml(availability(history, item.name, span, settings, now), label)).join("")}</div>
      <p class="detail-note">HTTP total runs from request start through the full response body; the separately measured DNS preflight is excluded. Historical connect, TLS and TTFB phases were not collected.</p>
    </details>
  </article>`;
}
function applyServiceFilters() {
  if (!UI.model || typeof document === "undefined") return;
  const matches = filterServices(UI.model.items, UI.query, UI.filter);
  const names = new Set(matches.map(item => item.name));
  const cards = document.querySelectorAll?.(".service-card") || [];
  for (const card of cards) card.hidden = !names.has(card.dataset.name || card.getAttribute?.("data-name"));
  setText("results-count", `${matches.length} of ${UI.model.items.length} services`);
  const empty = document.getElementById("empty-results");
  if (empty) empty.hidden = matches.length !== 0;
}
function showUnavailable(message = "We couldn’t load the latest checks. Trying again shortly.") {
  setText("summary", "Service status unavailable");
  setText("since", message);
  setText("updated", "Waiting for a verified update");
  ["count-healthy", "count-failing", "count-pending", "count-unknown", "freshness-age"].forEach(id => setText(id, "—"));
  setHtml("targets", '<p class="unavailable-state">Service observations are unavailable.</p>');
  setHtml("attention-list", '<p class="unavailable-state">Monitoring data is unavailable.</p>');
  setHtml("timeline", "");
  setHtml("history-summary", '<strong>Data unavailable</strong>');
  setHtml("incident-history", '<p>Monitoring data is unavailable.</p>');
  setHtml("gaps", '<p>Monitoring data is unavailable.</p>');
}
function render(history, state, now = Date.now(), weekly = null) {
  const meta = state?._meta, config = meta?.config, settings = config?.settings;
  if (!Array.isArray(history) || !history.length || !settings || !Array.isArray(config.targets) || !config.targets.length ||
      !config.targets.every(t => t && typeof t.name === "string") ||
      !Number.isFinite(settings.expected_interval_seconds) || settings.expected_interval_seconds <= 0 ||
      !Number.isFinite(settings.minimum_coverage) || settings.minimum_coverage <= 0 || settings.minimum_coverage > 1 ||
      !history.every(s => s && s.results && typeof s.results === "object" && Number.isFinite(+parseTs(s.ts)))) {
    showUnavailable();
    return;
  }
  const latest = history.at(-1);
  let fresh = freshness(meta.last_run_utc, settings, now);
  if (meta.latest_observation_utc !== latest.ts || +parseTs(latest.ts) > now ||
      +parseTs(latest.ts) > +parseTs(meta.last_run_utc)) fresh = "unknown";
  if (fresh === "unknown") {
    showUnavailable("Waiting for a complete update. Trying again shortly.");
    return;
  }
  const targets = [...config.targets].sort((a, b) => serviceInfo(a.name, a).order - serviceInfo(b.name, b).order);
  const items = targets.map(target => {
    const info = serviceInfo(target.name, target), status = tileStatus(latest.results[target.name], fresh);
    return {name: target.name, ...info, current: latest.results[target.name], tone: status.tone,
      group: status.group, statusText: status.text};
  });
  const counts = {
    healthy: items.filter(i => i.group === "healthy").length,
    failing: items.filter(i => i.tone === "bad").length,
    pending: items.filter(i => i.tone === "warn").length,
    unknown: items.filter(i => i.group === "unknown").length
  };
  const overall = fresh === "stale" ? ["Monitoring data is stale", "stale"] :
    fresh === "delayed" ? ["Monitoring is delayed", "delayed"] :
    counts.failing === 1 ? ["1 service needs attention", "bad"] :
    counts.failing ? [`${counts.failing} services need attention`, "bad"] :
    counts.pending ? ["Possible issue detected", "warn"] :
    counts.unknown ? ["Status incomplete", "unknown"] : ["All services healthy", "ok"];
  setText("summary", overall[0]);
  setText("since", fresh === "fresh" ? `Checked ${age(now - +parseTs(latest.ts))} · Tehran (UTC+03:30)` :
    "Waiting for a current observation.");
  setText("updated", `Latest cycle · ${fmtLocal(meta.last_run_utc)}`);
  setText("count-healthy", counts.healthy);
  setText("count-failing", counts.failing);
  setText("count-pending", counts.pending);
  setText("count-unknown", counts.unknown);
  setText("freshness-age", age(now - +parseTs(meta.last_run_utc)));
  setText("freshness-detail", fresh === "fresh" ? "Fresh" : fresh === "delayed" ? "Delayed" : "Stale");
  const badge = document.getElementById("freshness-badge");
  if (badge) {
    badge.className = `freshness-badge ${fresh}`;
    badge.innerHTML = `<span aria-hidden="true">◷</span> ${fresh === "fresh" ? "Fresh data" : fresh === "delayed" ? "Delayed data" : "Stale data"}`;
  }

  const allIncidents = [];
  for (const item of items) allIncidents.push(...incidentsFor(history, item.name));
  const warnings = [];
  if (fresh !== "fresh") warnings.push({priority: -1,
    html: `<article class="warning-row"><div class="incident-marker">!</div><div><div class="incident-title"><strong>Monitor freshness</strong><span>${fresh} data</span></div><p>No current service state can be confirmed. Last completed cycle: ${esc(fmtLocal(meta.last_run_utc))}.</p></div></article>`});
  for (const item of items) {
    const st = state[item.name] || {}, current = item.current;
    const incident = allIncidents.find(value => value.name === item.name && value.ongoing);
    if (incident) warnings.push({priority: 0, html: incidentHtml(incident)});
    else if (current?.ok === false && confirmedUp(current) === true) warnings.push({priority: 1,
      html: `<article class="warning-row"><div class="incident-marker">!</div><div><div class="incident-title"><strong>${esc(item.label)}</strong><span>Pending confirmation</span></div><p>${esc(statusEvidence(current))}</p><dl><div><dt>First detection</dt><dd>${esc(fmtLocal(latest.ts))}</dd></div><div><dt>Latest observation</dt><dd>${esc(fmtLocal(latest.ts))}</dd></div></dl></div></article>`});
    if (st.cert_failure_streak) warnings.push({priority: 2,
      html: `<article class="warning-row"><div class="incident-marker">!</div><div><div class="incident-title"><strong>${esc(item.label)} TLS</strong><span>Inspection unavailable</span></div><p>Last certificate information may be old.</p></div></article>`});
    else if (st.cert_days_left != null && st.cert_days_left <= Math.max(...settings.cert_warn_days)) warnings.push({priority: 2,
      html: `<article class="warning-row"><div class="incident-marker">!</div><div><div class="incident-title"><strong>${esc(item.label)} TLS</strong><span>${esc(st.cert_days_left)} days left</span></div><p>Certificate expires ${esc(st.cert_not_after)} · ${esc(st.cert_issuer)}</p></div></article>`});
    if (current?.dns_warning) warnings.push({priority: 3,
      html: `<article class="warning-row"><div class="incident-marker">!</div><div><div class="incident-title"><strong>${esc(item.label)} DNS</strong><span>Advisory</span></div><p>${esc(current.dns_warning)}</p></div></article>`});
  }
  warnings.sort((a, b) => a.priority - b.priority);
  setText("attention-count", warnings.length ? `${warnings.length} active` : "Clear");
  setHtml("attention-list", warnings.length ? warnings.map(item => item.html).join("") :
    '<div class="quiet-state"><span aria-hidden="true">✓</span><div><strong>No active incidents or warnings</strong><p>The latest recorded cycle has no actionable findings.</p></div></div>');

  const weeklyView = weeklyHtml(weekly);
  setText("weekly-window", weeklyView?.window || "First report pending");
  setText("weekly-health", weeklyView?.health || "Pending");
  setHtml("weekly-metrics", weeklyView?.metrics || '<p class="muted">No weekly report has been delivered yet.</p>');
  setHtml("weekly-observations", weeklyView?.observations || "");
  UI.model = {history, state, settings, now, items, weekly};
  setHtml("targets", items.map((item, index) => serviceCard(item, history, state, settings, now, index)
    .replace("<article class=", `<article data-name="${esc(item.name)}" class=`)).join(""));
  applyServiceFilters();

  const rangeMs = UI.rangeDays * DAY;
  const fleet = historyBuckets(history, items.map(item => item.name), rangeMs, settings, now, 72);
  const observed = fleet.filter(bucket => bucket.tone !== "unknown").length;
  setHtml("history-summary", `<strong>${observed}/${fleet.length}</strong><span>observed time buckets</span>`);
  const labels = {ok: "checks passed", warn: "at least one probe failed", unknown: "observations missing"};
  setHtml("timeline", fleet.map(bucket => {
    const label = `${fmtLocal(new Date(bucket.start).toISOString())}: ${labels[bucket.tone]}`;
    return `<i class="history-bar ${bucket.tone}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></i>`;
  }).join(""));
  setText("range-start", UI.rangeDays === 1 ? "24 hours ago" : `${UI.rangeDays} days ago`);
  setText("history-note", `${settings.expected_interval_seconds / 3600}h slots · red failed · gray missing`);
  setText("cadence", `Checks are expected about every ${settings.expected_interval_seconds / 3600} hours. Data becomes delayed after ${settings.delayed_after_seconds / 3600} hours and stale after ${settings.stale_after_seconds / 3600} hours. A confirmed outage requires ${settings.failures_before_down} consecutive failures.`);

  const gaps = items.flatMap(item => gapsFor(history, item.name, settings, now).map(gap => ({...gap, label: item.label})));
  setHtml("gaps", gaps.length ? gaps.map(gap => `<p><strong>${esc(gap.label)}</strong> · ${esc(fmtLocal(new Date(gap.start).toISOString()))} to ${esc(fmtLocal(new Date(gap.end).toISOString()))}</p>`).join("") :
    "<p>No gap longer than two expected intervals in the last 24 hours.</p>");
  const oldNames = new Set(history.flatMap(sample => Object.keys(sample.results)));
  for (const name of oldNames) if (!items.some(item => item.name === name)) {
    allIncidents.push(...incidentsFor(history, name).map(incident => ({...incident, removed: true})));
  }
  allIncidents.sort((a, b) => b.start - a.start);
  setHtml("incident-history", allIncidents.length ? allIncidents.map(incident => incidentHtml(incident, true)).join("") :
    "<p>No confirmed incidents in retained history.</p>");
}
function rerenderRange(days) {
  UI.rangeDays = days;
  if (UI.model) render(UI.model.history, UI.model.state, UI.model.now, UI.model.weekly);
}
function resetFilters() {
  UI.query = "";
  UI.filter = "all";
  const search = document.getElementById("service-search");
  if (search) search.value = "";
  for (const button of document.querySelectorAll?.(".filter") || []) {
    const active = button.dataset.filter === "all";
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  applyServiceFilters();
}
function initializeUi() {
  const search = document.getElementById("service-search");
  search?.addEventListener("input", event => { UI.query = event.target.value; applyServiceFilters(); });
  document.getElementById("status-filters")?.addEventListener("click", event => {
    const button = event.target.closest?.("[data-filter]");
    if (!button) return;
    UI.filter = button.dataset.filter;
    for (const item of document.querySelectorAll(".filter")) {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    }
    applyServiceFilters();
  });
  document.getElementById("range-controls")?.addEventListener("click", event => {
    const button = event.target.closest?.("[data-range]");
    if (!button) return;
    for (const item of document.querySelectorAll("#range-controls button")) {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    }
    rerenderRange(Number(button.dataset.range));
  });
  document.getElementById("reset-filters")?.addEventListener("click", resetFilters);
  document.getElementById("empty-reset")?.addEventListener("click", resetFilters);
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
    const weekly = await fetch(`weekly_report.json?_=${Date.now()}`, {cache: "no-store", signal: AbortSignal.timeout(15000)})
      .then(response => response.ok ? response.json() : null).catch(() => null);
    render(data[0], data[1], Date.now(), weekly);
    const interval = data[1]?._meta?.config?.settings?.refresh_seconds;
    if (Number.isFinite(interval) && interval > 0) refreshSeconds = interval;
    clearInterval(globalThis.statusClock);
    globalThis.statusClock = setInterval(() => render(data[0], data[1], Date.now(), weekly), Math.min(refreshSeconds, 10) * 1000);
  } catch (_) {
    clearInterval(globalThis.statusClock);
    showUnavailable();
  } finally {
    globalThis.statusRefresh = setTimeout(loadPage, refreshSeconds * 1000);
  }
}
if (typeof module !== "undefined") module.exports = {esc, parseTs, freshness, availability, incidentsFor,
  gapsFor, render, hourlyHealth, historyBuckets, tileStatus, filterServices, weeklyHtml, showUnavailable, loadPage};
else {
  initializeUi();
  loadPage();
}
