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
function render(history, state, now = Date.now()) {
  const el = id => document.getElementById(id);
  const meta = state?._meta, config = meta?.config, settings = config?.settings;
  if (!Array.isArray(history) || !history.length || !settings || !Array.isArray(config.targets) || !config.targets.length ||
      !history.every(s => s && s.results && typeof s.results === 'object' && Number.isFinite(+parseTs(s.ts)))) {
    el("summary").textContent = "UNKNOWN — monitoring data unavailable or awaiting metadata migration";
    el("targets").innerHTML = "";
    el("incidents").textContent = "Incident data unavailable";
    el("gaps").textContent = "Observation coverage unavailable";
    return;
  }
  const latest = history[history.length - 1];
  let fresh = freshness(meta.last_run_utc, settings, now);
  if (meta.latest_observation_utc !== latest.ts || +parseTs(latest.ts) > now ||
      +parseTs(latest.ts) > +parseTs(meta.last_run_utc)) fresh = "unknown";
  const names = config.targets.map(t => t.name);
  const known = names.every(n => confirmedUp(latest.results[n]) !== null);
  const down = names.filter(n => confirmedUp(latest.results[n]) === false).length;
  const pending = names.some(n => latest.results[n]?.ok === false && confirmedUp(latest.results[n]) === true);
  const messages = {stale: "STALE — monitoring delayed; endpoint status is historical",
    delayed: "DELAYED — awaiting a scheduled observation", unknown: "UNKNOWN — incomplete monitoring data"};
  el("summary").textContent = messages[fresh] || (!known ? messages.unknown : down ? `${down} endpoint(s) DOWN` :
    pending ? "Observation failed — awaiting confirmation" : "All systems operational (last observation)");
  el("since").textContent = `Last observation ${age(now - +parseTs(latest.ts))} ago. ` +
    `Failures are confirmed after ${settings.failures_before_down} consecutive failed observations.`;
  el("updated").textContent = `Last completed run ${fmtLocal(meta.last_run_utc)}`;
  el("cadence").textContent = `Expected interval ${settings.expected_interval_seconds / 60} min · ` +
    `page refresh ${settings.refresh_seconds}s · stale after ${settings.stale_after_seconds / 60} min · best effort`;
  const allIncidents = [], allGaps = [];
  el("targets").innerHTML = names.map(name => {
    const current = latest.results[name], st = state[name] || {};
    const isUp = confirmedUp(current);
    const status = !current || isUp === null ? "UNKNOWN" : fresh !== "fresh" ? `LAST OBSERVED ${isUp ? "UP" : "DOWN"}` :
      !isUp ? "DOWN" : current.ok === false ? "PENDING FAILURE" : "UP";
    const values = [[DAY, "24h"], [7 * DAY, "7d"], [30 * DAY, "30d"]]
      .map(([duration, label]) => availabilityHtml(availability(history, name, duration, settings, now), label)).join("");
    const samples = history.filter(s => s.results[name] && !s.boundary_anchor).slice(-40);
    const bars = samples.map(s => {
      const r = s.results[name];
      const label = r.ok === false ? "failed observation" : r.latency_ms > settings.latency_warn_ms ? "slow observation" : "successful observation";
      const cls = r.ok === false ? "bad" : r.latency_ms > settings.latency_warn_ms ? "slow" : "ok";
      return `<span class="bar ${cls}" style="height:100%" role="img" aria-label="${esc(`${fmtLocal(s.ts)}: ${label}`)}" ` +
        `title="${esc(`${fmtLocal(s.ts)}: ${label}, ${r.latency_ms ?? 'unknown'} ms`)}"></span>`;
    }).join("");
    allIncidents.push(...incidentsFor(history, name));
    const gaps = gapsFor(history, name, settings, now);
    if (gaps.length) allGaps.push(`<div>${esc(name)}: ${gaps.length} monitoring gap(s) in 24h; latest ` +
      `${esc(fmtLocal(new Date(gaps.at(-1).start).toISOString()))} — ${esc(fmtLocal(new Date(gaps.at(-1).end).toISOString()))}</div>`);
    let cert = "";
    if (st.cert_days_left != null) cert = `<div class="cert">TLS: ${esc(st.cert_days_left)} days at last inspection; ` +
      `expires ${esc(st.cert_not_after)} · ${esc(st.cert_issuer)} · inspected ${esc(fmtLocal(st.cert_inspected_utc))}</div>`;
    if (st.cert_failure_streak) cert += `<div class="warn">TLS metadata inspection unavailable (${esc(st.cert_failure_streak)} observations); retained certificate data may be old.</div>`;
    return `<div class="card"><div class="row"><span class="name">${esc(name)}</span>` +
      `<span class="badge ${status === 'UP' ? 'ok' : 'bad'}">${status}</span></div>` +
      `<div class="meta">${esc(current?.detail || 'No observation')} · checked ${esc(fmtLocal(st.last_check_utc || st.last_check))}` +
      `${current?.dns_warning ? ` · ${esc(current.dns_warning)}` : ''}` +
      `${current?.final_url ? ` · final URL ${esc(current.final_url)}` : ''}</div>` +
      `<div class="uptimes">${values}</div><div class="bars" role="group" aria-label="Recent observations; gaps listed separately">${bars}</div>${cert}</div>`;
  }).join("");
  // Include removed endpoints in the historical incident view until retention expires.
  const oldNames = new Set(history.flatMap(s => Object.keys(s.results)));
  for (const name of oldNames) if (!names.includes(name)) allIncidents.push(...incidentsFor(history, name)
    .map(i => ({...i, removed: true})));
  el("gaps").innerHTML = allGaps.join("") || "No gaps longer than two expected intervals observed in 24h.";
  allIncidents.sort((a, b) => b.start - a.start);
  el("incidents").innerHTML = allIncidents.length ? allIncidents.slice(0, 12).map(i =>
    `<div class="incident"><span>${esc(i.name)} · ${esc(fmtLocal(new Date(i.start).toISOString()))}` +
    `${i.boundary ? ' (start may precede retained observations)' : ''}</span><span>` +
    `${i.removed ? 'Target retired; last observed incident' : i.ongoing ? 'Open at last observation' : `Recovered after ${age(i.end - i.start)}`}` +
    ` · elapsed time may include monitoring gaps</span></div>`).join("") : "No confirmed incidents in retained observations.";
}
async function loadPage() {
  try {
    const data = await Promise.all(["history.json", "state.json"].map(async path => {
      const response = await fetch(`${path}?_=${Date.now()}`, {cache: "no-store"});
      if (!response.ok) throw new Error("unavailable");
      return response.json();
    }));
    render(...data);
    const interval = data[1]?._meta?.config?.settings?.refresh_seconds;
    if (Number.isFinite(interval) && interval > 0) setTimeout(loadPage, interval * 1000);
    // A local timer keeps stale status accurate even if subsequent fetches fail.
    clearInterval(globalThis.statusClock);
    globalThis.statusClock = setInterval(() => render(...data), Math.min(interval || 60, 10) * 1000);
  } catch (_) {
    clearInterval(globalThis.statusClock);
    document.getElementById("summary").textContent = "UNKNOWN — could not load monitoring data; reload to retry";
    document.getElementById("targets").innerHTML = "";
  }
}
if (typeof module !== "undefined") module.exports = {esc, parseTs, freshness, availability, incidentsFor, gapsFor, render};
else loadPage();
