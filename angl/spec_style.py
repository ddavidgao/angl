"""Style checks for keeping .angl intent semantic instead of procedural."""
import re


IMPLEMENTATION_SMELLS = [
    (re.compile(r"\bimport\b", re.I), "imports belong in compiler output, not source intent"),
    (re.compile(r"\bclass\b|\bsubclass\b|\bmethod\b|\bfunction\b", re.I),
     "target-language structure belongs in generated code, not source intent"),
    (re.compile(r"\bloop\b|\biterate\b|\bfor each\b", re.I),
     "iteration strategy belongs in generated code, not source intent"),
    (re.compile(r"\btry\s*/\s*except\b|\btry\s*:|\bexcept\s*:", re.I),
     "error-handling mechanics belong in generated code, not source intent"),
    (re.compile(r"\bDijkstra\b|\bbreadth[- ]first\b|\bFIFO\b", re.I),
     "algorithm names belong only in source when the algorithm is itself the requirement"),
    (re.compile(r"\bBaseSettings\b|\bBaseModel\b|\bpydantic\b|\bpydantic_settings\b", re.I),
     "library APIs belong in compiler prompts or demos, not source intent"),
    (re.compile(r"\bjson\.loads\b|\bserde_json\b|\bfmt\.Sprintf\b|\bbase64\.", re.I),
     "runtime APIs belong in compiler prompts, not source intent"),
]


def intent_style_findings(spec):
    """Return style findings for implementation details leaked into INTENT.

    This is deliberately separate from parsing. A spec can be syntactically
    valid while still being poor Angl because the English is secretly code.
    """
    findings = []
    intent = spec.get("intent", "")
    for line_no, line in enumerate(intent.splitlines(), 1):
        for pattern, message in IMPLEMENTATION_SMELLS:
            match = pattern.search(line)
            if match:
                findings.append({
                    "line": line_no,
                    "term": match.group(0),
                    "message": message,
                    "text": line,
                })
    return findings
