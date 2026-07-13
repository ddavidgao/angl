"""Parse a .angl source file into a spec dict.

.angl format (v1):

    name <identifier>
    interface <func>(<args>) -> <type>         the TYPED BOUNDARY other units see
    target <python|node|ruby|go|rust|typescript|bundle|assembly>
                                                  optional code generation target
    uses <unit>, <unit>                        optional dependencies

    INTENT
    <free english>                               fed to the compiler; GUIDES

    CONTRACT
    - <EARS sentence>                            human-readable, machine ignores
      case: <input> -> <expect>                  data-only; PINS the meaning

Only `case:` lines are executed. No programming language appears in the source:
inputs/expectations are data, not code.

Legacy `# name:`, `# interface:`, `# target:`, `# uses:`, and `## INTENT`
headers are still accepted so older specs keep working.

Markdown-like chapter headers are accepted too:

    # Fetch Price
    The app can ask this chapter to `fetch_price(url: string) -> number`.
    Compile this chapter as `python`.
    This chapter uses `http_client` and `cache`.

    ## What this chapter does
    <free english>

    ## Examples
    ### Happy path
    When the URL serves a price, it returns it.

    Given:
    ```json
    {"price": 19.99}
    ```

    Return:
    ```json
    19.99
    ```

Canonical v0.2 chapter anchors are accepted too:

    # Fetch Price

    > Boundary: `fetch_price(url: string) -> number`
    > Runs as: `python`
    > Uses: `http_client`

    ## Purpose
    <why this chapter exists>

    ## Behavior
    <what it promises>

    ## Examples
    ### Happy path
    When the URL serves a price, it returns it.

    Fixture `http_fixture`:
    ```json
    {"price": 19.99}
    ```

    Returns:
    ```json
    19.99
    ```
"""
import json
import re

# Fixtures are toolchain-provided (see fixtures.py). A source that begins with a
# known fixture name means "let the runner build the argument(s)".
KNOWN_FIXTURES = {"http_fixture", "postgres_fixture"}


def _directive(name, stripped):
    return re.match(rf"#?\s*{name}(?::|\s+)\s*(.+)$", stripped)


