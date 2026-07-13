"""Postgres fixture integration tests.

These use Docker when available. If Docker is unavailable, the tests become a
no-op so the pure parser/compiler suite still runs on smaller machines.

Run directly: python3 tests/test_postgres_fixture.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.compile import _render_shim
from angl.fixtures import POSTGRES_IMAGE
from angl.parse import parse
from angl.verify import verify_spec


def _docker_ready():
    if not shutil.which("docker"):
        return False
    return os.system("docker info >/dev/null 2>&1") == 0


def _postgres_image_available():
    return os.system(f"docker image inspect {POSTGRES_IMAGE} >/dev/null 2>&1") == 0


def test_parses_postgres_fixture_example():
    spec = parse("""\
# Count Incidents

> Boundary: `count_incidents(db: object) -> number`

## Behavior

Count persisted incident rows in Postgres.

## Examples

### Two incidents exist

Fixture `postgres_fixture`:
```json
{
  "setup_sql": [
    "create table incidents (id text primary key, severity text not null)",
    "insert into incidents (id, severity) values ('INC-1', 'sev1'), ('INC-2', 'sev2')"
  ]
}
```

Returns:
```json
2
```
""")
    assert spec["cases"][0]["input"]["sources"][0]["fixture"] == "postgres_fixture"


def test_postgres_fixture_runs_through_black_box_judge():
    if not _docker_ready() or not _postgres_image_available():
        print("  SKIP postgres fixture requires local docker and postgres:16-alpine")
        return

    spec = parse("""\
# Count Incidents

> Boundary: `count_incidents(db: object) -> number`

## Behavior

Count persisted incident rows in Postgres.

## Examples

### Two incidents exist

Fixture `postgres_fixture`:
```json
{
  "setup_sql": [
    "create table incidents (id text primary key, severity text not null)",
    "insert into incidents (id, severity) values ('INC-1', 'sev1'), ('INC-2', 'sev2')"
  ]
}
```

Returns:
```json
2
```
""")
    artifact_source = """\
import subprocess


def count_incidents(db):
    proc = subprocess.run(
        [
            "docker", "exec", db["container"], "psql",
            "-U", db["user"], "-d", db["database"],
            "-t", "-A", "-c", "select count(*) from incidents",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return int(proc.stdout.strip())
"""
    tmpdir = tempfile.mkdtemp()
    try:
        artifact = os.path.join(tmpdir, "count_incidents.py")
        shim = os.path.join(tmpdir, "count_incidents_shim.py")
        with open(artifact, "w") as f:
            f.write(artifact_source)
        with open(shim, "w") as f:
            f.write(_render_shim("count_incidents"))
        report = verify_spec(spec, {"artifact": artifact, "shim": shim})
        assert report["passed"] == 1
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
