"""Unit tests for angl.parse — no model, no network, pure parsing logic.

Run directly: python3 tests/test_parse.py
Also pytest-discoverable (test_* naming) if pytest is ever added.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.parse import parse, parse_input, parse_expect, _split_top_level

VALID_SPEC = """# name: fetch_price
# interface: fetch_price(url: string) -> number

## INTENT
Fetch JSON from the URL and return the "price" field.

## CONTRACT
- WHEN the URL serves a price THEN it returns it
  case: http_fixture {"price": 19.99} -> 19.99
- WHEN the field is missing THEN it errors
  case: http_fixture {} -> !error contains "price"
"""

CLEAN_SPEC = """name fetch_price
interface fetch_price(url: string) -> number
target node
uses http_client, cache

INTENT
Fetch JSON from the URL and return the "price" field.

CONTRACT
case: http_fixture {"price": 19.99} -> 19.99
"""


def test_parses_name_interface_func():
    spec = parse(VALID_SPEC)
    assert spec["name"] == "fetch_price"
    assert spec["func"] == "fetch_price"
    assert spec["interface"] == "fetch_price(url: string) -> number"


def test_parses_clean_header_syntax():
    spec = parse(CLEAN_SPEC)
    assert spec["name"] == "fetch_price"
    assert spec["func"] == "fetch_price"
    assert spec["interface"] == "fetch_price(url: string) -> number"
    assert spec["target"] == "node"
    assert spec["uses"] == ["http_client", "cache"]
    assert len(spec["cases"]) == 1


def test_parses_markdown_like_chapter_header():
    spec = parse("""\
# Fetch Price

The app can ask this chapter to `fetch_price(url: string) -> number`.
Compile this chapter as `node`.
This chapter uses `http_client` and `cache`.

INTENT
Fetch JSON from the URL and return the price.

CONTRACT
- WHEN the URL serves a price THEN it returns it
  case: http_fixture {"price": 19.99} -> 19.99
""")
    assert spec["name"] == "fetch_price"
    assert spec["func"] == "fetch_price"
    assert spec["interface"] == "fetch_price(url: string) -> number"
    assert spec["target"] == "node"
    assert spec["uses"] == ["http_client", "cache"]


def test_parses_canonical_v02_chapter_anchors():
    spec = parse("""\
# Fetch Price

> Boundary: `fetch_price(url: string) -> number`
> Runs as: `node`
> Uses: `http_client`, `cache`

## Behavior

Return a price from a service response.

## Examples

### Price exists

When the response has a price, return it.

Input `payload`:
```json
{
  "price": 19.99
}
```

Returns:
```json
19.99
```
""")
    assert spec["name"] == "fetch_price"
    assert spec["interface"] == "fetch_price(url: string) -> number"
    assert spec["target"] == "node"
    assert spec["uses"] == ["http_client", "cache"]
    assert spec["intent"] == "Return a price from a service response."
    assert spec["cases"][0]["expect"] == {"returns": 19.99}


def test_parses_canonical_v02_fixture_example():
    spec = parse("""\
# Fetch Price

> Boundary: `fetch_price(url: string) -> number`

## Behavior

Return a price from an HTTP JSON response.

## Examples

### Price exists

When the fixture serves a price, return it.

Fixture `http_fixture`:
```json
{
  "price": 19.99
}
```

Returns:
```json
19.99
```
""")
    assert spec["cases"][0]["input"]["sources"] == [
        {"fixture": "http_fixture", "data": {"price": 19.99}}
    ]


def test_parses_canonical_v02_error_example():
    spec = parse("""\
# Normalize

> Boundary: `normalize(request: object) -> object`

## Behavior

Guests must be positive.

## Examples

### Guests is zero

When guests is zero, reject the request.

Input `request`:
```json
{
  "guests": 0
}
```

Fails with error containing: guests
""")
    assert spec["cases"][0]["expect"] == {"error_contains": "guests"}


def test_unknown_schema_rail_is_rejected():
    bad = """\
# Fetch Price

> Boundary: `fetch_price(payload: object) -> number`
> Function: `fetch_price`

## Behavior
Return the price.

