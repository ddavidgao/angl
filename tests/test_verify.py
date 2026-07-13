"""Unit tests for angl.verify — the judge. Uses hand-written fake artifacts,
no model/network dependency, so these run anywhere instantly.

Run directly: python3 tests/test_verify.py
Also pytest-discoverable (test_* naming) if pytest is ever added.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import angl.verify as verify_mod
from angl.compile import _render_shim
from angl.verify import _eq, _judge_env, _last_error_line, verify_spec


def _build(tmpdir, func, artifact_src):
    """Write a hand-crafted artifact + the real shim renderer, mimicking what
    compile_spec produces, but without needing a model."""
    artifact = os.path.join(tmpdir, f"{func}.py")
    shim = os.path.join(tmpdir, f"{func}_shim.py")
    with open(artifact, "w") as f:
        f.write(artifact_src)
    with open(shim, "w") as f:
        f.write(_render_shim(func))
    return {"artifact": artifact, "shim": shim, "func": func}


def _case(raw, sources, expect):
    return {"raw": raw, "input": {"sources": sources}, "expect": expect}


def test_judge_passes_correct_artifact():
    tmpdir = tempfile.mkdtemp()
    try:
        build = _build(tmpdir, "add", "def add(a, b):\n    return a + b\n")
        spec = {"cases": [_case("1,2 -> 3", [{"literal": 1}, {"literal": 2}],
                                 {"returns": 3})]}
        result = verify_spec(spec, build)
        assert result == {"passed": 1, "total": 1, "results": result["results"]}
        assert result["results"][0]["pass"] is True
    finally:
        shutil.rmtree(tmpdir)


def test_judge_fails_wrong_return_value():
    tmpdir = tempfile.mkdtemp()
    try:
        build = _build(tmpdir, "add", "def add(a, b):\n    return 999\n")
        spec = {"cases": [_case("1,2 -> 3", [{"literal": 1}, {"literal": 2}],
                                 {"returns": 3})]}
        result = verify_spec(spec, build)
        assert result["passed"] == 0
        assert "expected return 3" in result["results"][0]["detail"]
    finally:
        shutil.rmtree(tmpdir)


def test_judge_calls_out_nested_shim_protocol_value():
    tmpdir = tempfile.mkdtemp()
    try:
        build = _build(
            tmpdir,
            "wrap",
            "def wrap(x):\n"
            "    return {'ok': True, 'value': x}\n",
        )
        spec = {"cases": [_case("3 -> 3", [{"literal": 3}], {"returns": 3})]}
        result = verify_spec(spec, build)
        assert result["passed"] == 0
        assert "returned the shim protocol as its value" in result["results"][0]["detail"]
    finally:
        shutil.rmtree(tmpdir)


def test_judge_passes_expected_error():
    tmpdir = tempfile.mkdtemp()
    try:
        build = _build(tmpdir, "divide",
                        "def divide(a, b):\n"
                        "    if b == 0:\n"
                        "        raise ValueError('cannot divide by zero')\n"
                        "    return a / b\n")
        spec = {"cases": [_case("1,0 -> !err", [{"literal": 1}, {"literal": 0}],
                                 {"error_contains": "divide by zero"})]}
        result = verify_spec(spec, build)
        assert result["passed"] == 1
    finally:
        shutil.rmtree(tmpdir)


def test_judge_catches_a_crashing_artifact():
    tmpdir = tempfile.mkdtemp()
    try:
        # Artifact fails to import entirely — a real shape this hit in
        # practice (pydantic v1->v2 BaseSettings ImportError).
        build = _build(tmpdir, "broken", "raise ImportError('nope, moved')\n")
        spec = {"cases": [_case("1 -> 1", [{"literal": 1}], {"returns": 1})]}
        result = verify_spec(spec, build)
        assert result["passed"] == 0
        assert "shim crashed" in result["results"][0]["detail"]
    finally:
        shutil.rmtree(tmpdir)


def test_judge_fails_malformed_shim_output_instead_of_crashing():
    tmpdir = tempfile.mkdtemp()
    try:
        shim = os.path.join(tmpdir, "bad_shim.py")
        with open(shim, "w") as f:
            f.write("print('[]')\n")
        build = {"artifact": os.path.join(tmpdir, "x.py"), "shim": shim, "func": "x"}
        spec = {"cases": [_case("1 -> 1", [{"literal": 1}], {"returns": 1})]}
        result = verify_spec(spec, build)
        assert result["passed"] == 0
        assert "shim crashed" in result["results"][0]["detail"]
    finally:
        shutil.rmtree(tmpdir)


def test_shim_invoked_with_dont_write_bytecode_flag():
    # Regression test for a real, confirmed bug: Angl rewrites the SAME
    # artifact filename on every recompile. Apple's system Python caches
    # bytecode by absolute path with second-granularity mtime checks, so two
    # writes to the same path within the same wall-clock second can produce a
    # false cache hit — the judge would then verify STALE code, not what was
    # just written. Confirmed by direct repro outside Angl. -B
    # (dont_write_bytecode) sidesteps it. This test only guards that the flag
    # stays in the invocation; the actual OS-level race isn't portably
    # reproducible on demand (it depends on the Python build's cache
    # behavior, not on Angl's own code).
    tmpdir = tempfile.mkdtemp()
    try:
        build = _build(tmpdir, "add", "def add(a, b):\n    return a + b\n")
        captured = {}
        real_run = subprocess.run

        def spy(cmd, **kwargs):
            captured["cmd"] = cmd
            return real_run(cmd, **kwargs)

        verify_mod.subprocess.run = spy
        try:
            verify_spec({"cases": [
                {"raw": "1,2 -> 3", "input": {"sources": [{"literal": 1}, {"literal": 2}]},
                 "expect": {"returns": 3}}]}, build)
        finally:
            verify_mod.subprocess.run = real_run
        assert "-B" in captured["cmd"]
    finally:
        shutil.rmtree(tmpdir)


def test_judge_does_not_inherit_secret_environment_values():
    tmpdir = tempfile.mkdtemp()
    old_secret = os.environ.get("ANTHROPIC_API_KEY")
    try:
        os.environ["ANTHROPIC_API_KEY"] = "should-not-leak"
        build = _build(
            tmpdir,
            "leak",
            "import os\n"
            "def leak():\n"
            "    return os.environ.get('ANTHROPIC_API_KEY')\n",
        )
        spec = {"cases": [_case("-> null", [], {"returns": None})]}
        result = verify_spec(spec, build)
        assert result["passed"] == 1
    finally:
        if old_secret is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old_secret
        shutil.rmtree(tmpdir)


def test_judge_env_supports_explicit_fixture_allowlist():
    old_value = os.environ.get("ANGLED_TEST_FIXTURE")
    old_allowlist = os.environ.get("ANGL_JUDGE_ENV_ALLOWLIST")
    try:
        os.environ["ANGLED_TEST_FIXTURE"] = "visible"
        os.environ["ANGL_JUDGE_ENV_ALLOWLIST"] = "ANGLED_TEST_FIXTURE"
        env = _judge_env()
        assert env["ANGLED_TEST_FIXTURE"] == "visible"
    finally:
        if old_value is None:
            os.environ.pop("ANGLED_TEST_FIXTURE", None)
        else:
            os.environ["ANGLED_TEST_FIXTURE"] = old_value
        if old_allowlist is None:
            os.environ.pop("ANGL_JUDGE_ENV_ALLOWLIST", None)
        else:
            os.environ["ANGL_JUDGE_ENV_ALLOWLIST"] = old_allowlist


def test_eq_numeric_cross_type_is_loose():
    assert _eq(3, 3.0) is True
    assert _eq(9.99, 9.9900000001) is True


def test_eq_bool_requires_exact_type_match():
    # Regression test: bool is a subclass of int in Python, so 1 == True is
    # True — that used to let a contract expecting a real boolean pass
    # against a buggy int-returning implementation.
    assert _eq(1, True) is False
    assert _eq(0, False) is False
    assert _eq(True, True) is True
    assert _eq(False, False) is True


def test_last_error_line_finds_the_real_exception_not_a_footer():
    # Regression test: pydantic's ImportError embeds its own multi-line
    # message with a "For further information..." footer line AFTER the
    # real error, which a naive "last non-empty line" grab would surface
    # instead of the actual error.
    stderr = (
        "Traceback (most recent call last):\n"
        "  File \"shim.py\", line 2, in <module>\n"
        "    from x import x\n"
        "pydantic.errors.PydanticImportError: `BaseSettings` has moved.\n"
        "\n"
        "For further information visit https://errors.pydantic.dev/u/x\n"
    )
    line = _last_error_line(stderr)
    assert line.startswith("pydantic.errors.PydanticImportError:")


def test_last_error_line_falls_back_to_last_line_if_no_exception_header():
    line = _last_error_line("some weird output\nwith no traceback shape\n")
    assert line == "with no traceback shape"


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
