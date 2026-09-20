"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const page = require("../docs/status.js");
const settings = require("../targets.json").settings;
const now = Date.parse("2026-09-19T12:00:00Z");
const iso = value => new Date(value).toISOString();
const sample = (time, up, ok = up) => ({ts: iso(time), results: {Example: {confirmed_up: up, ok, detail: "OK"}}});
test("freshness boundary values, missing and future observations", () => {
  for (const [seconds, expected] of [[0, "fresh"], [600, "fresh"], [601, "delayed"],
    [1200, "delayed"], [1201, "stale"]]) {
    assert.equal(page.freshness(iso(now - seconds * 1000), settings, now), expected);
  }
  assert.equal(page.freshness(null, settings, now), "unknown");
  assert.equal(page.freshness("broken", settings, now), "unknown");
  assert.equal(page.freshness(iso(now + 1000), settings, now), "unknown");
});
test("missing schedules reduce coverage; duplicate checks cannot fill gaps", () => {
  const complete = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map(i => sample(now - i * 300000, true));
  let result = page.availability(complete, "Example", 3000000, settings, now);
  assert.equal(result.coverage, 1);
  assert.equal(result.percent, 100);
  result = page.availability(complete.slice(-2), "Example", 3000000, settings, now);
  assert.equal(result.coverage, 0.2);
  assert.equal(result.percent, null);
  result = page.availability([...complete.slice(-2), ...Array(20).fill(complete.at(-1))], "Example", 3000000, settings, now);
  assert.equal(result.observed, 2);
  complete[0].results.Example.ok = false; // a debounced failure still isn't an observed success
  result = page.availability(complete, "Example", 3000000, settings, now);
  assert.equal(result.percent, 90);
  assert.equal(page.availability(complete.slice(1), "Example", 3000000, settings, now).coverage, 0.9);
});
test("single raw failure is not an incident; confirmed down starts immediately and recovery closes", () => {
  const history = [sample(now, true), sample(now + 300000, true, false), sample(now + 600000, false),
    sample(now + 900000, false), sample(now + 1200000, true)];
  assert.equal(page.incidentsFor(history.slice(0, 2), "Example").length, 0);
  assert.equal(page.incidentsFor(history.slice(0, 3), "Example").length, 1);
  const incidents = page.incidentsFor(history, "Example");
  assert.equal(incidents.length, 1);
  assert.equal(incidents[0].end - incidents[0].start, 600000);
  assert.equal(incidents[0].ongoing, false);
});
test("retained boundary preserves incident start; gaps never manufacture outages", () => {
  const history = [sample(now, false), sample(now + 3600000, true)];
  history[0].results.Example.incident_started_utc = iso(now - 86400000);
  history[0].boundary_anchor = true;
  const incident = page.incidentsFor(history, "Example")[0];
  assert.equal(incident.start, now - 86400000);
  assert.equal(incident.end, now + 3600000);
  history[0].results.Example.confirmed_up = true;
  assert.equal(page.incidentsFor(history, "Example").length, 0);
  assert.ok(page.gapsFor(history, "Example", settings, now + 3600000).length);
  delete history[0].results.Example.confirmed_up;
  assert.equal(page.incidentsFor(history, "Example").length, 0);
});
test("Tehran legacy timestamps parse as UTC instants", () => {
  assert.equal(+page.parseTs("2026-09-19 15:30 IRST"), now);
  assert.equal(+page.parseTs("2026-09-19 12:00 UTC"), now);
});
function documentStub() {
  const elements = {};
  global.document = {getElementById(id) { return elements[id] ||= {textContent: "", innerHTML: ""}; }};
  return elements;
}
test("render never calls stale, missing, or mismatched cycles healthy", () => {
  const elements = documentStub();
  const history = [sample(now - 1201000, true)];
  const state = {_meta: {last_run_utc: history[0].ts, latest_observation_utc: history[0].ts,
    config: {settings, targets: [{name: "Example"}]}}, Example: {}};
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /Waiting for a fresh update/);
  assert.doesNotMatch(elements.summary.textContent, /operational/);
  assert.match(elements.targets.innerHTML, /Previously available/);
  assert.doesNotMatch(elements.targets.innerHTML, /service-tile ok/);
  page.render([], state, now);
  assert.match(elements.summary.textContent, /unavailable/);
  state._meta.last_run_utc = iso(now);
  state._meta.latest_observation_utc = iso(now);
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /unavailable/);
  history[0].ts = iso(now);
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /All systems operational/);
  delete history[0].results.Example;
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /unavailable/);
});
test("remote names, details, URLs and certificate metadata are escaped", () => {
  const elements = documentStub();
  const attack = '<img src=x onerror="alert(1)">';
  const history = [{ts: iso(now), results: {[attack]: {ok: true, confirmed_up: true, detail: attack, final_url: attack}}}];
  const state = {_meta: {last_run_utc: iso(now), latest_observation_utc: iso(now),
    config: {settings, targets: [{name: attack}]}}, [attack]: {cert_days_left: 5, cert_issuer: attack}};
  page.render(history, state, now);
  assert.doesNotMatch(elements.targets.innerHTML, /<img/);
  assert.match(elements.targets.innerHTML, /&lt;img/);
  assert.doesNotMatch(elements.diagnostics.innerHTML, /<img/);
  assert.match(elements.diagnostics.innerHTML, /&lt;img/);
  assert.equal(page.esc("'&\"<>"), "&#39;&amp;&quot;&lt;&gt;");
});
test("tiles distinguish a confirmed outage, a pending check, and old results", () => {
  assert.deepEqual(page.tileStatus({confirmed_up: false, ok: false}, "fresh"), {tone: "bad", text: "Unavailable"});
  assert.deepEqual(page.tileStatus({confirmed_up: true, ok: false}, "fresh"), {tone: "warn", text: "Checking an issue"});
  assert.deepEqual(page.tileStatus({confirmed_up: true, ok: true}, "delayed"), {tone: "unknown", text: "Previously available"});
  assert.equal(page.tileStatus({confirmed_up: false}, "stale").tone, "unknown");
  assert.equal(page.tileStatus(undefined, "fresh").tone, "unknown");
});
test("hourly history never fills missing checks with green or hides failed checks", () => {
  const history = Array.from({length: 288}, (_, i) => sample(now - (287 - i) * 300000, true));
  assert.ok(page.hourlyHealth(history, ["Example"], settings, now).every(h => h.tone === "ok"));
  assert.ok(page.hourlyHealth(history.slice(-2), ["Example"], settings, now).every(h => h.tone === "unknown"));
  assert.ok(page.hourlyHealth(history, ["Example", "Missing"], settings, now).every(h => h.tone === "unknown"));
  history.at(-1).results.Example.ok = false;
  assert.equal(page.hourlyHealth(history.slice(-1), ["Example"], settings, now).at(-1).tone, "warn");
  const future = sample(now + 300000, false);
  const anchor = {...sample(now, false), boundary_anchor: true};
  assert.equal(page.hourlyHealth([future, anchor], ["Example"], settings, now).at(-1).tone, "unknown");
});
test("unknown cycles clear all prior data and do not leave a healthy history summary", () => {
  const elements = documentStub();
  const history = [sample(now, true)];
  const state = {_meta: {last_run_utc: iso(now), latest_observation_utc: iso(now), config: {settings, targets: [{name: "Example"}]}}};
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /All systems operational/);
  page.showUnavailable();
  assert.match(elements.summary.textContent, /unavailable/);
  assert.doesNotMatch(elements.targets.innerHTML, /Operational/);
  assert.equal(elements.timeline.innerHTML, "");
  assert.equal(elements.diagnostics.innerHTML, "");
  assert.match(elements.incidents.innerHTML, /unavailable/);
  assert.match(elements["history-summary"].innerHTML, /unavailable/);
  assert.match(elements.updated.textContent, /Waiting/);
});
test("failed fetch schedules an automatic retry and a successful retry restores service status", async () => {
  const elements = documentStub();
  const original = {fetch: global.fetch, setTimeout: global.setTimeout, clearTimeout: global.clearTimeout,
    setInterval: global.setInterval, clearInterval: global.clearInterval};
  let retry, seconds;
  global.setTimeout = (callback, delay) => { retry = callback; seconds = delay; return 1; };
  global.clearTimeout = () => {};
  global.setInterval = () => 1;
  global.clearInterval = () => {};
  try {
    global.fetch = async () => { throw new Error("offline"); };
    await page.loadPage();
    assert.match(elements.summary.textContent, /unavailable/);
    assert.equal(seconds, 60000);
    assert.equal(retry, page.loadPage);
    const time = Date.now();
    const history = [sample(time, true)];
    const state = {_meta: {last_run_utc: iso(time), latest_observation_utc: iso(time), config: {settings, targets: [{name: "Example"}]}}};
    global.fetch = async path => ({ok: true, json: async () => path.startsWith("history") ? history : state});
    await retry();
    assert.match(elements.summary.textContent, /All systems operational/);
  } finally {
    Object.assign(global, original);
    delete global.statusClock;
    delete global.statusRefresh;
  }
});
