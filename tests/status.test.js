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
  assert.match(elements.summary.textContent, /STALE/);
  assert.doesNotMatch(elements.summary.textContent, /operational/);
  assert.match(elements.targets.innerHTML, /LAST OBSERVED UP/);
  page.render([], state, now);
  assert.match(elements.summary.textContent, /UNKNOWN/);
  state._meta.last_run_utc = iso(now);
  state._meta.latest_observation_utc = iso(now);
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /UNKNOWN/);
  history[0].ts = iso(now);
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /All systems operational/);
  delete history[0].results.Example;
  page.render(history, state, now);
  assert.match(elements.summary.textContent, /UNKNOWN/);
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
  assert.equal(page.esc("'&\"<>"), "&#39;&amp;&quot;&lt;&gt;");
});
