#!/usr/bin/env python3
"""Generate and optionally deliver the Wednesday operations report."""

import argparse
import datetime
import html
import math
import os
import statistics

import monitor


DASHBOARD_URL = "https://m3hr4nn.github.io/mahansco-uptime/"
WEEK = datetime.timedelta(days=7)


def report_boundary(now):
    """Latest Wednesday 09:00 Asia/Tehran boundary at or before now."""
    local = now.astimezone(monitor.TEHRAN)
    boundary = local.replace(hour=9, minute=0, second=0, microsecond=0)
    boundary -= datetime.timedelta(days=(boundary.weekday() - 2) % 7)
    if boundary > local:
        boundary -= WEEK
    return boundary.astimezone(datetime.timezone.utc)


def percentile(values, fraction):
    """Nearest-rank percentile; never average already-aggregated percentiles."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _window(history, names, settings, start, end):
    interval = settings["expected_interval_seconds"]
    expected_per_target = math.ceil((end - start).total_seconds() / interval)
    slots = {name: {} for name in names}
    latencies = []
    endpoints = {name: {"latencies": [], "failures": 0, "samples": 0, "measurement_ids": set()} for name in names}
    latest = None
    for sample in history:
        stamp = monitor.parse_timestamp(sample["ts"]).astimezone(datetime.timezone.utc)
        if sample.get("boundary_anchor") or not start <= stamp < end:
            continue
        latest = stamp if latest is None or stamp > latest else latest
        slot = min(expected_per_target - 1, int((stamp - start).total_seconds() // interval))
        for name in names:
            result = sample["results"].get(name)
            if not result:
                continue
            endpoints[name]["samples"] += 1
            endpoints[name]["measurement_ids"].add(result.get("measurement_id", "legacy-v1"))
            slots[name][slot] = slots[name].get(slot, True) and result.get("ok") is True
            if result.get("ok") is False:
                endpoints[name]["failures"] += 1
            latency = result.get("latency_ms")
            if type(latency) in (int, float) and math.isfinite(latency) and latency >= 0:
                latencies.append(latency)
                endpoints[name]["latencies"].append(latency)
    expected = expected_per_target * len(names)
    observed = sum(len(value) for value in slots.values())
    successful = sum(sum(value.values()) for value in slots.values())
    coverage = observed / expected if expected else 0
    measurement_consistent = all(len(endpoint["measurement_ids"]) <= 1 for endpoint in endpoints.values())
    availability = successful / observed * 100 if observed and coverage >= settings["minimum_coverage"] and measurement_consistent else None
    for name, endpoint in endpoints.items():
        endpoint_slots = slots[name]
        endpoint["observed_slots"] = len(endpoint_slots)
        endpoint["missing_slots"] = expected_per_target - len(endpoint_slots)
        endpoint["median_ms"] = statistics.median(endpoint["latencies"]) if endpoint["latencies"] else None
        endpoint["p95_ms"] = percentile(endpoint["latencies"], .95)
        endpoint["latency_samples"] = len(endpoint.pop("latencies"))
        endpoint["measurement_ids"] = sorted(endpoint["measurement_ids"])
    return {"expected_slots": expected, "observed_slots": observed, "successful_slots": successful,
            "missing_slots": expected - observed, "coverage": coverage, "availability_percent": availability,
            "median_ms": statistics.median(latencies) if latencies else None,
            "p95_ms": percentile(latencies, .95), "latency_samples": len(latencies),
            "measurement_consistent": measurement_consistent,
            "latest_observation_utc": latest.isoformat() if latest else None, "endpoints": endpoints}


def _incident_stats(history, names, start, end):
    incidents = []
    for name in names:
        active = None
        for sample in history:
            stamp = monitor.parse_timestamp(sample["ts"]).astimezone(datetime.timezone.utc)
            if stamp >= end:
                break
            result = sample["results"].get(name)
            if not result or type(result.get("confirmed_up")) is not bool:
                continue
            if result["confirmed_up"] is False:
                incident_start = monitor.parse_timestamp(result.get("incident_started_utc", sample["ts"])).astimezone(datetime.timezone.utc)
                if active is None:
                    active = {"name": name, "start": incident_start, "last": stamp}
                active["last"] = stamp
            elif active:
                incidents.append({**active, "end": stamp, "ongoing": False})
                active = None
        if active:
            incidents.append({**active, "end": active["last"], "ongoing": True})
    overlaps = []
    for incident in incidents:
        clipped_start, clipped_end = max(start, incident["start"]), min(end, incident["end"])
        if clipped_end >= clipped_start and incident["end"] >= start and incident["start"] < end:
            overlaps.append({**incident, "seconds": max(0, int((clipped_end - clipped_start).total_seconds()))})
    longest = max(overlaps, key=lambda item: item["seconds"], default=None)
    return {"count": len(overlaps), "downtime_seconds": sum(item["seconds"] for item in overlaps),
            "longest_seconds": longest["seconds"] if longest else 0,
            "longest_endpoint": longest["name"] if longest else None}


def _change(current, previous):
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous * 100


def generate_summary(history, state, boundary):
    config = state["_meta"]["config"]
    settings = config["settings"]
    names = [target["name"] for target in config["targets"]]
    start, previous_start = boundary - WEEK, boundary - 2 * WEEK
    current = _window(history, names, settings, start, boundary)
    previous = _window(history, names, settings, previous_start, start)
    incidents = _incident_stats(history, names, start, boundary)
    same_measurements = all(current["endpoints"][name]["measurement_ids"] == previous["endpoints"][name]["measurement_ids"]
                            and len(current["endpoints"][name]["measurement_ids"]) == 1 for name in names)
    comparable = (current["measurement_consistent"] and previous["measurement_consistent"] and same_measurements and
                  current["coverage"] >= settings["minimum_coverage"] and
                  previous["coverage"] >= settings["minimum_coverage"] and previous["latency_samples"] > 0)
    for name in names:
        endpoint = current["endpoints"][name]
        prior = previous["endpoints"][name]
        endpoint["median_change_percent"] = _change(endpoint["median_ms"], prior["median_ms"]) if comparable else None
    latest = None
    for sample in history:
        stamp = monitor.parse_timestamp(sample["ts"]).astimezone(datetime.timezone.utc)
        if stamp < boundary and not sample.get("boundary_anchor"):
            latest = sample
    freshness_seconds = int((boundary - monitor.parse_timestamp(latest["ts"]).astimezone(datetime.timezone.utc)).total_seconds()) if latest else None
    latest_results = latest["results"] if latest else {}
    if latest is None or freshness_seconds > settings["stale_after_seconds"]:
        health = "UNKNOWN"
    elif any(latest_results.get(name, {}).get("confirmed_up") is False for name in names):
        health = "OUTAGE"
    elif any(latest_results.get(name, {}).get("ok") is False for name in names):
        health = "DEGRADED"
    elif current["coverage"] < settings["minimum_coverage"] or not current["measurement_consistent"]:
        health = "PARTIAL DATA"
    else:
        health = "HEALTHY"
    observations = []
    if current["coverage"] < settings["minimum_coverage"]:
        observations.append(f"Coverage is {current['coverage'] * 100:.0f}%; treat availability as insufficient, not downtime.")
    if not current["measurement_consistent"]:
        observations.append("A probe definition changed within the window; availability and comparison are withheld.")
    failing = sorted(((name, value["failures"]) for name, value in current["endpoints"].items() if value["failures"]),
                     key=lambda item: item[1], reverse=True)
    if failing:
        observations.append(f"{failing[0][0]} had {failing[0][1]} failed probes; review the assertion and response evidence.")
    slow = sorted(((name, value["median_change_percent"]) for name, value in current["endpoints"].items()
                   if value["median_change_percent"] is not None and value["median_change_percent"] >= 10),
                  key=lambda item: item[1], reverse=True)
    if slow:
        observations.append(f"{slow[0][0]} median HTTP total increased {slow[0][1]:.0f}% week over week.")
    if not observations:
        observations.append("No confirmed incidents or significant comparable slowdowns were found.")
    if freshness_seconds is not None and freshness_seconds > settings["delayed_after_seconds"]:
        observations.append("The last sample was delayed at the report boundary; verify the Actions schedule.")
    return {"schema_version": 1, "boundary_utc": boundary.isoformat(), "timezone": "Asia/Tehran (UTC+03:30)",
            "window_start_utc": start.isoformat(), "window_end_utc": boundary.isoformat(), "health": health,
            "availability_percent": current["availability_percent"], "coverage_percent": current["coverage"] * 100,
            "observed_slots": current["observed_slots"], "expected_slots": current["expected_slots"],
            "missing_slots": current["missing_slots"], "confirmed_downtime_seconds": incidents["downtime_seconds"],
            "incident_count": incidents["count"], "longest_incident_seconds": incidents["longest_seconds"],
            "longest_incident_endpoint": incidents["longest_endpoint"], "median_ms": current["median_ms"],
            "p95_ms": current["p95_ms"], "latency_samples": current["latency_samples"],
            "median_change_percent": _change(current["median_ms"], previous["median_ms"]) if comparable else None,
            "p95_change_percent": _change(current["p95_ms"], previous["p95_ms"]) if comparable else None,
            "comparison_available": comparable, "latest_observation_utc": current["latest_observation_utc"],
            "measurement_consistent": current["measurement_consistent"],
            "freshness_seconds": freshness_seconds, "endpoints": current["endpoints"], "observations": observations[:3]}


def _duration(seconds):
    if seconds < 3600:
        return f"{round(seconds / 60)}m"
    return f"{seconds / 3600:.1f}h"


def format_report(summary, test=False):
    esc = lambda value: html.escape(str(value), quote=True)
    start = monitor.parse_timestamp(summary["window_start_utc"]).astimezone(monitor.TEHRAN)
    end = monitor.parse_timestamp(summary["window_end_utc"]).astimezone(monitor.TEHRAN)
    availability = (f"{summary['availability_percent']:.2f}%" if summary["availability_percent"] is not None
                    else "insufficient coverage")
    change = (f"median {summary['median_change_percent']:+.0f}% · p95 {summary['p95_change_percent']:+.0f}% WoW"
              if summary["comparison_available"] else "WoW unavailable (partial/incomparable coverage)")
    latency = (f"median {round(summary['median_ms'])}ms · p95 {round(summary['p95_ms'])}ms · n={summary['latency_samples']}"
               if summary["median_ms"] is not None else "unavailable · n=0")
    ranked = sorted(summary["endpoints"].items(), key=lambda item: (item[1]["failures"], item[1]["median_change_percent"] or 0), reverse=True)
    notable = []
    for name, value in ranked:
        if value["failures"]:
            notable.append(f"• {esc(name)}: {value['failures']} failed probes")
        elif value["median_change_percent"] is not None and abs(value["median_change_percent"]) >= 10:
            notable.append(f"• {esc(name)}: median {value['median_change_percent']:+.0f}% WoW")
        if len(notable) == 3:
            break
    if not notable:
        notable = ["• No endpoint-level failure or significant comparable slowdown"]
    prefix = "🧪 <b>TEST — Weekly operations report</b>" if test else "📊 <b>Weekly operations report</b>"
    lines = [prefix, f"<i>{start:%d %b %H:%M} – {end:%d %b %H:%M} · Asia/Tehran</i>", "",
             f"<b>Overall:</b> {esc(summary['health'])}",
             f"<b>Availability:</b> {availability} · coverage {summary['coverage_percent']:.0f}% ({summary['observed_slots']}/{summary['expected_slots']} endpoint-slots)",
             f"<b>Incidents:</b> {summary['incident_count']} · confirmed endpoint downtime {_duration(summary['confirmed_downtime_seconds'])} · longest {_duration(summary['longest_incident_seconds'])}",
             f"<b>HTTP total:</b> {latency}", f"<b>Comparison:</b> {change}", "", "<b>Most affected</b>", *notable, "", "<b>Observations</b>"]
    lines.extend(f"• {esc(value)}" for value in summary["observations"])
    lines.extend(["", f'<a href="{DASHBOARD_URL}">Open dashboard</a>'])
    text = "\n".join(lines)
    if len(text) > 4096:
        raise ValueError("weekly report exceeds Telegram's 4096-character limit")
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--send", action="store_true", help="send once and persist its report window")
    action.add_argument("--test-send", action="store_true", help="send one clearly labeled test without advancing the window")
    parser.add_argument("--resend", action="store_true", help="allow a controlled repeat of an already-sent window")
    parser.add_argument("--at", help="generate for the latest boundary at this ISO timestamp")
    args = parser.parse_args(argv)
    if args.resend and not args.send:
        parser.error("--resend requires --send")
    now = monitor.parse_timestamp(args.at) if args.at else monitor.utcnow()
    boundary = report_boundary(now)
    history = monitor.load_json(monitor.HISTORY_PATH, [])
    state = monitor.load_json(monitor.STATE_PATH, {})
    rollup = monitor.load_json(monitor.ROLLUP_PATH, {})
    monitor.validate_data(state, history, rollup)
    summary = generate_summary(history, state, boundary)
    report = format_report(summary, test=args.test_send)
    print(report)
    if not (args.send or args.test_send):
        return 0
    tracking_path = os.path.join(os.path.dirname(monitor.STATE_PATH), "weekly_report.json")
    tracking = monitor.load_json(tracking_path, {"schema_version": 1, "last_sent_boundary_utc": None, "latest": None})
    if args.send and not args.resend and tracking.get("last_sent_boundary_utc") == boundary.isoformat():
        print("Weekly report already sent for this boundary; skipping duplicate.")
        return 0
    if not monitor.telegram(report):
        print("Weekly report delivery failed; report window was not advanced.")
        return 1
    if args.send:
        same_window = tracking.get("last_sent_boundary_utc") == boundary.isoformat()
        tracking.update({"schema_version": 1, "last_sent_boundary_utc": boundary.isoformat(),
                         "last_delivery_utc": monitor.utcnow().isoformat(),
                         "delivery_count": tracking.get("delivery_count", 0) + 1 if same_window else 1,
                         "latest": summary})
        monitor.dump_json_atomic(tracking_path, tracking)
    print("Weekly report accepted by Telegram API." if not args.test_send else "Weekly report TEST accepted by Telegram API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