def parse(text):
    name = interface = None
    target = "python"
    uses = []
    intent_lines = []
    cases = []
    section = None
    example = None
    pending_data = None
    data_lines = []
    last_scenario = None

    for line in text.splitlines():
        stripped = line.strip()

        if pending_data:
            if stripped.startswith("```") and not data_lines:
                continue
            if stripped == "```":
                data = json.loads("\n".join(data_lines))
                if pending_data["kind"] == "input":
                    example["inputs"].append(data)
                elif pending_data["kind"] == "fixture":
                    example["inputs"].append({
                        "fixture": pending_data["name"],
                        "data": data,
                    })
                elif pending_data["kind"] == "returns":
                    example["expect"] = {"returns": data}
                pending_data = None
                data_lines = []
            else:
                data_lines.append(line)
            continue

        if stripped.startswith(">"):
            anchor = _blockquote_anchor(stripped)
            if not anchor:
                raise ValueError(
                    "invalid schema rail; blockquote lines are reserved for "
                    "machine schema and must look like '> Boundary: `...`'"
                )
            key, value = anchor
            if key not in {"boundary", "runs as", "uses"}:
                raise ValueError(
                    f"unknown schema rail {key!r}; supported rails are "
                    "Boundary, Runs as, and Uses"
                )
            if section is not None:
                raise ValueError(
                    f"schema rail {key!r} must appear before the first section; "
                    "prose sections are natural language, not schema"
                )
            if key == "boundary":
                interface = value
                continue
            if key == "runs as":
                target = _normalize_target(value)
                continue
            if key == "uses":
                uses += _parse_uses_value(value)
                continue

        if section is None:
            m = _directive("name", stripped)
            if m:
                name = m.group(1).strip()
                continue
            m = _directive("interface", stripped)
            if m:
                interface = m.group(1).strip()
                continue
            m = _directive("target", stripped)
            if m:
                target = _normalize_target(m.group(1).strip())
                continue
            m = _directive("uses", stripped)
            if m:
                uses += [d.strip() for d in m.group(1).split(",") if d.strip()]
                continue
            if stripped.startswith("# ") and name is None:
                name = _slugify_name(stripped[2:].strip())
                continue
            if interface is None:
                natural_interface = _natural_interface(stripped)
                if natural_interface:
                    interface = natural_interface
                    continue
            natural_target = _natural_target(stripped)
            if natural_target:
                target = _normalize_target(natural_target)
                continue
            natural_uses = _natural_uses(stripped)
            if natural_uses:
                uses += natural_uses
                continue

        if stripped.startswith("## "):
            if section == "EXAMPLES":
                _finish_example(example, cases)
                example = None
            section = _normalize_section(stripped[3:].strip())
            continue
        if stripped in {"INTENT", "CONTRACT", "EXAMPLES"}:
            if section == "EXAMPLES":
                _finish_example(example, cases)
                example = None
            section = stripped
            continue

        if stripped.startswith("case:"):
            if section == "EXAMPLES":
                _finish_example(example, cases)
                example = None
            case = parse_case(stripped[len("case:"):].strip())
            if last_scenario:
                case["scenario"] = last_scenario
            cases.append(case)
            continue

        if section == "INTENT" and stripped:
            intent_lines.append(stripped)
            continue

        if section == "CONTRACT" and stripped.startswith("- "):
            last_scenario = stripped[2:].strip()
            continue

        if section == "EXAMPLES":
            if stripped.startswith("### "):
                _finish_example(example, cases)
                example = {"title": stripped[4:].strip(), "scenario": "", "inputs": [], "expect": None}
                continue
            if example is None:
                continue
            if stripped.startswith("Given:") or stripped.startswith("And:"):
                pending_data = {"kind": "input"}
                data_lines = []
                continue
            if re.match(r"Input\s+`[^`]+`:", stripped):
                pending_data = {"kind": "input"}
                data_lines = []
                continue
            if stripped.startswith("Input"):
                raise ValueError(
                    "example input labels must name exactly one argument, for "
                    "example 'Input `quantity`:'; repeat an Input block for "
                    "each positional argument"
                )
            m = re.match(r"Fixture\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?:", stripped)
            if m:
                pending_data = {"kind": "fixture", "name": m.group(1)}
                data_lines = []
                continue
            if stripped.startswith(("Return:", "Returns:", "It returns:")):
                pending_data = {"kind": "returns"}
                data_lines = []
                continue
            if stripped.startswith("Error contains:"):
                example["expect"] = {"error_contains": stripped.split(":", 1)[1].strip().strip('"')}
                continue
            m = re.match(r"Fails with error containing:\s*(.+)$", stripped)
            if m:
                example["expect"] = {"error_contains": m.group(1).strip().strip('"')}
                continue
            if stripped:
                example["scenario"] = (example["scenario"] + " " + stripped).strip()

    if section == "EXAMPLES":
        _finish_example(example, cases)

    if not name:
        raise ValueError(
            "spec missing 'name' header; use 'name <identifier>' or legacy "
            "'# name: <identifier>'"
        )
    if not interface:
        raise ValueError(
            f"spec {name!r} missing 'interface' header; every unit needs a "
            "typed boundary for composition and generated shims"
        )
    if not intent_lines:
        raise ValueError(
            f"spec {name!r} missing behavior/intent section; every unit needs "
            "English intent for the compiler. Use '## Behavior' in chapter "
            "syntax or legacy 'INTENT'."
        )
    if not cases:
        raise ValueError(
            f"spec {name!r} has zero contract cases — a unit with no cases can "
            "never distinguish a correct compile from a broken one, and must "
            "never report green (check for a 'case:' line that failed to parse, "
            "e.g. it must be on its own line, not merged with the '- ' bullet)"
        )

    # Function under test: the identifier before '(' in the interface line.
    func = interface.split("(")[0].strip() if interface else name
    if not func.isidentifier():
        raise ValueError(
            f"interface function name {func!r} (parsed from interface "
            f"{interface!r}) is not a valid Python identifier; check for a "
            "typo like a stray space"
        )
    if target not in {
        "python",
        "node",
        "ruby",
        "go",
        "rust",
        "typescript",
        "bundle",
        "assembly",
    }:
        raise ValueError(
            f"unsupported target {target!r}; supported targets are python, "
            "node, ruby, go, rust, typescript, bundle, and assembly"
        )

    return {
        "name": name,
        "func": func,
        "interface": interface,
        "target": target,
        "uses": uses,
        "intent": "\n".join(intent_lines),
        "cases": cases,
    }


def parse_case(s):
    """Parse '<input> -> <expect>' into {input, expect, raw}."""
    if "->" not in s:
        raise ValueError(f"case missing '->': {s!r}")
    left, right = (p.strip() for p in s.split("->", 1))
    return {"raw": s, "input": parse_input(left), "expect": parse_expect(right)}


def _normalize_section(title):
    normalized = title.strip().upper()
    aliases = {
        "PURPOSE": "PURPOSE",
        "WHAT THIS CHAPTER DOES": "INTENT",
        "WHAT IT DOES": "INTENT",
        "BEHAVIOR": "INTENT",
        "EXAMPLES": "EXAMPLES",
        "CONTRACT": "CONTRACT",
        "INTENT": "INTENT",
    }
    return aliases.get(normalized, normalized)