## Examples
### Price exists
When price exists, return it.
Input `payload`:
```json
{"price": 1}
```
Returns:
```json
1
```
"""
    try:
        parse(bad)
        assert False, "expected ValueError for unknown schema rail"
    except ValueError as e:
        assert "unknown schema rail" in str(e)


def test_schema_rail_inside_prose_is_rejected():
    bad = """\
# Fetch Price

> Boundary: `fetch_price(payload: object) -> number`

## Behavior
Return the price.
> Runs as: `node`

## Examples
### Price exists
When price exists, return it.
Input `payload`:
```json
{"price": 1}
```
Returns:
```json
1
```
"""
    try:
        parse(bad)
        assert False, "expected ValueError for misplaced schema rail"
    except ValueError as e:
        assert "must appear before the first section" in str(e)


def test_parses_markdown_like_examples():
    spec = parse("""\
# Fetch Price

The app can ask this chapter to `fetch_price(payload: object) -> number`.

## What this chapter does
Return the price from a payload.

## Examples
### Payload with a price
When the payload includes a price, it returns that price.

Given:
```json
{
  "price": 19.99
}
```

Return:
```json
19.99
```

### Missing price
When the payload has no price, it errors.

Given:
```json
{}
```

Error contains: price
""")
    assert spec["intent"] == "Return the price from a payload."
    assert len(spec["cases"]) == 2
    assert spec["cases"][0]["input"]["sources"] == [{"literal": {"price": 19.99}}]
    assert spec["cases"][0]["expect"] == {"returns": 19.99}
    assert spec["cases"][0]["scenario"] == "When the payload includes a price, it returns that price."
    assert spec["cases"][1]["expect"] == {"error_contains": "price"}


def test_parses_markdown_like_multi_argument_example():
    spec = parse("""\
# Pack

The app can ask this chapter to `pack(picnic: object, menu: object) -> object`.

## What this chapter does
Build a packing list.

## Examples
### Hot lemonade picnic
When the picnic is hot and the drink is lemonade, it packs sunscreen and cooler.

Given:
```json
{
  "temperature_f": 88,
  "kids": true
}
```

And:
```json
{
  "drink": "lemonade"
}
```

Return:
```json
{
  "items": ["water", "sunscreen", "cooler"],
  "count": 3
}
```
""")
    assert len(spec["cases"]) == 1
    assert spec["cases"][0]["input"]["sources"] == [
        {"literal": {"temperature_f": 88, "kids": True}},
        {"literal": {"drink": "lemonade"}},
    ]
    assert spec["cases"][0]["expect"] == {
        "returns": {"items": ["water", "sunscreen", "cooler"], "count": 3}
    }


def test_markdown_example_with_input_but_no_expectation_is_rejected():
    bad = """\
# Normalize

> Boundary: `normalize(raw: object) -> object`

## Behavior

Normalize the input.

## Examples

### Missing expected output

Input `raw`:
```json
{"event_id": "E-1"}
```
"""
    try:
        parse(bad)
        assert False, "expected ValueError for incomplete executable example"
    except ValueError as e:
        assert "input data but no expected return or error" in str(e)


def test_markdown_example_rejects_multi_argument_input_label():
    bad = """\
# Add

> Boundary: `add(left: number, right: number) -> number`

## Behavior

Add two numbers.

## Examples

### Two inputs

Input `left`, `right`:
```json
1
2
```

