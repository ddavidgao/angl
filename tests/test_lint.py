"""Unit tests for Angl source linting.

Run directly: python3 tests/test_lint.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.lint import lint_source


CANONICAL = """# Normalize

> Boundary: `normalize(request: object) -> object`

## Purpose

Prepare request data for planning.

## Behavior

Guests must be positive.

## Examples

### Guests is valid

When guests is valid, return the request.

Input `request`:
```json
{
  "guests": 2
}
```

Returns:
```json
{
  "guests": 2
}
```
"""


def test_lint_accepts_canonical_source():
    assert lint_source(CANONICAL) == []


def test_lint_warns_on_legacy_headers_and_example_words():
    findings = lint_source("""name normalize
interface normalize(request: object) -> object

INTENT
Guests must be positive.

CONTRACT
- WHEN guests is valid THEN return the request
  case: {"guests": 2} -> {"guests": 2}
""")
    messages = [finding["message"] for finding in findings]
    assert "use '# Chapter Title' plus '> Boundary:' instead of name headers" in messages
    assert "use '> Boundary:' instead of interface headers" in messages


def test_lint_reports_parse_errors_as_errors():
    findings = lint_source("# Empty\n")
    assert findings[0]["severity"] == "error"
    assert "missing 'interface' header" in findings[0]["message"]


def test_lint_flags_implementation_leakage():
    findings = lint_source(CANONICAL.replace("Guests must be positive.", "Import pydantic and define a class."))
    messages = [finding["message"] for finding in findings]
    assert "imports belong in compiler output, not source intent" in messages
    assert "target-language structure belongs in generated code, not source intent" in messages


def test_lint_flags_schema_like_prose():
    findings = lint_source(CANONICAL.replace(
        "Guests must be positive.",
        "Target: Python\nFunction: normalize\nGuests must be positive.",
    ))
    messages = [finding["message"] for finding in findings]
    assert messages.count(
        "schema-like prose is ambiguous; use a supported schema rail or rewrite this as natural language"
    ) == 2


def test_lint_flags_generated_file_instructions():
    findings = lint_source(CANONICAL.replace(
        "Guests must be positive.",
        "Create normalize.py and helpers.ts for this behavior.",
    ))
    messages = [finding["message"] for finding in findings]
    assert (
        "source should not name generated implementation files; promote public behavior "
        "to a chapter and let the manifest report generated files"
    ) in messages


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
