"""Runner-owned fixtures.

A fixture turns a case's declared *data* into the actual runtime *argument(s)*
for the function under test, plus any side effects (e.g. a live HTTP server).
Fixtures live in the toolchain, never in the spec's target language. This is
what lets the same .angl contract verify a Python artifact today and a Rust one
later: the side-effect setup is ours, only the arguments cross the boundary.
"""
import contextlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

POSTGRES_IMAGE = "postgres:16-alpine"


@contextlib.contextmanager
def http_fixture(data):
    """Serve `data` as JSON on any GET at an ephemeral localhost URL.

    Yields the arg(s) it contributes to the call: [url].
    """
    payload = json.dumps(data).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass  # keep test output clean

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield [f"http://127.0.0.1:{port}"]
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def postgres_fixture(data):
    """Start an isolated Postgres container, apply SQL, and yield connection info.

    Fixture data shape:

        {
          "setup_sql": [
            "create table incidents (...)",
            "insert into incidents values (...)"
          ]
        }

    Yields one JSON-serializable argument:

        {"url": "...", "container": "...", "user": "angl", ...}
    """
    _require_docker()
    name = f"angl-pg-{uuid.uuid4().hex[:12]}"
    started = False
    try:
        _run([
            "docker", "run", "--rm", "-d",
            "--name", name,
            "-e", "POSTGRES_USER=angl",
            "-e", "POSTGRES_PASSWORD=angl",
            "-e", "POSTGRES_DB=angl",
            "-p", "127.0.0.1::5432",
            POSTGRES_IMAGE,
        ])
        started = True
        port = _wait_for_postgres(name)
        setup_sql = data.get("setup_sql", []) if isinstance(data, dict) else []
        if setup_sql:
            _psql(name, _sql_script(setup_sql))
        yield [{
            "url": f"postgresql://angl:angl@127.0.0.1:{port}/angl",
            "host": "127.0.0.1",
            "port": int(port),
            "database": "angl",
            "user": "angl",
            "password": "angl",
            "container": name,
        }]
    finally:
        if started:
            subprocess.run(["docker", "stop", name], capture_output=True, text=True)


@contextlib.contextmanager
def resolve_input(inp):
    """Yield the full args list for a parsed case input, standing up any fixtures
    for the duration and tearing them down after. Sources compose in order."""
    args = []
    with contextlib.ExitStack() as stack:
        for src in inp["sources"]:
            if "literal" in src:
                args.append(src["literal"])
            elif src.get("fixture") == "http_fixture":
                args.extend(stack.enter_context(http_fixture(src.get("data"))))
            elif src.get("fixture") == "postgres_fixture":
                args.extend(stack.enter_context(postgres_fixture(src.get("data"))))
            else:
                raise ValueError(f"unknown source: {src!r}")
        yield args


def _require_docker():
    if not shutil.which("docker"):
        raise RuntimeError("postgres_fixture requires docker on PATH")
    _run(["docker", "info"])


def _wait_for_postgres(name, timeout=30):
    deadline = time.time() + timeout
    last_error = ""
    port = None
    while time.time() < deadline:
        port_proc = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            capture_output=True,
            text=True,
        )
        if port_proc.returncode == 0 and port_proc.stdout.strip():
            port = port_proc.stdout.strip().rsplit(":", 1)[-1]
        ready = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-U", "angl", "-d", "angl"],
            capture_output=True,
            text=True,
        )
        if port and ready.returncode == 0:
            return port
        last_error = (ready.stderr or ready.stdout or port_proc.stderr).strip()
        time.sleep(0.5)
    raise RuntimeError(f"postgres_fixture did not become ready: {last_error}")


def _psql(container, sql):
    _run(
        ["docker", "exec", "-i", container, "psql", "-U", "angl", "-d", "angl", "-v", "ON_ERROR_STOP=1"],
        input=sql,
    )


def _sql_script(statements):
    lines = []
    for statement in statements:
        statement = statement.strip()
        if not statement:
            continue
        if not statement.endswith(";"):
            statement += ";"
        lines.append(statement)
    return "\n".join(lines)


def _run(cmd, input=None):
    proc = subprocess.run(cmd, input=input, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"{cmd[0]} command failed: {detail}")
    return proc
