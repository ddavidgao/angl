"""Smoke test for the no-clone installer script.

Run directly: python3 tests/test_install.py
"""
import os
import json
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from angl import __version__


def test_release_versions_match():
    with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as f:
        package = json.load(f)
    assert package["version"] == __version__


def test_install_script_installs_cli_and_starter_flow():
    tmpdir = tempfile.mkdtemp()
    try:
        prefix = os.path.join(tmpdir, "prefix")
        bin_dir = os.path.join(tmpdir, "bin")
        config_dir = os.path.join(tmpdir, "config")
        project = os.path.join(tmpdir, "project")
        env = os.environ.copy()
        env["ANGL_INSTALL_SOURCE"] = ROOT
        env["ANGL_INSTALL_PREFIX"] = prefix
        env["ANGL_INSTALL_BIN_DIR"] = bin_dir

        subprocess.run([os.path.join(ROOT, "install.sh")], env=env, check=True)
        angl = os.path.join(bin_dir, "angl")
        version = subprocess.run([angl, "--version"], capture_output=True, text=True, check=True)
        assert version.stdout.strip() == f"angl {__version__}"

        env["ANGL_CONFIG_DIR"] = config_dir
        subprocess.run([angl, "new", project, "--provider", "codex"], env=env, check=True)
        checked = subprocess.run(
            [angl, "check"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "program: 1 unit(s)  [greet]" in checked.stdout
    finally:
        shutil.rmtree(tmpdir)


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
