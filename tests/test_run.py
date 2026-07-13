"""Unit tests for angl.run orchestration helpers.

Run directly: python3 tests/test_run.py
Also pytest-discoverable (test_* naming) if pytest is ever added.
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import angl.run as run_mod
from angl.compile import ProviderError
from angl.run import compile_until_green, load_program, topo_order


def test_topo_order_puts_dependencies_first():
    units = {
        "checkout": {"uses": ["fetch_price"]},
        "fetch_price": {"uses": []},
    }
    assert topo_order(units) == ["fetch_price", "checkout"]


def test_topo_order_rejects_cycles_with_path():
    units = {
        "a": {"uses": ["b"]},
        "b": {"uses": ["c"]},
        "c": {"uses": ["a"]},
    }
    try:
        topo_order(units)
        assert False, "expected ValueError for cyclic dependency"
    except ValueError as e:
        assert "cyclic # uses dependency" in str(e)
        assert "a -> b -> c -> a" in str(e)


def test_topo_order_rejects_missing_dependency_clearly():
    units = {
        "checkout": {"uses": ["fetch_price"]},
    }
    try:
        topo_order(units)
        assert False, "expected ValueError for missing dependency"
    except ValueError as e:
        assert str(e) == "missing # uses dependency: fetch_price"


def test_load_program_rejects_missing_dependency_file_clearly():
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "checkout.angl")
        with open(path, "w") as f:
            f.write(
                "# name: checkout\n"
                "# interface: checkout() -> number\n"
                "# uses: fetch_price\n\n"
                "## INTENT\nx\n\n"
                "## CONTRACT\ncase: -> 1\n"
            )
        try:
            load_program(path)
            assert False, "expected ValueError for missing dependency file"
        except ValueError as e:
            assert "missing # uses dependency: fetch_price" in str(e)
            assert "fetch_price.angl" in str(e)
    finally:
        shutil.rmtree(tmpdir)


def test_load_program_accepts_hyphenated_chapter_filenames():
    tmpdir = tempfile.mkdtemp()
    try:
        root = os.path.join(tmpdir, "reserve.angl")
        dependency = os.path.join(tmpdir, "validate-request.angl")
        with open(root, "w") as f:
            f.write(
                "name reserve\ninterface reserve() -> string\nuses validate_request\n\n"
                "INTENT\nReturn a reservation.\n\nCONTRACT\ncase: -> \"ok\"\n"
            )
        with open(dependency, "w") as f:
            f.write(
                "name validate_request\ninterface validate_request() -> string\n\n"
                "INTENT\nValidate a request.\n\nCONTRACT\ncase: -> \"ok\"\n"
            )
        units = load_program(root)
        assert set(units) == {"reserve", "validate_request"}
    finally:
        shutil.rmtree(tmpdir)


def test_provider_error_does_not_consume_repair_attempts():
    original_compile = run_mod.compile_spec
    spec = {
        "name": "hello",
        "func": "hello",
        "target": "python",
        "cases": [{"raw": "-> \"hello\""}],
    }
    try:
        def fail_provider(*_args, **_kwargs):
            raise ProviderError("Codex failed: nested execution is blocked")

        run_mod.compile_spec = fail_provider
        _build, report, attempts = compile_until_green(spec, tempfile.mkdtemp(), max_attempts=3)
        assert attempts == 1
        assert report["passed"] == 0
        assert "nested execution is blocked" in report["results"][0]["detail"]
    finally:
        run_mod.compile_spec = original_compile


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
