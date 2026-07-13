"""Judge-style verifier.

Runs each contract case against the build's judge adapter as a black box
(subprocess + JSON over pipes). Never imports the implementation, so the same
contract works regardless of the target language the compiler chose.
"""
import json
import os
import re
import subprocess
import sys

from .fixtures import resolve_input

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _venv_python():
    """The pinned venv (requirements.txt) if present, so artifacts that use a
    3rd-party library the compiler chose (e.g. requests) can actually run.
    Falls back to this process's interpreter for stdlib-only artifacts."""
    for candidate in ("bin/python3", "Scripts/python.exe"):
        path = os.path.join(REPO_ROOT, ".venv", candidate)
        if os.path.exists(path):
            return path
    return sys.executable


def verify_spec(spec, build, timeout=15):
    judge_adapter = build.get("judge_adapter") or build["shim"]
    results = [_run_case(judge_adapter, case, timeout) for case in spec["cases"]]
    passed = sum(1 for r in results if r["pass"])
    return {"passed": passed, "total": len(results), "results": results}


def _run_case(shim, case, timeout):
    with resolve_input(case["input"]) as args:
        try:
            proc = subprocess.run(
                # -B: Angl rewrites the SAME artifact filename on every
                # recompile. Some Python builds (Apple's system Python)
                # cache bytecode by absolute path with second-granularity
                # mtime checks; two writes within the same wall-clock second
                # produce a false cache hit and silently run STALE code. -B
                # (dont_write_bytecode) sidesteps this entirely. Confirmed by
                # direct repro: without it, a same-second recompile can judge
                # the previous attempt's code, not the new one.
                [_venv_python(), "-B", shim],
                input=json.dumps({"args": args}),
                capture_output=True, text=True, timeout=timeout,
                env=_judge_env(),
            )
        except subprocess.TimeoutExpired:
            return _fail(case, "shim timed out")

    out = _parse_shim_output(proc)
    if out is None:
        return _fail(case, f"shim crashed: {_last_error_line(proc.stderr)}")
    return _check(case, out)


_BASE_ENV_ALLOWLIST = {
    "PATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
}


def _judge_env():
    """Run generated code without inheriting credentials from the caller."""
    names = set(_BASE_ENV_ALLOWLIST)
    extra = os.environ.get("ANGL_JUDGE_ENV_ALLOWLIST", "")
    names.update(name.strip() for name in extra.split(",") if name.strip())
    env = {name: os.environ[name] for name in names if name in os.environ}
    env["PYTHONNOUSERSITE"] = "1"
    return env


_EXC_HEADER = re.compile(r"^[\w.]+(Error|Exception|Warning):")


def _last_error_line(stderr):
    """The exception header line is almost always more useful than the full
    traceback — no absolute file paths, no frames the reader doesn't care
    about, just what actually broke. Search from the bottom for the line that
    actually starts the exception (`SomeError: message`), not just the last
    non-empty line — some exceptions (e.g. pydantic's) embed their own
    multi-line message, so the literal last line can be a continuation/footer
    rather than the real error."""
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    for line in reversed(lines):
        if _EXC_HEADER.match(line):
            return line[:200]
    return lines[-1][:200] if lines else "(no stderr)"


def _parse_shim_output(proc):
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        out = json.loads(text.splitlines()[-1])
    except ValueError:
        return None
    if not isinstance(out, dict) or "ok" not in out:
        return None
    return out


def _check(case, out):
    exp = case["expect"]
    if "error_contains" in exp:
        if not out.get("ok", True) and exp["error_contains"] in (out.get("error") or ""):
            return _pass(case)
        return _fail(case, f"expected error containing {exp['error_contains']!r}, got {out}")
    if out.get("ok") and _eq(out.get("value"), exp["returns"]):
        return _pass(case)
    if out.get("ok") and _looks_like_nested_shim(out.get("value")):
        return _fail(
            case,
            "artifact returned the shim protocol as its value; return the "
            "contract value directly or raise an exception for expected errors",
        )
    return _fail(case, f"expected return {exp['returns']!r}, got {out}")


def _eq(a, b):
    # bool is a subclass of int in Python, so `1 == True` is True — that would
    # let a contract expecting a real JSON boolean pass against a buggy
    # int-returning implementation. Require an exact type match whenever
    # either side is actually a bool.
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    return a == b


def _looks_like_nested_shim(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("ok"), bool)
        and ("value" in value or "error" in value)
    )


def _pass(case):
    return {"pass": True, "case": case["raw"], "detail": ""}


def _fail(case, detail):
    return {"pass": False, "case": case["raw"], "detail": detail}
