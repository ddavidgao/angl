"""Unit tests for project-level Angl stack files.

Run directly: python3 tests/test_project.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.project import find_project_file, load_project_for, parse_project


PROJECT = """# Project

> Stack: `local_web_app`
> UI: `typescript/react`
> API: `python/fastapi`
> Data: `sqlite`
> Runtime: `docker`
> Entry points: `orders_api`, `health_check`

## Rules

- Chapters define public behavior.
- Generated helper files are private.
"""


def test_parse_project_stack_and_rules():
    project = parse_project(PROJECT)
    assert project["title"] == "Project"
    assert project["stack"]["stack"] == "local_web_app"
    assert project["stack"]["ui"] == "typescript/react"
    assert project["stack"]["api"] == "python/fastapi"
    assert project["stack"]["runtime"] == "docker"
    assert project["entry_points"] == ["orders_api", "health_check"]
    assert project["rules"] == [
        "Chapters define public behavior.",
        "Generated helper files are private.",
    ]


def test_find_project_file_walks_up_from_spec():
    tmpdir = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmpdir, "specs", "nested"))
        project_path = os.path.join(tmpdir, "angl.project")
        with open(project_path, "w") as f:
            f.write(PROJECT)
        spec_path = os.path.join(tmpdir, "specs", "nested", "thing.angl")
        with open(spec_path, "w") as f:
            f.write("# Thing\n")
        assert find_project_file(spec_path) == project_path
        loaded = load_project_for(spec_path)
        assert loaded["path"] == project_path
        assert loaded["stack"]["data"] == "sqlite"
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
