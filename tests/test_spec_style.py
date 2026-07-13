"""Tests that keep Angl source English-level instead of code-in-English."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.parse import parse
from angl.spec_style import intent_style_findings


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(ROOT, "specs")


def test_current_specs_do_not_leak_implementation_in_intent():
    failures = []
    for name in sorted(os.listdir(SPECS)):
        if not name.endswith(".angl"):
            continue
        path = os.path.join(SPECS, name)
        with open(path) as f:
            spec = parse(f.read())
        for finding in intent_style_findings(spec):
            failures.append(
                f"{name}:{finding['line']}: {finding['term']}: "
                f"{finding['message']}: {finding['text']}"
            )
    assert failures == []


def test_style_checker_accepts_domain_semantics_and_data_contracts():
    spec = parse("""\
name normalize
interface normalize(raw: object) -> object

INTENT
Normalize a raw incident event. Required fields are service and region.
Missing optional fields become null. Invalid records raise an error containing
the invalid field name.

CONTRACT
case: {"service":"api","region":"us"} -> {"service":"api","region":"us"}
""")
    assert intent_style_findings(spec) == []


def test_style_checker_accepts_ordinary_product_language_about_trying():
    spec = parse("""\
name invitation
interface invitation(viewer: object) -> object

INTENT
Invite first-time visitors to try the product.

CONTRACT
case: {"returning":false} -> {"label":"Try now"}
""")
    assert intent_style_findings(spec) == []


def test_style_checker_flags_code_in_english():
    spec = parse("""\
name normalize
interface normalize(raw: object) -> object

INTENT
Import pydantic BaseSettings. Define a class and loop over each field.
Use json.loads for parsing.

CONTRACT
case: {"service":"api"} -> {"service":"api"}
""")
    findings = intent_style_findings(spec)
    terms = {f["term"].lower() for f in findings}
    assert "import" in terms
    assert "pydantic" in terms
    assert "class" in terms
    assert "loop" in terms
    assert "json.loads" in terms


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
