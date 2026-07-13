from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .book import main as book_main
from .config import config_path, get_config_value, load_config, save_config
from .lint import format_finding, lint_file
from .project import load_project_for
from .provider import codex_failure_detail, codex_model
from .run import compile_until_green, load_program, topo_order
from .verify import verify_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="angl", description="Angl compiler toolchain")
    parser.add_argument("--version", action="version", version=f"angl {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="create a new Angl project")
    new_parser.add_argument("path", help="project directory to create")
    _add_provider_options(new_parser)
    new_parser.set_defaults(func=_new)

    init_parser = subparsers.add_parser("init", help="create Angl starter files in a directory")
    init_parser.add_argument("path", nargs="?", default=".", help="directory to initialize")
    _add_provider_options(init_parser)
    init_parser.set_defaults(func=_init)

    try_parser = subparsers.add_parser(
        "try",
        help="create a temporary starter project, compile it, and verify it",
    )
    _add_provider_options(try_parser, default_provider="codex")
    try_parser.add_argument("--path", default=None, help="use this directory instead of a temp dir")
    try_parser.add_argument("--keep", action="store_true", help="keep the temp project after success")
    try_parser.add_argument("--max-attempts", type=int, default=_default_attempts())
    try_parser.add_argument(
        "--no-provider-smoke",
        action="store_true",
        help="skip the provider smoke request before compiling",
    )
    try_parser.set_defaults(func=_try)

    check_parser = subparsers.add_parser(
        "check", help="parse and lint a chapter or every chapter in a directory"
    )
    check_parser.add_argument("spec", nargs="?", help="target .angl chapter or directory")
    check_parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    check_parser.set_defaults(func=_check_program)

    setup_parser = subparsers.add_parser("setup", help="remember a compiler provider")
    setup_subparsers = setup_parser.add_subparsers(dest="provider", required=True)
    codex_parser = setup_subparsers.add_parser("codex", help="use the Codex CLI")
    codex_parser.add_argument("--model", default=None, help="optional Codex model")
    codex_parser.add_argument("--timeout", default="180")
    codex_parser.set_defaults(func=_setup)
    claude_parser = setup_subparsers.add_parser("claude-code", help="use the Claude Code CLI")
    claude_parser.add_argument("--model", default="sonnet")
    claude_parser.add_argument("--timeout", default="180")
    claude_parser.set_defaults(func=_setup)
    ollama_parser = setup_subparsers.add_parser("ollama", help="use a local Ollama server")
    ollama_parser.add_argument("--model", default="qwen2.5-coder:14b")
    ollama_parser.add_argument("--url", default="http://127.0.0.1:11434")
    ollama_parser.add_argument("--timeout", default="180")
    ollama_parser.set_defaults(func=_setup)

    config_parser = subparsers.add_parser("config", help="show remembered Angl configuration")
    config_parser.set_defaults(func=_config)

    run_parser = subparsers.add_parser(
        "run", help="deprecated alias for build"
    )
    run_parser.add_argument("spec", nargs="?", help="target .angl chapter or directory")
    run_parser.add_argument("--build-dir", default="build", help="generated output directory")
    run_parser.add_argument("--max-attempts", type=int, default=_default_attempts())
    run_parser.set_defaults(func=_run)

    build_parser = subparsers.add_parser(
        "build", help="compile and verify a chapter or every chapter in a directory"
    )
    build_parser.add_argument("spec", nargs="?", help="target .angl chapter or directory")
    build_parser.add_argument("--build-dir", default="build", help="generated output directory")
    build_parser.add_argument("--max-attempts", type=int, default=_default_attempts())
    build_parser.set_defaults(func=_run)

    verify_parser = subparsers.add_parser(
        "verify", help="verify a chapter or every chapter in a directory"
    )
    verify_parser.add_argument("spec", nargs="?", help="target .angl chapter or directory")
    verify_parser.add_argument("--build-dir", default="build", help="generated output directory")
    verify_parser.set_defaults(func=_verify)

    preview_parser = subparsers.add_parser("preview", help="render or serve a source reader")
    preview_parser.add_argument("spec", nargs="?", help="target .angl chapter")
    preview_parser.add_argument("--build-dir", default="build")
    preview_parser.add_argument("--results", default=None)
    preview_parser.add_argument("--html", default=None)
    preview_parser.add_argument("--markdown", default=None)
    preview_parser.add_argument("--serve", action="store_true")
    preview_parser.add_argument("--host", default="127.0.0.1")
    preview_parser.add_argument("--port", type=int, default=8782)
    preview_parser.add_argument("--view", choices=["reader", "chapter"], default="reader")
    preview_parser.set_defaults(func=_preview)

    doctor_parser = subparsers.add_parser("doctor", help="check local Angl setup")
    doctor_parser.add_argument("--spec", default=None, help="optional .angl chapter to inspect")
    doctor_parser.add_argument(
        "--provider-smoke",
        action="store_true",
        help="make a tiny provider request to verify compiler authentication/connectivity",
    )
    doctor_parser.set_defaults(func=_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


def _new(args) -> int:
    root = Path(args.path)
    if root.exists() and any(root.iterdir()):
        print(f"error: {root} already exists and is not empty")
        return 1
    _write_starter(root)
    _maybe_setup_provider(args)
    print(f"created {root}")
    _print_next_steps(root)
    return 0


def _init(args) -> int:
    root = Path(args.path)
    existing = [path for path in _starter_paths(root) if path.exists()]
    if existing:
        rel = ", ".join(str(path.relative_to(root)) for path in existing)
        print(f"error: refusing to overwrite existing files: {rel}")
        return 1
    _write_starter(root)
    _maybe_setup_provider(args)
    print(f"initialized {root}")
    _print_next_steps(root)
    return 0


def _try(args) -> int:
    root = Path(args.path).resolve() if args.path else Path(tempfile.mkdtemp(prefix="angl-try-")).resolve()
    created_temp = args.path is None
    result = 1
    old_cwd = os.getcwd()
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")

    if root.exists() and any(root.iterdir()):
        print(f"error: {root} already exists and is not empty")
        return 1

    try:
        _write_starter(root)
        os.environ["ANGL_CONFIG_DIR"] = str(root / ".angl")
        _maybe_setup_provider(args)
        print(f"project: {root}")
        print(f"provider: {args.provider}")

        os.chdir(root)
        print("\n1. check source")
        result = _check_program(argparse.Namespace(spec=None, strict=True))
        if result != 0:
            return result

        print("\n2. check provider")
        result = _doctor(
            argparse.Namespace(spec=None, provider_smoke=not args.no_provider_smoke)
        )
        if result != 0:
            return result

        print("\n3. compile and verify")
        result = _run(
            argparse.Namespace(
                spec=None,
                build_dir="build",
                max_attempts=args.max_attempts,
            )
        )
        if result == 0:
            print("\nTRY PASSED")
        return result
    finally:
        os.chdir(old_cwd)
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        if created_temp and result == 0 and not args.keep:
            shutil.rmtree(root, ignore_errors=True)
            print("temporary project removed")
        elif created_temp:
            print(f"temporary project kept: {root}")
        else:
            print(f"project kept: {root}")


def _check_program(args) -> int:
    failed = False
    try:
        target = _resolve_program_arg(args.spec)
        units = _load_units(target)
        order = topo_order(units)
    except Exception as exc:
        print(f"error: {exc}")
        return 1

    print(f"program: {len(units)} unit(s)  [{' -> '.join(order)}]")
    for name in order:
        path = _unit_path(units[name])
        findings = lint_file(str(path))
        errors = [finding for finding in findings if finding["severity"] == "error"]
        warnings = [finding for finding in findings if finding["severity"] == "warning"]
        failed = failed or bool(errors) or (args.strict and bool(warnings))
        if not findings:
            print(f"ok   {path}")
            continue
        for finding in findings:
            print(format_finding(finding))
    for finding in _composition_findings(target, units):
        print(format_finding(finding))
        failed = failed or finding["severity"] == "error" or (
            args.strict and finding["severity"] == "warning"
        )
    for finding in _project_layout_findings(target):
        print(format_finding(finding))
        failed = True
    return 1 if failed else 0


def _setup(args) -> int:
    _save_provider(args.provider, args.model, args.timeout, getattr(args, "url", None), quiet=False)
    return 0


def _config(_args) -> int:
    path = config_path()
    data = load_config()
    print(f"config: {path}")
    if not data:
        print("(empty)")
        return 0
    for key in sorted(data):
        print(f"{key}: {data[key]}")
    return 0


def _run(args) -> int:
    target = _resolve_program_arg(args.spec)
    build_dir = Path(args.build_dir).resolve()
    units = _load_units(target)
    if _print_project_errors(target, units):
        return 1
    order = topo_order(units)
    print(f"program: {len(units)} unit(s)  [{' -> '.join(order)}]")
    print(f"build: {build_dir}")

    all_green = True
    for name in order:
        spec = units[name]
        build, report, attempts = compile_until_green(
            spec,
            str(build_dir),
            units,
            max_attempts=args.max_attempts,
        )
        all_green = all_green and report["passed"] == report["total"]
        dep_note = f"  (uses: {', '.join(spec['uses'])})" if spec["uses"] else ""
        attempt_note = f"  [{attempts} attempts]" if attempts > 1 else ""
        target_note = f"  target={build.get('target', spec.get('target', 'python'))}"
        print(
            f"\n[{name}]  {report['passed']}/{report['total']} cases green"
            f"{dep_note}{target_note}{attempt_note}"
        )
        for result in report["results"]:
            mark = "PASS" if result["pass"] else "FAIL"
            line = f"  [{mark}] {result['case']}"
            if not result["pass"]:
                line += f"  -- {result['detail']}"
            print(line)

    print(f"\n{'ALL GREEN' if all_green else 'FAILURES'}: {len(order)} unit(s)")
    return 0 if all_green else 1


def _verify(args) -> int:
    target = _resolve_program_arg(args.spec)
    build_dir = Path(args.build_dir).resolve()
    units = _load_units(target)
    if _print_project_errors(target, units):
        return 1
    order = topo_order(units)
    failed = False
    print(f"program: {len(units)} unit(s)  [{' -> '.join(order)}]")
    print(f"build: {build_dir}")

    for name in order:
        spec = units[name]
        manifest_path = build_dir / f"{spec['func']}.manifest.json"
        if not manifest_path.exists():
            print(f"\n[{name}]  missing manifest: {manifest_path}")
            failed = True
            continue
        manifest = json.loads(manifest_path.read_text())
        build = {
            "implementation": str(build_dir / manifest["implementation"]),
            "judge_adapter": str(build_dir / manifest["judge_adapter"]),
            "shim": str(build_dir / manifest["judge_adapter"]),
            "target": manifest.get("target", spec.get("target", "python")),
        }
        report = verify_spec(spec, build, timeout=60)
        failed = failed or report["passed"] != report["total"]
        print(f"\n[{name}]  {report['passed']}/{report['total']} cases green")
        for result in report["results"]:
            mark = "PASS" if result["pass"] else "FAIL"
            line = f"  [{mark}] {result['case']}"
            if not result["pass"]:
                line += f"  -- {result['detail']}"
            print(line)

    print(f"\n{'ALL GREEN' if not failed else 'FAILURES'}: {len(order)} unit(s)")
    return 1 if failed else 0


def _preview(args) -> int:
    spec_path = _resolve_spec_arg(args.spec)
    forwarded = [
        str(spec_path),
        "--build-dir",
        str(Path(args.build_dir).resolve()),
        "--view",
        args.view,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.results:
        forwarded += ["--results", args.results]
    if args.html:
        forwarded += ["--html", args.html]
    if args.markdown:
        forwarded += ["--markdown", args.markdown]
    if args.serve:
        forwarded.append("--serve")
    return book_main(forwarded)


def _doctor(args) -> int:
    ok = True
    _check(True, f"angl {__version__}")
    _check(sys.version_info >= (3, 9), f"python {sys.version.split()[0]} (requires >= 3.9)")

    provider = get_config_value("model_provider", ["ANGL_MODEL_PROVIDER"])
    ok = _check(bool(provider), "compiler provider is configured") and ok
    provider = (provider or "").lower()
    if provider in {"codex", "codex-cli", "codex_cli"}:
        _check(True, f"model provider: {provider}")
        codex = shutil.which("codex")
        ok = _check(bool(codex), "codex CLI on PATH") and ok
        if codex:
            _check(True, codex)
            if args.provider_smoke:
                ok = _check(_smoke_codex(), "Codex smoke request") and ok
    elif provider in {"claude", "claude-code", "claude_code"}:
        _check(True, f"model provider: {provider}")
        claude = shutil.which("claude")
        ok = _check(bool(claude), "claude CLI on PATH") and ok
        if claude:
            _check(True, claude)
            if args.provider_smoke:
                ok = _check(_smoke_claude_code(), "Claude Code smoke request") and ok
    elif provider == "ollama":
        _check(True, f"model provider: {provider}")
        url = get_config_value("model_url", ["ANGL_MODEL_URL"])
        ok = _check(bool(url), "Ollama model URL is configured") and ok
        if url and args.provider_smoke:
            ok = _check(_smoke_ollama(url), "Ollama smoke request") and ok
    elif provider:
        ok = _check(False, f"unsupported ANGL_MODEL_PROVIDER={provider!r}") and ok

    spec_path = _resolve_optional_spec_arg(args.spec)
    if spec_path:
        ok = _check(spec_path.exists(), f"spec exists: {spec_path}") and ok
        if spec_path.exists():
            try:
                units = load_program(str(spec_path))
                order = topo_order(units)
                _check(True, f"program loads: {' -> '.join(order)}")
            except Exception as exc:
                ok = _check(False, f"program load failed: {exc}") and ok

    docker = shutil.which("docker")
    if docker:
        _check(True, f"docker on PATH: {docker}")
    else:
        _check(True, "docker not found (only needed for docker-backed fixtures)")

    clang = shutil.which("clang")
    if clang:
        _check(True, f"clang on PATH: {clang}")
    else:
        _check(True, "clang not found (only needed for assembly target)")

    return 0 if ok else 1


def _smoke_claude_code() -> bool:
    model = (
        get_config_value("claude_model", ["ANGL_CLAUDE_MODEL"])
        or get_config_value("model", ["ANGL_MODEL"], "sonnet")
    )
    try:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                "--output-format",
                "text",
                "--no-session-persistence",
                "--tools",
                "",
                "--model",
                model,
            ],
            input="Reply with exactly: ANGL_PROVIDER_OK",
            capture_output=True,
            text=True,
            timeout=int(get_config_value("model_timeout", ["ANGL_MODEL_TIMEOUT"], "60")),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"     {exc}")
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        if detail:
            print(f"     {detail}")
        return False
    return "ANGL_PROVIDER_OK" in proc.stdout


def _smoke_codex() -> bool:
    model = codex_model()
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as f:
        output_path = f.name
    cmd = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        output_path,
    ]
    if model:
        cmd += ["--model", model]
    cmd.append("-")
    try:
        proc = subprocess.run(
            cmd,
            input="Reply with exactly: ANGL_PROVIDER_OK",
            capture_output=True,
            text=True,
            timeout=int(get_config_value("model_timeout", ["ANGL_MODEL_TIMEOUT"], "60")),
        )
        try:
            with open(output_path) as f:
                response = f.read()
        except OSError:
            response = ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"     {exc}")
        return False
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass
    if proc.returncode != 0:
        detail = codex_failure_detail(proc.stderr or proc.stdout)
        if detail:
            print(f"     {detail}")
        return False
    return "ANGL_PROVIDER_OK" in (response or proc.stdout)