Returns:
```json
3
```
"""
    try:
        parse(bad)
        assert False, "expected ValueError for an ambiguous input label"
    except ValueError as e:
        assert "must name exactly one argument" in str(e)


def test_target_defaults_to_python():
    spec = parse(VALID_SPEC)
    assert spec["target"] == "python"


def test_unsupported_target_is_rejected():
    bad = ("name x\ninterface x() -> number\ntarget brainfuck\n\n"
           "INTENT\nx\n\nCONTRACT\ncase: -> 1\n")
    try:
        parse(bad)
        assert False, "expected ValueError for unsupported target"
    except ValueError as e:
        assert "unsupported target" in str(e)


def test_target_aliases_are_normalized():
    js = ("name x\ninterface x() -> number\ntarget js\n\n"
          "INTENT\nx\n\nCONTRACT\ncase: -> 1\n")
    ts = ("name x\ninterface x() -> number\ntarget ts\n\n"
          "INTENT\nx\n\nCONTRACT\ncase: -> 1\n")
    asm = ("name x\ninterface x() -> number\ntarget asm\n\n"
           "INTENT\nx\n\nCONTRACT\ncase: -> 1\n")
    assert parse(js)["target"] == "node"
    assert parse(ts)["target"] == "typescript"
    assert parse(asm)["target"] == "assembly"


def test_intent_excludes_headers_and_contract():
    spec = parse(VALID_SPEC)
    assert "price" in spec["intent"]
    assert "case:" not in spec["intent"]
    assert "# name" not in spec["intent"]


def test_parses_two_cases():
    spec = parse(VALID_SPEC)
    assert len(spec["cases"]) == 2
    assert spec["cases"][0]["expect"] == {"returns": 19.99}
    assert spec["cases"][1]["expect"] == {"error_contains": "price"}


def test_zero_cases_is_a_hard_error():
    no_cases = "# name: x\n# interface: x() -> number\n\n## INTENT\nnothing\n"
    try:
        parse(no_cases)
        assert False, "expected ValueError for zero cases"
    except ValueError as e:
        assert "zero contract cases" in str(e)


def test_missing_interface_is_a_hard_error():
    no_interface = "# name: x\n\n## INTENT\nx\n\n## CONTRACT\ncase: 1 -> 1\n"
    try:
        parse(no_interface)
        assert False, "expected ValueError for missing interface"
    except ValueError as e:
        assert "missing 'interface' header" in str(e)


def test_missing_behavior_is_a_hard_error():
    no_behavior = "# name: x\n# interface: x() -> number\n\n## CONTRACT\ncase: -> 1\n"
    try:
        parse(no_behavior)
        assert False, "expected ValueError for missing behavior"
    except ValueError as e:
        assert "missing behavior/intent section" in str(e)


def test_malformed_interface_name_is_rejected():
    bad = ('# name: x\n# interface: fetch price(url) -> number\n\n'
           '## INTENT\nx\n\n## CONTRACT\ncase: "a" -> 1\n')
    try:
        parse(bad)
        assert False, "expected ValueError for non-identifier func name"
    except ValueError as e:
        assert "not a valid Python identifier" in str(e)


def test_uses_parses_comma_separated_deps():
    spec_text = ('# name: checkout\n# interface: checkout(a) -> number\n'
                 '# uses: fetch_price, other_unit\n\n## INTENT\nx\n\n'
                 '## CONTRACT\ncase: 1 -> 1\n')
    spec = parse(spec_text)
    assert spec["uses"] == ["fetch_price", "other_unit"]


def test_multi_arg_case_is_comma_separated_not_a_list():
    parsed = parse_input('"widget" , 3 , 9.99')
    assert parsed["sources"] == [
        {"literal": "widget"}, {"literal": 3}, {"literal": 9.99}]


def test_empty_input_means_zero_args():
    parsed = parse_input("")
    assert parsed["sources"] == []


def test_single_bracket_literal_is_one_list_arg_not_three():
    parsed = parse_input('[1, 2, 3]')
    assert parsed["sources"] == [{"literal": [1, 2, 3]}]


def test_fixture_and_literal_compose_in_order():
    parsed = parse_input('http_fixture {"price": 2.50} , 4')
    assert parsed["sources"] == [
        {"fixture": "http_fixture", "data": {"price": 2.50}}, {"literal": 4}]


def test_comma_inside_plain_string_literal_does_not_split():
    # Regression test for a real bug: _split_top_level used to only track
    # {}/[] depth, not quote state, so a comma inside a bare string literal
    # (not wrapped in an object/array) split the string in half and crashed.
    parsed = parse_input('"hello, world"')
    assert parsed["sources"] == [{"literal": "hello, world"}]


def test_escaped_quote_inside_string_with_comma():
    parsed = parse_input('"say \\"hi, there\\""')
    assert parsed["sources"] == [{"literal": 'say "hi, there"'}]


def test_error_contains_expectation():
    assert parse_expect('!error contains "price"') == {"error_contains": "price"}


def test_returns_expectation():
    assert parse_expect('{"a": 1}') == {"returns": {"a": 1}}


def test_split_top_level_ignores_commas_in_brackets():
    assert _split_top_level('{"a": 1, "b": 2} , 4', ',') == ['{"a": 1, "b": 2} ', ' 4']


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
