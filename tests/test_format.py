"""Unit tests for canonical Angl source formatting.

Run directly: python3 tests/test_format.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.format import format_source
from angl.parse import parse


def _behavior_cases(spec):
    return [
        {
            "input": case["input"],
            "expect": case["expect"],
        }
        for case in spec["cases"]
    ]


def test_formats_legacy_source_as_canonical_v02():
    source = """name fetch_price
interface fetch_price(url: string) -> number

INTENT
Fetch JSON from the given URL and return the price.

CONTRACT
- WHEN the URL serves a price THEN it returns it
  case: http_fixture {"price": 19.99} -> 19.99
"""
    formatted = format_source(source)
    assert formatted.startswith("# Fetch Price\n\n> Boundary: `fetch_price(url: string) -> number`")
    assert "## Purpose\n\nThis chapter defines the public behavior of `fetch_price`." in formatted
    assert "## Behavior\n\nFetch JSON from the given URL and return the price." in formatted
    assert "Fixture `http_fixture`:" in formatted
    assert "Returns:\n```json\n19.99\n```" in formatted
    assert "### The URL serves a price" in formatted
    assert "When the URL serves a price, it returns it." in formatted
    assert _behavior_cases(parse(formatted)) == _behavior_cases(parse(source))


def test_formats_multiple_arguments_with_boundary_names():
    source = """# Pack

The app can ask this chapter to `pack(picnic: object, menu: object) -> object`.
Compile this chapter as `ruby`.

## What this chapter does
Build a packing list.

## Examples
### Hot picnic
When hot picnic rules apply.

Given:
```json
{"temperature_f": 88}
```

And:
```json
{"drink": "lemonade"}
```

Return:
```json
{"count": 2}
```
"""
    formatted = format_source(source)
    assert "> Runs as: `ruby`" in formatted
    assert "Input `picnic`:" in formatted
    assert "Input `menu`:" in formatted
    assert '"temperature_f": 88' in formatted
    assert '"drink": "lemonade"' in formatted
    assert _behavior_cases(parse(formatted)) == _behavior_cases(parse(source))


def test_formats_expected_errors():
    source = """# Normalize

> Boundary: `normalize(request: object) -> object`

## Behavior
Guests must be positive.

## Examples
### Guests is zero
When guests is zero.

Input `request`:
```json
{"guests": 0}
```

Error contains: guests
"""
    formatted = format_source(source)
    assert "Fails with error containing: guests" in formatted
    assert _behavior_cases(parse(formatted)) == _behavior_cases(parse(source))


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