def _smoke_ollama(url: str) -> bool:
    root = url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{root}/api/tags", timeout=10) as resp:
            json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"     {exc}")
        return False
    return True


def _check(condition: bool, message: str) -> bool:
    mark = "ok" if condition else "fail"
    print(f"{mark:4} {message}")
    return condition


def _default_attempts() -> int:
    try:
        return int(os.environ.get("ANGL_MAX_ATTEMPTS", "3"))
    except ValueError:
        return 3


def _resolve_spec_arg(spec: str | None) -> Path:
    if spec:
        return Path(spec).resolve()
    specs = sorted(Path("specs").glob("*.angl"))
    if len(specs) == 1:
        return specs[0].resolve()
    if not specs:
        raise SystemExit("error: no spec provided and no specs/*.angl files found")
    raise SystemExit(
        "error: no spec provided and multiple specs/*.angl files exist: "
        + ", ".join(str(path) for path in specs)
    )


def _resolve_program_arg(spec: str | None) -> Path:
    """Resolve one chapter or a directory of chapters.

    Project commands deliberately default to the specs directory. A project
    should compile as a graph, not force its author to name a synthetic root
    chapter just to build everything.
    """
    if spec:
        path = Path(spec).resolve()
        if not path.exists():
            raise SystemExit(f"error: target does not exist: {path}")
        if path.is_file() and path.suffix != ".angl":
            raise SystemExit(f"error: target must be a .angl chapter or directory: {path}")
        if not path.is_file() and not path.is_dir():
            raise SystemExit(f"error: target must be a .angl chapter or directory: {path}")
        return path

    specs_dir = Path("specs")
    if specs_dir.is_dir():
        return specs_dir.resolve()

    return _resolve_spec_arg(None)