def _finish_example(example, cases):
    if not example:
        return
    if example["inputs"] and not example["expect"]:
        title = example["title"] or "(untitled example)"
        raise ValueError(
            f"example {title!r} has input data but no expected return or error"
        )
    if not example["inputs"] and not example["expect"]:
        return
    left_parts = []
    for value in example["inputs"]:
        if isinstance(value, dict) and "fixture" in value:
            data = json.dumps(value.get("data"), separators=(",", ":"))
            left_parts.append(f"{value['fixture']} {data}")
        else:
            left_parts.append(json.dumps(value, separators=(",", ":")))
    left = " , ".join(left_parts)
    if "error_contains" in example["expect"]:
        right = f'!error contains "{example["expect"]["error_contains"]}"'
    else:
        right = json.dumps(example["expect"]["returns"], separators=(",", ":"))
    raw = f"{left} -> {right}"
    case = parse_case(raw)
    scenario = example["scenario"] or example["title"]
    case["scenario"] = scenario
    cases.append(case)


def _normalize_target(target):
    aliases = {
        "js": "node",
        "javascript": "node",
        "ts": "typescript",
        "asm": "assembly",
    }
    return aliases.get(target.lower(), target.lower())


def _slugify_name(title):
    name = re.sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_")
    return name


def _natural_interface(stripped):
    # Keep the machine boundary explicit, but let the source read as a sentence.
    # Example: The app can ask this chapter to `foo(x: object) -> object`.
    m = re.search(r"`([^`]+?\([^`]*\)\s*->\s*[^`]+)`", stripped)
    return m.group(1).strip() if m else None


def _natural_target(stripped):
    m = re.search(r"\b(?:compile|build)\b.*\bas\s+`?([a-zA-Z]+)`?", stripped, re.I)
    return m.group(1).strip() if m else None


def _natural_uses(stripped):
    if "uses" not in stripped.lower() and "builds on" not in stripped.lower():
        return []
    backticked = re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", stripped)
    if backticked:
        return backticked
    m = re.search(r"(?:uses|builds on)\s+(.+)$", stripped, re.I)
    if not m:
        return []
    return _parse_uses_value(m.group(1))


def _blockquote_anchor(stripped):
    m = re.match(r">\s*([^:]+):\s*(.+)$", stripped)
    if not m:
        return None
    key = m.group(1).strip().lower()
    value = m.group(2).strip()
    return key, _strip_optional_code(value)


def _strip_optional_code(value):
    value = value.strip()
    if re.fullmatch(r"`[^`]+`", value):
        return value[1:-1].strip()
    return value


def _parse_uses_value(value):
    value = value.strip()
    backticked = re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", value)
    if backticked:
        return backticked
    deps = re.sub(r"[.]", "", value)
    deps = deps.replace(" and ", ", ")
    return [dep.strip() for dep in deps.split(",") if dep.strip()]


def parse_input(left):
    """A comma-separated list of argument sources (top-level commas only).

    Each source is either a fixture invocation ('http_fixture {json}') or a JSON
    literal. Fixtures contribute the arg(s) they yield; literals contribute
    themselves. Order is preserved, so:

        http_fixture {"price": 2.50} , 4   ->  args = [<url>, 4]
        [1, 2, 3]                          ->  args = [[1, 2, 3]]  (one list arg)
    """
    if not left:
        return {"sources": []}
    sources = [_parse_source(seg.strip()) for seg in _split_top_level(left, ",")]
    return {"sources": sources}


def _parse_source(seg):
    head = seg.split(None, 1)
    if head and head[0] in KNOWN_FIXTURES:
        data = json.loads(head[1]) if len(head) > 1 and head[1].strip() else None
        return {"fixture": head[0], "data": data}
    return {"literal": json.loads(seg)}


def parse_expect(right):
    """Right side of a case.

    Error form:   !error contains "price"   -> expect a raised error, substring match
    Return form:  10.0                       -> expect this JSON value returned
    """
    m = re.match(r'!error\s+contains\s+"(.*)"$', right)
    if m:
        return {"error_contains": m.group(1)}
    return {"returns": json.loads(right)}


def _split_top_level(s, sep):
    """Split on `sep` only at brace/bracket depth 0 and outside any JSON string
    literal, so commas inside JSON objects/arrays/strings are all safe."""
    parts, depth, cur, in_string, escaped = [], 0, "", False, False
    for ch in s:
        if escaped:
            escaped = False
        elif in_string:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1

        if ch == sep and depth == 0 and not in_string:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts
