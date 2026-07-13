"""Mutation checks for the incident-triage contracts.

These do not prove the specs are complete. They prove the current executable
examples catch plausible implementation mistakes instead of merely accepting one
happy-path artifact.

Run directly: python3 tests/test_incident_contract_strength.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.compile import _render_shim
from angl.run import load_program
from angl.verify import verify_spec


def _incident_units():
    return load_program("specs/build_escalation_packet.angl")


def _verify_mutant(unit_name, source):
    units = _incident_units()
    spec = units[unit_name]
    tmpdir = tempfile.mkdtemp()
    try:
        artifact = os.path.join(tmpdir, f"{spec['func']}.py")
        shim = os.path.join(tmpdir, f"{spec['func']}_shim.py")
        with open(artifact, "w") as f:
            f.write(source)
        with open(shim, "w") as f:
            f.write(_render_shim(spec["func"]))
        return verify_spec(spec, {"artifact": artifact, "shim": shim})
    finally:
        shutil.rmtree(tmpdir)


def _assert_contract_rejects(unit_name, source):
    report = _verify_mutant(unit_name, source)
    assert report["passed"] < report["total"], (
        f"{unit_name} contract accepted a known-bad mutant: "
        f"{report['passed']}/{report['total']}"
    )
    return report


def test_normalize_event_contract_catches_alias_precedence_bug():
    report = _assert_contract_rejects("normalize_event", """\
def normalize_event(raw):
    service = raw.get("svc") or raw.get("service")
    if not service:
        raise ValueError("service")
    if "timestamp" not in raw:
        raise ValueError("timestamp")
    if "fingerprint" not in raw:
        raise ValueError("fingerprint")
    out = {
        "event_id": raw.get("event_id"),
        "timestamp": raw["timestamp"],
        "service": service.strip().lower(),
        "region": (raw.get("zone") or raw.get("region") or "global").strip().lower(),
        "customer_impact": raw.get("customer_impact", False),
        "error_budget_burn": raw.get("error_budget_burn", 0),
        "signal_count": raw.get("signal_count", 0),
        "fingerprint": raw["fingerprint"],
    }
    if "minute" in raw:
        out["minute"] = raw["minute"]
    return out
""")
    assert any("Payments-API" in result["case"] for result in report["results"] if not result["pass"])


def test_classify_severity_contract_catches_threshold_off_by_one_bug():
    report = _assert_contract_rejects("classify_severity", """\
def classify_severity(event):
    burn = event.get("error_budget_burn", 0)
    signals = event.get("signal_count", 0)
    if burn < 0 or signals < 0:
        raise ValueError("negative")
    if event.get("customer_impact") and (burn > 0.25 or signals > 10):
        return "sev1"
    if burn > 0.10 or signals > 5:
        return "sev2"
    return "sev3"
""")
    failed = [r["case"] for r in report["results"] if not r["pass"]]
    assert any("0.25" in case for case in failed)
    assert any("10" in case for case in failed)


def test_route_owner_contract_catches_unknown_sev2_paging_bug():
    report = _assert_contract_rejects("route_owner", """\
def route_owner(event, severity):
    service = event.get("service")
    if service == "payments-api":
        team = "payments"
        channel = "sre-oncall" if severity == "sev1" else "team-payments"
    elif service == "search":
        team = "search"
        channel = "sre-oncall" if severity == "sev1" else "team-search"
    else:
        team = "platform"
        channel = "sre-oncall" if severity in ("sev1", "sev2") else "team-platform"
    return {"team": team, "channel": channel, "page": channel == "sre-oncall"}
""")
    assert any('"sev2"' in result["case"] for result in report["results"] if not result["pass"])


def test_dedupe_incident_contract_catches_absolute_time_window_bug():
    report = _assert_contract_rejects("dedupe_incident", """\
def dedupe_incident(event, open_incidents):
    if "minute" not in event:
        raise ValueError("minute")
    for incident in open_incidents:
        if incident.get("fingerprint") != event.get("fingerprint"):
            continue
        if incident.get("region") != event.get("region"):
            continue
        if abs(event["minute"] - incident.get("last_seen_minute", 0)) <= 30:
            return {"duplicate": True, "incident_id": incident.get("incident_id")}
    return {"duplicate": False, "incident_id": None}
""")
    assert any('"minute":90' in result["case"] for result in report["results"] if not result["pass"])


def test_compute_action_contract_catches_duplicate_page_priority_bug():
    report = _assert_contract_rejects("compute_action", """\
def compute_action(owner, dedupe):
    if owner.get("page"):
        return "page-and-create"
    if dedupe.get("duplicate"):
        return "attach-to-existing"
    return "create-ticket"
""")
    assert any("INC-99" in result["case"] for result in report["results"] if not result["pass"])


def test_format_incident_title_contract_catches_missing_uppercase_bug():
    report = _assert_contract_rejects("format_incident_title", """\
def format_incident_title(event, severity):
    return f"{severity} {event.get('service')} ({event.get('region')})"
""")
    assert any('"sev1"' in result["case"] for result in report["results"] if not result["pass"])


def test_build_escalation_packet_contract_catches_ignored_dedupe_bug():
    report = _assert_contract_rejects("build_escalation_packet", """\
def build_escalation_packet(raw_event, open_incidents):
    if "service" not in raw_event and "svc" not in raw_event:
        raise ValueError("service")
    service = (raw_event.get("service") or raw_event.get("svc")).strip().lower()
    region = (raw_event.get("region") or raw_event.get("zone") or "global").strip().lower()
    severity = "sev1" if raw_event.get("customer_impact") and raw_event.get("error_budget_burn", 0) >= 0.25 else "sev3"
    owner = {
        "team": "payments" if service == "payments-api" else "platform",
        "channel": "sre-oncall" if severity == "sev1" else "team-platform",
        "page": severity == "sev1",
    }
    return {
        "event_id": raw_event.get("event_id"),
        "title": f"{severity.upper()} {service} ({region})",
        "service": service,
        "region": region,
        "severity": severity,
        "owner": owner,
        "dedupe": {"duplicate": False, "incident_id": None},
        "action": "page-and-create" if owner["page"] else "create-ticket",
    }
""")
    assert any("INC-77" in result["case"] for result in report["results"] if not result["pass"])


def _run_all():
    failures = []
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return len(failures) == 0


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