def _load_units(target: Path) -> dict:
    """Load a complete graph from a chapter or every chapter below a directory."""
    paths = [target] if target.is_file() else sorted(target.rglob("*.angl"))
    if not paths:
        raise ValueError(f"no .angl chapters found in {target}")

    units = {}
    for path in paths:
        for name, spec in load_program(str(path)).items():
            existing = units.get(name)
            if existing and _unit_path(existing) != _unit_path(spec):
                raise ValueError(
                    f"duplicate chapter name {name!r}: "
                    f"{_unit_path(existing)} and {_unit_path(spec)}"
                )
            units[name] = spec
    return units


def _unit_path(spec: dict) -> Path:
    path = spec.get("_source_path")
    if not path:
        raise ValueError(f"chapter {spec.get('name', '<unknown>')!r} has no source path")
    return Path(path)


def _composition_findings(target: Path, units: dict) -> list[dict]:
    project = load_project_for(str(target))
    if not project or not project.get("entry_points"):
        return []

    entry_points = project["entry_points"]
    findings = []
    for name in entry_points:
        if name not in units:
            findings.append({
                "path": project["path"],
                "severity": "error",
                "message": f"declared entry point {name!r} has no matching chapter",
            })
    if findings:
        return findings

    reachable = set()

    def visit(name):
        if name in reachable:
            return
        reachable.add(name)
        for dependency in units[name]["uses"]:
            visit(dependency)

    for name in entry_points:
        visit(name)
    for name in sorted(set(units) - reachable):
        findings.append({
            "path": str(_unit_path(units[name])),
            "severity": "warning",
            "message": (
                f"chapter is not reachable from declared entry points: "
                f"{', '.join(entry_points)}; add a Uses edge, declare it as an entry point, "
                "or remove it"
            ),
        })
    return findings


def _project_layout_findings(target: Path) -> list[dict]:
    if not target.is_dir():
        return []
    findings = []
    for path in target.rglob("*.md"):
        findings.append({
            "path": str(path),
            "severity": "error",
            "message": (
                "chapter source must use the .angl extension; move prose-only "
                "notes outside specs or rename this file so Angl includes it "
                "in the project graph"
            ),
        })
    project = load_project_for(str(target))
    if project and target.resolve() == Path(project["path"]).parent / "specs":
        for path in Path(project["path"]).parent.glob("*.angl"):
            if not path.is_file():
                continue
            findings.append({
                "path": str(path),
                "severity": "error",
                "message": (
                    "chapter source belongs under specs/ in this project; move "
                    "it there or target this file explicitly"
                ),
            })
    return findings


def _print_project_errors(target: Path, units: dict) -> bool:
    findings = _composition_findings(target, units) + _project_layout_findings(target)
    errors = [finding for finding in findings if finding["severity"] == "error"]
    for finding in errors:
        print(format_finding(finding))
    return bool(errors)


def _resolve_optional_spec_arg(spec: str | None) -> Path | None:
    if spec:
        return Path(spec)
    specs = sorted(Path("specs").glob("*.angl"))
    if len(specs) == 1:
        return specs[0]
    return None


def _add_provider_options(parser, default_provider=None) -> None:
    parser.add_argument(
        "--provider",
        choices=["codex", "claude-code", "ollama"],
        default=default_provider,
        help="compiler provider to configure",
    )
    parser.add_argument("--model", default=None, help="model name for --provider")
    parser.add_argument("--url", default=None, help="Ollama URL for --provider ollama")
    parser.add_argument("--timeout", default="180", help="model timeout in seconds")


def _maybe_setup_provider(args) -> None:
    if not getattr(args, "provider", None):
        return
    if args.provider == "ollama":
        model = args.model or "qwen2.5-coder:14b"
    elif args.provider == "claude-code":
        model = args.model or "sonnet"
    else:
        model = args.model
    url = args.url or "http://127.0.0.1:11434"
    _save_provider(args.provider, model, args.timeout, url, quiet=True)


def _save_provider(provider, model, timeout, url=None, quiet=False):
    data = load_config()
    data["model_provider"] = provider
    if model:
        data["model"] = model
    else:
        data.pop("model", None)
    data["model_timeout"] = str(timeout)
    if provider == "ollama":
        data["model_url"] = url or "http://127.0.0.1:11434"
        data.pop("claude_model", None)
        data.pop("codex_model", None)
    elif provider in {"codex", "codex-cli", "codex_cli"}:
        if model:
            data["codex_model"] = model
        else:
            data.pop("codex_model", None)
        data.pop("model_url", None)
        data.pop("claude_model", None)
    else:
        if model:
            data["claude_model"] = model
        data.pop("model_url", None)
        data.pop("codex_model", None)
    path = save_config(data)
    if not quiet:
        print(f"saved {path}")
        print(f"model provider: {provider}")
        if model:
            print(f"model: {model}")
        if provider == "ollama":
            print(f"model url: {data['model_url']}")
    return path


def _starter_paths(root: Path) -> list[Path]:
    return [
        root / "angl.project",
        root / ".gitignore",
        root / "README.md",
        root / ".vscode" / "tasks.json",
        root / "specs" / "greet.angl",
    ]


def _write_starter(root: Path) -> None:
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / ".vscode").mkdir(parents=True, exist_ok=True)
    files = {
        root / "angl.project": STARTER_PROJECT,
        root / ".gitignore": STARTER_GITIGNORE,
        root / "README.md": STARTER_README,
        root / ".vscode" / "tasks.json": STARTER_VSCODE_TASKS,
        root / "specs" / "greet.angl": STARTER_SPEC,
    }
    for path, content in files.items():
        path.write_text(content)


def _print_next_steps(root: Path) -> None:
    print("")
    print("next:")
    print(f"  cd {root}")
    print("  angl check")
    print("  angl doctor --provider-smoke")
    print("  angl build")


STARTER_PROJECT = """# Angl Project

> Stack: `local_library`
> Runtime: `python`
> Entry points: `greet`

## Rules

- Chapters define public behavior.
- Generated code is disposable and belongs in `build/`.
- If behavior matters, pin it with an executable example.
"""


STARTER_GITIGNORE = """build/
__pycache__/
.venv*/
"""


STARTER_README = """# Angl Starter

This project was created with `angl new`.

## Files

- `angl.project` describes project-level defaults and rules.
- `specs/greet.angl` is the source chapter.
- `.vscode/tasks.json` adds check/build tasks for the current `.angl` file.
- `build/` is generated output and should not be committed.

## Commands

Check source without calling a model:

```bash
angl check
```

Compile and verify:

```bash
angl doctor --provider-smoke
angl build
```

Read as a local source book:

```bash
angl preview --serve
```

## Multiple Inputs

Each `Input` block supplies one positional boundary argument. Repeat the block
for a function with multiple arguments:

````text
Input `quantity`:
```json
2
```

Input `stock`:
```json
10
```
````

Do not combine argument names or JSON values into one Input block.

## Composition

`angl.project` lists the public entry chapters. Each entry reaches its helpers
through `> Uses:` rails. Run `angl check --strict` before committing so an
unconnected chapter cannot quietly become dead behavior.
"""


STARTER_VSCODE_TASKS = """{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "angl: check current file",
      "type": "shell",
      "command": "angl check ${file}",
      "problemMatcher": [],
      "group": "test"
    },
    {
      "label": "angl: build current file",
      "type": "shell",
      "command": "angl build ${file}",
      "problemMatcher": [],
      "group": {
        "kind": "build",
        "isDefault": true
      }
    }
  ]
}
"""


STARTER_SPEC = """# Greet

> Boundary: `greet(name: string) -> string`

## Purpose

Return a friendly greeting.

## Behavior

Use the provided name in a short greeting. Trim surrounding whitespace before
building the greeting.

## Examples

### A simple name is greeted

Input `name`:
```json
"Ada"
```

Returns:
```json
"Hello, Ada."
```

### Surrounding whitespace is ignored

Input `name`:
```json
"  Grace  "
```

Returns:
```json
"Hello, Grace."
```
"""


if __name__ == "__main__":
    raise SystemExit(main())
