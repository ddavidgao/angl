"""Canonical formatter for Angl source.

The formatter is intentionally conservative: it rewrites valid source into the
v0.2 reading shape without changing parsed behavior. It is not a renderer for
marketing pages. Its output is meant to be checked in.
"""
import argparse
import json
import os
import re
import sys

from .parse import parse


def format_source(text):
    spec = parse(text)
    lines = [
        f"# {_title(spec['name'])}",
        "",
        f"> Boundary: `{spec['interface']}`",
    ]
    if spec.get("target", "python") != "python":
        lines.append(f"> Runs as: `{spec['target']}`")
    if spec.get("uses"):
        lines.append("> Uses: " + ", ".join(f"`{dep}`" for dep in spec["uses"]))
    lines += [
        "",
        "## Purpose",
        "",
        f"This chapter defines the public behavior of `{spec['func']}`.",
        "",
        "## Behavior",
        "",
    ]
    intent = _clean_intent(spec.get("intent", ""), spec["func"])
    lines += _wrapped_blocks(intent or "No behavior prose has been written yet.")
    lines += ["", "## Examples", ""]

    arg_names = _arg_names(spec["interface"])
    for idx, case in enumerate(spec["cases"], 1):
        title = _example_title(case, idx)
        lines += [f"### {title}", ""]
        scenario = _scenario_sentence(case)
        if scenario:
            lines += _wrapped_blocks(scenario)
            lines.append("")
        lines += _format_inputs(case["input"]["sources"], arg_names)
        lines += _format_expect(case["expect"])
        if idx != len(spec["cases"]):
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_file(path, write=False):
    with open(path) as f:
        original = f.read()
    formatted = format_source(original)
    if write and formatted != original:
        with open(path, "w") as f:
            f.write(formatted)
    return formatted


def _format_inputs(sources, arg_names):
    lines = []
    for idx, source in enumerate(sources):
        if "fixture" in source:
            lines += [
                f"Fixture `{source['fixture']}`:",
                "```json",
                _json_block(source.get("data")),
                "```",
                "",
            ]
            continue
        name = arg_names[idx] if idx < len(arg_names) else f"arg{idx + 1}"
        lines += [
            f"Input `{name}`:",
            "```json",
            _json_block(source.get("literal")),
            "```",
            "",
        ]
    return lines


def _format_expect(expect):
    if "error_contains" in expect:
        return [f"Fails with error containing: {expect['error_contains']}"]
    return [
        "Returns:",
        "```json",
        _json_block(expect.get("returns")),
        "```",
    ]


def _json_block(value):
    return json.dumps(value, indent=2, sort_keys=False)


def _arg_names(interface):
    m = re.match(r"\s*[a-zA-Z_][a-zA-Z0-9_]*\((.*)\)\s*->", interface)
    if not m:
        return []
    args = m.group(1).strip()
    if not args:
        return []
    names = []
    for arg in _split_args(args):
        name = arg.split(":", 1)[0].strip()
        if name:
            names.append(name)
    return names


def _split_args(args):
    parts = []
    cur = ""
    depth = 0
    for ch in args:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _example_title(case, idx):
    scenario = case.get("scenario", "").strip()
    if not scenario:
        return f"Example {idx}"
    split = _split_when_then(scenario) or _split_when_comma(scenario)
    title = split[0] if split else scenario
    title = re.sub(r"^when\s+", "", title, flags=re.I)
    title = re.sub(r"[.!?]\s*$", "", title)
    return title[:1].upper() + title[1:]


def _scenario_sentence(case):
    scenario = case.get("scenario", "").strip()
    if not scenario:
        return ""
    split = _split_when_then(scenario)
    if not split:
        return scenario
    situation, promise = split
    situation = re.sub(r"^when\s+", "", situation, flags=re.I).strip()
    promise = promise.strip()
    return f"When {situation}, {promise[:1].lower() + promise[1:]}."


def _split_when_then(text):
    match = re.match(r"\s*when\s+(.+?)\s+then\s+(.+?)\s*[.!?]?\s*$", text, re.I)
    if not match:
        return None
    return match.group(1), match.group(2)


def _split_when_comma(text):
    match = re.match(r"\s*when\s+(.+?),\s+(.+?)\s*[.!?]?\s*$", text, re.I)
    if not match:
        return None
    return match.group(1), match.group(2)


def _clean_intent(intent, func):
    generic = f"This chapter defines the public behavior of `{func}`."
    text = intent.strip()
    if text == generic:
        return ""
    if text.startswith(generic + " "):
        return text[len(generic):].strip()
    if text.startswith(generic + "\n"):
        return text[len(generic):].strip()
    return text


def _title(name):
    return " ".join(part.capitalize() for part in name.split("_"))


def _wrapped_blocks(text, width=88):
    import textwrap

    lines = []
    paragraph = []

    def flush_paragraph():
        if not paragraph:
            return
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(textwrap.wrap(" ".join(paragraph), width=width))
        lines.append("")
        paragraph.clear()

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            flush_paragraph()
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            lines.append(stripped)
            continue
        paragraph.append(stripped)
    flush_paragraph()
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="format .angl source as canonical v0.2")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    for path in args.paths:
        formatted = format_file(path, write=args.write)
        if not args.write:
            if len(args.paths) > 1:
                print(f"--- {os.path.relpath(path)} ---")
            print(formatted, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
