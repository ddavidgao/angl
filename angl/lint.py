"""Lint Angl source for the v0.2 writing standard."""
import argparse
import os
import re
import sys

from .parse import parse
from .spec_style import intent_style_findings


def lint_source(text, path="<string>"):
    findings = []
    try:
        spec = parse(text)
    except ValueError as e:
        return [{
            "path": path,
            "severity": "error",
            "message": str(e),
        }]

    for finding in intent_style_findings(spec):
        findings.append({
            "path": path,
            "severity": "warning",
            "message": finding["message"],
            "line": finding["line"],
            "term": finding["term"],
        })

    findings += _canonical_findings(text, path)
    findings += _pseudo_schema_findings(text, path)
    findings += _size_findings(spec, path)
    return findings


def lint_file(path):
    with open(path) as f:
        return lint_source(f.read(), path=path)


def _canonical_findings(text, path):
    findings = []
    if _has_line(text, r"(#\s*)?name\s*(:|\s+)"):
        findings.append(_warning(path, "use '# Chapter Title' plus '> Boundary:' instead of name headers"))
    if _has_line(text, r"(#\s*)?interface\s*(:|\s+)"):
        findings.append(_warning(path, "use '> Boundary:' instead of interface headers"))
    if _has_line(text, r"(#\s*)?target\s*(:|\s+)"):
        findings.append(_warning(path, "use '> Runs as:' instead of target headers"))
    if _has_line(text, r"(#\s*)?uses\s*(:|\s+)"):
        findings.append(_warning(path, "use '> Uses:' instead of uses headers"))
    if "## What this chapter does" in text:
        findings.append(_warning(path, "use '## Behavior' instead of '## What this chapter does'"))
    if "\nGiven:" in text or "\nAnd:" in text:
        findings.append(_warning(path, "use named 'Input `arg`:' blocks instead of Given/And"))
    if "\nReturn:" in text:
        findings.append(_warning(path, "use 'Returns:' instead of 'Return:'"))
    if "\nError contains:" in text:
        findings.append(_warning(path, "use 'Fails with error containing:' instead of 'Error contains:'"))
    return findings


def _has_line(text, pattern):
    return re.search(rf"(?m)^\s*{pattern}", text) is not None


def _pseudo_schema_findings(text, path):
    findings = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if _is_schema_or_example_line(stripped):
            continue
        if re.match(
            r"(?i)^(function|target|language|runtime|dependency|dependencies|"
            r"files?|create files?|implementation|algorithm|class|method|tests?)\s*:",
            stripped,
        ):
            findings.append({
                "path": path,
                "severity": "warning",
                "line": line_no,
                "message": (
                    "schema-like prose is ambiguous; use a supported schema rail "
                    "or rewrite this as natural language"
                ),
                "term": stripped.split(":", 1)[0],
            })
        if re.search(r"(?i)\b(create|write|generate|make)\b.*\.(py|js|ts|go|rs|rb)\b", stripped):
            findings.append({
                "path": path,
                "severity": "warning",
                "line": line_no,
                "message": (
                    "source should not name generated implementation files; "
                    "promote public behavior to a chapter and let the manifest "
                    "report generated files"
                ),
                "term": "generated file",
            })
    return findings


def _is_schema_or_example_line(stripped):
    if stripped.startswith(("# ", "## ", "### ", "- ")):
        return True
    if stripped.startswith("> "):
        return True
    if re.match(r"Input\s+`[^`]+`:", stripped):
        return True
    if re.match(r"Fixture\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?:", stripped):
        return True
    if stripped in {"Returns:", "Return:", "Given:", "And:"}:
        return True
    if stripped.startswith(("Fails with error containing:", "Error contains:")):
        return True
    if stripped.startswith("case:"):
        return True
    return False


def _size_findings(spec, path):
    findings = []
    cases = len(spec.get("cases", []))
    intent_lines = len([line for line in spec.get("intent", "").splitlines() if line.strip()])
    if cases > 12:
        findings.append(_warning(path, f"chapter has {cases} examples; consider splitting it"))
    if intent_lines > 80:
        findings.append(_warning(path, f"behavior prose has {intent_lines} non-empty lines; consider splitting it"))
    return findings


def _warning(path, message):
    return {"path": path, "severity": "warning", "message": message}


def format_finding(finding):
    location = finding["path"]
    if finding.get("line"):
        location += f":{finding['line']}"
    term = f" ({finding['term']})" if finding.get("term") else ""
    return f"{location}: {finding['severity']}: {finding['message']}{term}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="lint .angl source")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    findings = []
    for path in args.paths:
        findings += lint_file(path)
    for finding in findings:
        print(format_finding(finding))
    has_errors = any(f["severity"] == "error" for f in findings)
    has_warnings = any(f["severity"] == "warning" for f in findings)
    return 1 if has_errors or (args.strict and has_warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
