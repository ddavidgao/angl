"""Angl compiler: chapter -> generated edition + adapters.

The generated edition is the ordinary code for a chapter. It is disposable and
can be regenerated from the same `.angl` source. Adapters are generated
plumbing:

- judge_adapter: the verifier's black-box JSON entrypoint.
- host_adapter: optional glue a host app can import when the implementation is
  not in the host language.

Users should think in terms of chapters, expectations, and generated editions.
The legacy "artifact", "shim", and "proxy" names remain in the returned build
dict for compatibility with older demos and tests.

P2: `_generate` calls a real model over HTTP. The endpoint is never hardcoded —
read from ANGL_MODEL_URL (see ops.local.md for this dev's concrete address), so
the repo carries no infra details. `_render_shim` is unchanged from P1.

P3: the prompt tells the model exactly which dependency versions are currently
pinned. This is what makes "bump the pin, recompile" actually heal: the model
isn't guessing at a library's API, it's told what's installed right now and
writes code that matches. Same mechanism a human would use when told "we're on
library X version Y" before writing code against it.

The pins are read from the ACTUAL venv (`pip freeze`), not requirements.txt —
the file is a declaration that can drift from what's really installed (it did,
mid-session, until this was fixed); the venv is ground truth.

P6: `repair` is the actual "fail -> the compiler sees why -> tries again" loop.
Without it, a failed judge run just sits there — recompiling blind is a reroll,
not a fix. See angl/run.py's `compile_until_green` for the loop that drives
this; `compile_spec`/`_generate`/`_build_prompt` here just know how to accept
one round of "here's what you wrote and here's exactly what failed" as context.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request

from .config import get_config_value
from .provider import codex_failure_detail, codex_model

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_EXT = {
    "python": ".py",
    "node": ".js",
    "ruby": ".rb",
    "go": ".go",
    "rust": ".rs",
    "typescript": ".ts",
    "bundle": ".bundle.json",
    "assembly": ".bundle.json",
}
TARGET_COMMAND = {"node": "node", "ruby": "ruby"}
DOCKER_TARGET_IMAGE = {
    "go": "golang:1.23-alpine",
    "rust": "rust:1.85",
    "typescript": "node:22-alpine",
}


class ProviderError(RuntimeError):
    """The configured compiler could not make a generation request.

    Repairing a prior artifact cannot fix authentication, configuration, or a
    blocked nested agent process, so the runner must not spend more attempts.
    """


def _model():
    return get_config_value("model", ["ANGL_MODEL"], "qwen2.5-coder:14b")


def compile_spec(spec, build_dir, units=None, repair=None):
    """repair, if given: {"prior_code": str|None, "failures": [one-line
    strings]} — the previous attempt and exactly what the judge said was
    wrong about it, fed back into this attempt's prompt."""
    os.makedirs(build_dir, exist_ok=True)
    func = spec["func"]
    target = spec.get("target", "python")
    if target not in TARGET_EXT:
        raise RuntimeError(f"unsupported target {target!r}")
    if target in {"bundle", "assembly"}:
        return _compile_bundle_spec(spec, build_dir, units or {}, repair)
    implementation = os.path.join(build_dir, f"{func}{TARGET_EXT[target]}")
    judge_adapter = os.path.join(build_dir, f"{func}_shim.py")
    manifest_path = os.path.join(build_dir, f"{func}.manifest.json")

    with open(implementation, "w") as f:
        f.write(_generate(spec, units or {}, repair))
    host_adapter = None
    if target != "python":
        host_adapter = os.path.join(build_dir, f"{func}.py")
        with open(host_adapter, "w") as f:
            f.write(_render_python_host_adapter(func, target))
    with open(judge_adapter, "w") as f:
        f.write(_render_shim(func, target))
    manifest = _build_manifest(spec, build_dir, implementation, judge_adapter, host_adapter)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    return {
        "implementation": implementation,
        "judge_adapter": judge_adapter,
        "host_adapter": host_adapter,
        # Backward-compatible aliases. New code should prefer the clearer keys
        # above; old demos/tests still use these names.
        "artifact": implementation,
        "shim": judge_adapter,
        "func": func,
        "target": target,
        "proxy": host_adapter,
        "manifest": manifest_path,
    }


def _build_manifest(spec, build_dir, implementation, judge_adapter, host_adapter):
    generated = [implementation, judge_adapter]
    public_files = [implementation]
    private_files = [judge_adapter]
    if host_adapter:
        generated.append(host_adapter)
        public_files.append(host_adapter)
    implementation_rel = os.path.relpath(implementation, build_dir)
    judge_adapter_rel = os.path.relpath(judge_adapter, build_dir)
    host_adapter_rel = (
        os.path.relpath(host_adapter, build_dir) if host_adapter else None
    )
    return {
        "chapter": spec["name"],
        "function": spec["func"],
        "boundary": spec["interface"],
        "target": spec.get("target", "python"),
        "implementation": implementation_rel,
        "host_adapter": host_adapter_rel,
        "judge_adapter": judge_adapter_rel,
        # Legacy name retained until downstream book/demo code moves to
        # implementation.
        "entrypoint": implementation_rel,
        "generated_files": [os.path.relpath(path, build_dir) for path in generated],
        "public_files": [os.path.relpath(path, build_dir) for path in public_files],
        "private_files": [os.path.relpath(path, build_dir) for path in private_files],
        "uses": spec.get("uses", []),
        "cases": len(spec.get("cases", [])),
    }


def _render_shim(func, target="python"):
    """Generic Python adapter.

    Python targets are imported directly. Node/Ruby targets are subprocesses
    that implement the same JSON filter protocol as the shim: stdin
    {"args":[...]} -> stdout {"ok":bool,...}.
    """
    if target == "python":
        return (
            "import json, sys\n"
            f"from {func} import {func}\n\n"
            'req = json.loads(sys.stdin.read() or "{}")\n'
            'args = req.get("args", [])\n'
            "try:\n"
            f"    value = {func}(*args)\n"
            '    print(json.dumps({"ok": True, "value": value}))\n'
            "except Exception as e:\n"
            '    print(json.dumps({"ok": False, "error": str(e)}))\n'
        )
    if target not in TARGET_COMMAND:
        if target not in {"go", "rust", "typescript"}:
            raise RuntimeError(f"unsupported target {target!r}")
    command_block = textwrap.indent(_render_command_block(func, target), "        ")
    return textwrap.dedent(f"""\
        import json, os, subprocess, sys

        req = json.loads(sys.stdin.read() or "{{}}")
        build_dir = os.path.dirname(__file__)
        payload = json.dumps({{"args": req.get("args", [])}})
        env = os.environ.copy()
{command_block}
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if proc.stdout.strip():
            print(proc.stdout.strip().splitlines()[-1])
        else:
            error = proc.stderr.strip() or {target!r} + f" exited with {{proc.returncode}}"
            print(json.dumps({{"ok": False, "error": error}}))
    """)


def _render_python_host_adapter(func, target):
    command_block = textwrap.indent(_render_command_block(func, target), "            ")
    return textwrap.dedent(f"""\
        import json
        import os
        import subprocess


        def {func}(*args):
            build_dir = os.path.dirname(__file__)
            payload = json.dumps({{"args": list(args)}})
            env = os.environ.copy()
{command_block}
            proc = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if not proc.stdout.strip():
                raise RuntimeError(proc.stderr.strip() or {target!r} + f" exited with {{proc.returncode}}")
            out = json.loads(proc.stdout.strip().splitlines()[-1])
            if out.get("ok"):
                return out.get("value")
            raise RuntimeError(out.get("error") or "unknown {target} artifact error")
    """)


# Compatibility for tests/demos that still import the old helper name.
_render_python_proxy = _render_python_host_adapter


def _compile_bundle_spec(spec, build_dir, units, repair=None):
    raw = _call_model(_build_prompt(spec, units, repair))
    target = spec.get("target", "bundle")
    bundle = _parse_bundle(raw, spec["func"], target)
    generated_files = _write_bundle_files(bundle, build_dir)
    _run_bundle_build(bundle, build_dir)

    judge_adapter = _bundle_path(build_dir, bundle["judge_adapter"])
    implementation = _bundle_path(
        build_dir,
        bundle.get("implementation") or bundle["judge_adapter"],
    )
    host_adapter = (
        _bundle_path(build_dir, bundle["host_adapter"])
        if bundle.get("host_adapter")
        else None
    )
    manifest_path = os.path.join(build_dir, f"{spec['func']}.manifest.json")
    manifest = {
        "chapter": spec["name"],
        "function": spec["func"],
        "boundary": spec["interface"],
        "target": target,
        "implementation": os.path.relpath(implementation, build_dir),
        "host_adapter": (
            os.path.relpath(host_adapter, build_dir) if host_adapter else None
        ),
        "judge_adapter": os.path.relpath(judge_adapter, build_dir),
        "entrypoint": os.path.relpath(implementation, build_dir),
        "generated_files": sorted(generated_files),
        "public_files": sorted(bundle.get("public_files") or []),
        "private_files": sorted(bundle.get("private_files") or []),
        "build": bundle.get("build", []),
        "uses": spec.get("uses", []),
        "cases": len(spec.get("cases", [])),
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return {
        "implementation": implementation,
        "judge_adapter": judge_adapter,
        "host_adapter": host_adapter,
        "artifact": implementation,
        "shim": judge_adapter,
        "func": spec["func"],
        "target": target,
        "proxy": host_adapter,
        "manifest": manifest_path,
        "generated_files": [os.path.join(build_dir, path) for path in generated_files],
    }


def _parse_bundle(raw, func, target="bundle"):
    text = _strip_fences(raw).strip()
    try:
        bundle = json.loads(text)
    except ValueError as e:
        raise RuntimeError(f"model generated invalid bundle JSON for {func!r}: {e}\n---\n{text}") from e
    if not isinstance(bundle, dict):
        raise RuntimeError(f"model generated bundle for {func!r} must be a JSON object")
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError(f"model generated bundle for {func!r} must include files")
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError(f"bundle file entries for {func!r} must be objects")
        if not isinstance(item.get("path"), str) or not item["path"]:
            raise RuntimeError(f"bundle file entry for {func!r} is missing path")
        if not isinstance(item.get("content"), str):
            raise RuntimeError(f"bundle file {item.get('path')!r} is missing string content")
    if not isinstance(bundle.get("judge_adapter"), str):
        raise RuntimeError(f"bundle for {func!r} must declare judge_adapter")
    expected_judge = f"{func}_judge.py"
    if bundle["judge_adapter"] != expected_judge:
        raise RuntimeError(
            f"bundle for {func!r} must declare judge_adapter {expected_judge!r}"
        )
    if bundle.get("host_adapter") and bundle["host_adapter"] != f"{func}.py":
        raise RuntimeError(
            f"bundle for {func!r} must declare host_adapter {func + '.py'!r}"
        )
    build = bundle.get("build", [])
    if not isinstance(build, list):
        raise RuntimeError(f"bundle build field for {func!r} must be a list")
    for command in build:
        if not (
            isinstance(command, list)
            and command
            and all(isinstance(part, str) for part in command)
        ):
            raise RuntimeError(f"bundle build command for {func!r} must be a string array")
    if target == "assembly":
        _validate_assembly_bundle(bundle, func)
    return bundle


def _validate_assembly_bundle(bundle, func):
    paths = [item["path"] for item in bundle["files"]]
    if not any(path.endswith(".s") for path in paths):
        raise RuntimeError(f"assembly bundle for {func!r} must generate a .s file")
    build_parts = [part for command in bundle["build"] for part in command]
    if not any(part.endswith((".dylib", ".so")) for part in build_parts):
        raise RuntimeError(
            f"assembly bundle for {func!r} must build a shared library"
        )
    if not any(command and os.path.basename(command[0]) == "clang" for command in bundle["build"]):
        raise RuntimeError(f"assembly bundle for {func!r} must build with clang")
    if not bundle.get("host_adapter"):
        raise RuntimeError(f"assembly bundle for {func!r} must provide a host_adapter")
    if not str(bundle.get("implementation", "")).endswith(".s"):
        raise RuntimeError(f"assembly bundle for {func!r} must declare implementation as .s")


def _write_bundle_files(bundle, build_dir):
    written = []
    for item in bundle["files"]:
        path = _bundle_path(build_dir, item["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(item["content"])
        written.append(os.path.relpath(path, build_dir))
    judge_adapter = _bundle_path(build_dir, bundle["judge_adapter"])
    if not os.path.exists(judge_adapter):
        raise RuntimeError(f"bundle judge_adapter {bundle['judge_adapter']!r} was not generated")
    return written


def _run_bundle_build(bundle, build_dir):
    for command in bundle.get("build", []):
        _validate_bundle_command(command)
        proc = subprocess.run(
            command,
            cwd=build_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"bundle build failed for {command!r}: {detail}")


def _validate_bundle_command(command):
    allowed = {"clang", "cc", "python3", sys.executable}
    tool = command[0]
    if os.path.basename(tool) not in allowed and tool not in allowed:
        raise RuntimeError(
            f"bundle build command {command!r} is not allowed; allowed tools are "
            "clang, cc, and python3"
        )
    for part in command[1:]:
        if os.path.isabs(part) and not part.startswith("/tmp/"):
            raise RuntimeError(f"bundle build command uses unsafe absolute path {part!r}")


def _bundle_path(build_dir, relpath):
    normalized = os.path.normpath(relpath)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        raise RuntimeError(f"bundle path {relpath!r} escapes build directory")
    return os.path.join(build_dir, normalized)


def _render_command_block(func, target):
    artifact_name = f"{func}{TARGET_EXT[target]}"
    if target in TARGET_COMMAND:
        command = TARGET_COMMAND[target]
        return f"artifact = os.path.join(build_dir, {artifact_name!r})\ncmd = [{command!r}, artifact]"
    if target == "go":
        return f"""\
artifact = os.path.join(build_dir, {artifact_name!r})
if os.environ.get("ANGL_EXECUTION_MODE") == "local":
    env.setdefault("GOCACHE", "/tmp/go-cache")
    cmd = ["go", "run", artifact]
else:
{textwrap.indent(_docker_run_prefix_block(), "    ")}
    mount = f"{{build_dir}}:/work:ro"
    cmd = docker_prefix + [
        "-v", mount, "-w", "/work",
        "-e", "GOCACHE=/tmp/go-cache",
        "golang:1.23-alpine", "go", "run", {artifact_name!r},
    ]"""
    if target == "typescript":
        return f"""\
artifact = os.path.join(build_dir, {artifact_name!r})
if os.environ.get("ANGL_EXECUTION_MODE") == "local":
    env.setdefault("HOME", "/tmp")
    env.setdefault("npm_config_cache", "/tmp/npm-cache")
    cmd = ["tsx", artifact]
else:
{textwrap.indent(_docker_run_prefix_block(), "    ")}
    mount = f"{{build_dir}}:/work:ro"
    cmd = docker_prefix + [
        "-v", mount, "-w", "/work",
        "-e", "HOME=/tmp",
        "-e", "npm_config_cache=/tmp/npm-cache",
        "node:22-alpine", "sh", "-c", "npx --yes tsx {artifact_name}",
    ]"""
    if target == "rust":
        manifest = (
            '[package]\nname = "angl_artifact"\nversion = "0.1.0"\n'
            'edition = "2021"\n\n[dependencies]\nserde_json = "1"\n'
        )
        shell = (
            "input=$(cat); tmp=$(mktemp -d); mkdir -p $tmp/src; "
            f"cp /work/{artifact_name} $tmp/src/main.rs; "
            f"cat > $tmp/Cargo.toml <<'EOF'\n{manifest}EOF\n"
            "printf %s \"$input\" | cargo run --quiet --manifest-path $tmp/Cargo.toml"
        )
        local_shell = (
            "input=$(cat); tmp=$(mktemp -d); mkdir -p $tmp/src; "
            "cp \"$1\" $tmp/src/main.rs; "
            f"cat > $tmp/Cargo.toml <<'EOF'\n{manifest}EOF\n"
            "printf %s \"$input\" | cargo run --offline --quiet --manifest-path $tmp/Cargo.toml"
        )
        return f"""\
artifact = os.path.join(build_dir, {artifact_name!r})
if os.environ.get("ANGL_EXECUTION_MODE") == "local":
    env.setdefault("HOME", "/tmp")
    env.setdefault("CARGO_HOME", "/opt/angl-cargo")
    env.setdefault("CARGO_TARGET_DIR", "/tmp/target")
    cmd = ["sh", "-c", {local_shell!r}, "sh", artifact]
else:
{textwrap.indent(_docker_run_prefix_block(), "    ")}
    mount = f"{{build_dir}}:/work:ro"
    cmd = docker_prefix + [
        "-v", mount, "-w", "/work",
        "-e", "HOME=/tmp",
        "-e", "CARGO_HOME=/tmp/cargo",
        "-e", "CARGO_TARGET_DIR=/tmp/target",
        "rust:1.85", "sh", "-c", {shell!r},
    ]"""
    raise RuntimeError(f"unsupported target {target!r}")


def _docker_run_prefix_block():
    return """\
docker_prefix = [
    "docker", "run", "--rm", "-i",
    "--read-only",
    "--tmpfs", "/tmp:rw,exec,nosuid,nodev,mode=1777,size=512m",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--pids-limit", os.environ.get("ANGL_DOCKER_PIDS_LIMIT", "128"),
    "--memory", os.environ.get("ANGL_DOCKER_MEMORY", "512m"),
    "--cpus", os.environ.get("ANGL_DOCKER_CPUS", "1"),
    "--network", os.environ.get("ANGL_DOCKER_NETWORK", "bridge"),
    "--user", os.environ.get("ANGL_DOCKER_USER", "65534:65534"),
]"""


# --- P2 real generator ----------------------------------------------------

def _generate(spec, units, repair=None):
    prompt = _build_prompt(spec, units, repair)
    raw = _call_model(prompt)
    code = _strip_fences(raw)
    _check_generated(code, spec)
    return code


def _build_prompt(spec, units, repair=None):
    target = spec.get("target", "python")
    prompt_builder = {
        "python": _python_prompt,
        "node": _node_prompt,
        "ruby": _ruby_prompt,
        "go": _go_prompt,
        "rust": _rust_prompt,
        "typescript": _typescript_prompt,
        "bundle": _bundle_prompt,
        "assembly": _assembly_prompt,
    }.get(target)
    if not prompt_builder:
        raise RuntimeError(f"unsupported target {target!r}")
    lines = prompt_builder(spec)
    _append_chapter_rules(lines)
    _append_contract_prompt(lines, spec)
    _append_dependency_prompt(lines, spec, units)
    _append_pins(lines)
    _append_repair_prompt(lines, repair)
    return "\n".join(lines)


def _append_chapter_rules(lines):
    lines += [
        "",
        "Angl source is a chapter of product behavior, not pseudocode. Treat "
        "the natural-language behavior as domain guidance. Choose whatever "
        "implementation structure is simplest for the requested target.",
        "The examples below are the executable expectations. They are the "
        "acceptance contract the judge will run. If prose and examples appear "
        "to disagree, satisfy the examples exactly and avoid inventing extra "
        "observable behavior.",
        "Do not ask the source to provide loops, classes, helper functions, or "
        "algorithm steps. Infer those implementation details yourself.",
    ]


def _python_prompt(spec):
    python_version = ".".join(str(p) for p in sys.version_info[:3])
    lines = [
        "Write Python per the spec below. Output ONLY the code. No markdown "
        "fences, no explanation, no example usage.",
        f"The artifact will run on Python {python_version}. Write code "
        "compatible with that exact runtime. In particular, do not use syntax "
        "or runtime-evaluated type annotations from newer Python versions.",
        "Return only JSON-serializable Python values: dict, list, str, int, "
        "float, bool, or None. Do not return dataclass, Pydantic model, or "
        "custom class instances.",
        "Do not return the shim protocol yourself. In normal function code, "
        "never return dictionaries like {'ok': false, 'error': ...} or "
        "{'ok': false, 'value': ...}. If the contract expects an error, raise "
        "an exception and let the generated shim convert it to the protocol.",
        "All interface arguments are ordinary JSON values decoded by the shim. "
        "An object-typed argument arrives as a Python dict, not as an open file, "
        "HTTP response, database connection, cursor, class instance, or other "
        "live handle. If a fixture passes connection info, use the fields in "
        "that dict to open the connection yourself.",
        "Do not add comments that restate what the code obviously does. Only "
        "comment a genuinely non-obvious choice, if one exists.",
        "",
        f"Interface (must match exactly): {spec['interface']}",
        "The function named in the interface MUST be defined at MODULE LEVEL "
        "(top-level `def`), never as a method inside a class, even if you also "
        "define helper classes (e.g. a pydantic model) to implement it. The "
        "caller does `from <module> import <that exact function name>` and "
        "calls it directly with positional arguments.",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]
    return lines


def _node_prompt(spec):
    return [
        "Write Node.js JavaScript per the spec below. Output ONLY the code. "
        "No markdown fences, no explanation, no example usage.",
        "The artifact MUST be a standalone JSON filter. It reads JSON from "
        "stdin shaped as {\"args\":[...]}. It writes exactly one JSON object "
        "to stdout: {\"ok\":true,\"value\":...} on success or "
        "{\"ok\":false,\"error\":\"...\"} on error.",
        "The args field is a positional array, not an object. The first "
        "interface argument is req.args[0], the second is req.args[1], and so "
        "on. Never read req.args.event or req.args.severity unless the "
        "contract itself passed an object with those fields as one positional "
        "argument.",
        "Do not import local dependency units in Node for this prototype. If "
        "dependencies are needed, this target is not yet supported.",
        "",
        f"Interface semantics to implement: {spec['interface']}",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]


def _ruby_prompt(spec):
    return [
        "Write Ruby per the spec below. Output ONLY the code. No markdown "
        "fences, no explanation, no example usage.",
        "The artifact MUST be a standalone JSON filter. It reads JSON from "
        "STDIN shaped as {\"args\":[...]}. It writes exactly one JSON object "
        "to STDOUT: {\"ok\":true,\"value\":...} on success or "
        "{\"ok\":false,\"error\":\"...\"} on error.",
        "The args field is a positional array, not an object. The first "
        "interface argument is req['args'][0], the second is req['args'][1], "
        "and so on. Never read req['args']['event'] or req['args']['severity'] "
        "unless the contract itself passed an object with those fields as one "
        "positional argument.",
        "Require only Ruby standard library json.",
        "Do not import local dependency units in Ruby for this prototype. If "
        "dependencies are needed, this target is not yet supported.",
        "",
        f"Interface semantics to implement: {spec['interface']}",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]


def _go_prompt(spec):
    return [
        "Write Go per the spec below. Output ONLY the code. No markdown "
        "fences, no explanation, no example usage.",
        "The artifact MUST be a standalone JSON filter. It reads JSON from "
        "stdin shaped as {\"args\":[...]}. It writes exactly one JSON object "
        "to stdout: {\"ok\":true,\"value\":...} on success or "
        "{\"ok\":false,\"error\":\"...\"} on error.",
        "The args field is a positional array, not an object. Decode it as "
        "[]json.RawMessage or []any. The first interface argument is args[0], "
        "the second is args[1], and so on. Never define args as a struct with "
        "event/open_incidents fields unless a single positional object has "
        "those fields.",
        "Use only the Go standard library. The file must be a complete package "
        "main program.",
        "When decoding args, do not convert decoded maps with fmt.Sprintf and "
        "then try to parse that as JSON. Use json.RawMessage for args or "
        "json.Marshal an already-decoded arg before decoding it into a struct.",
        "",
        f"Interface semantics to implement: {spec['interface']}",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]


def _rust_prompt(spec):
    return [
        "Write Rust per the spec below. Output ONLY the code. No markdown "
        "fences, no explanation, no example usage.",
        "The artifact MUST be a complete src/main.rs style JSON filter. It "
        "reads JSON from stdin shaped as {\"args\":[...]}. It writes exactly "
        "one JSON object to stdout: {\"ok\":true,\"value\":...} on success or "
        "{\"ok\":false,\"error\":\"...\"} on error.",
        "The args field is a positional array. The first interface argument "
        "is args[0], the second is args[1], and so on. Never model args as an "
        "object with named fields unless a single positional object has those "
        "fields.",
        "You may use serde_json. Do not use any other external crates.",
        "Do not import or use serde_json::Result as the return type for main. "
        "If you use the ? operator with stdin or other IO, main must return "
        "Result<(), Box<dyn std::error::Error>>. It is also fine to handle all "
        "errors manually and return from main with no Result type.",
        "",
        f"Interface semantics to implement: {spec['interface']}",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]


def _typescript_prompt(spec):
    return [
        "Write TypeScript per the spec below. Output ONLY the code. No markdown "
        "fences, no explanation, no example usage.",
        "The artifact MUST be a standalone JSON filter run by tsx. It reads "
        "JSON from stdin shaped as {\"args\":[...]}. It writes exactly one "
        "JSON object to stdout: {\"ok\":true,\"value\":...} on success or "
        "{\"ok\":false,\"error\":\"...\"} on error.",
        "The args field is a positional array, not an object. The first "
        "interface argument is data.args[0], the second is data.args[1], and "
        "so on. For an interface like f(event, severity), use "
        "`const [event, severity] = data.args`, not "
        "`const { event, severity } = data.args`.",
        "Use Node standard library only.",
        "",
        f"Interface semantics to implement: {spec['interface']}",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]


def _bundle_prompt(spec):
    return [
        "Write a generated code bundle per the spec below. Output ONLY JSON. "
        "No markdown fences, no explanation, no example usage.",
        "The bundle is ordinary generated build output. It may contain one or "
        "more source files, adapters, and optional build commands. Choose the "
        "implementation structure yourself from the chapter behavior and "
        "examples. Do not expect Angl source to provide pseudocode.",
        "The JSON shape must be:",
        "{",
        '  "files": [{"path": "relative/path", "content": "file contents"}],',
        '  "build": [["clang", "-dynamiclib", "-o", "libunit.dylib", "unit.s"]],',
        '  "implementation": "relative/path/to/main/generated/source",',
        '  "judge_adapter": "relative/path/to/python_json_filter.py",',
        '  "host_adapter": "relative/path/to/optional/importable_adapter.py",',
        '  "public_files": ["relative/path"],',
        '  "private_files": ["relative/path"]',
        "}",
        "The judge adapter MUST be a Python JSON filter. It reads stdin shaped "
        "as {\"args\":[...]} and writes exactly one JSON object to stdout: "
        "{\"ok\":true,\"value\":...} on success or "
        "{\"ok\":false,\"error\":\"...\"} on error. It may import generated "
        "files from the same bundle directory.",
        f"The judge_adapter path MUST be {spec['func']}_judge.py.",
        f"If you provide a host_adapter, its path MUST be {spec['func']}.py and "
        f"it MUST expose an importable function named {spec['func']}.",
        "Use unique file names prefixed by the interface function name so "
        "multiple bundle units can share one build directory without collisions.",
        "Build commands run from the bundle build directory. Keep commands "
        "simple and local. Prefer no build command for pure Python bundles. "
        "For native code bundles, clang is available on this machine.",
        "",
        f"Interface semantics to implement: {spec['interface']}",
        "",
        "Chapter behavior:",
        spec["intent"],
    ]


def _assembly_prompt(spec):
    lines = _bundle_prompt(spec)
    lines[:0] = [
        "This target is assembly, not pure Python. The generated bundle MUST "
        "include real assembly source and compile it locally with clang.",
        "Required assembly bundle shape:",
        "- include at least one .s file",
        "- include a clang build command that produces a .dylib or .so shared library",
        "- declare implementation as the .s file",
        "- provide a Python host_adapter named exactly <function>.py",
        "- the host_adapter must load the compiled shared library with ctypes.CDLL",
        "- the exported function named in the Angl boundary must use the native "
        "assembly result for the decision it returns",
        "Python in this target is adapter glue only: parsing JSON-like dict/list "
        "arguments, converting values into ctypes arrays or scalars, calling "
        "the compiled assembly library, and shaping the JSON-serializable return.",
        "Do not satisfy the chapter by reimplementing the decision entirely in "
        "Python while leaving assembly unused.",
        "",
    ]
    return lines


def _append_dependency_prompt(lines, spec, units):
    if spec["uses"]:
        if spec.get("target", "python") != "python":
            lines += ["", "Dependency imports are currently implemented only "
                           "for Python target units. Do not choose Node/Ruby "
                           "for units that declare uses."]
            return
        lines += ["", "Available dependencies (already implemented, import and call them"
                       " directly, do not redefine them):"]
        for dep in spec["uses"]:
            dep_spec = units.get(dep)
            dep_func = dep_spec["func"] if dep_spec else dep
            sig = dep_spec["interface"] if dep_spec else dep
            lines.append(f"  from {dep_func} import {dep_func}   # {sig}")
            if dep_spec:
                lines.append(f"  Verified behavior examples for {dep_func}:")
                for case in dep_spec["cases"][:2]:
                    lines.append(f"    case: {_truncate(case['raw'], 500)}")
                if len(dep_spec["cases"]) > 2:
                    lines.append(f"    ... {len(dep_spec['cases']) - 2} more case(s)")


def _truncate(text, limit):
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _append_contract_prompt(lines, spec):
    lines += [
        "",
        "Executable expectations the judge will enforce. Use these data "
        "examples to match behavior exactly; do not treat this as source code:",
    ]
    lines += [f"  case: {case['raw']}" for case in spec["cases"]]


def _append_pins(lines):
    pins = _pinned_deps()
    if pins:
        lines += ["", "These exact library versions are installed. Write code "
                       "compatible with THESE versions' current API (not an "
                       "older or newer one you might otherwise assume):"]
        lines += [f"  {p}" for p in pins]


def _append_repair_prompt(lines, repair):
    if repair:
        lines += ["", "--- A previous attempt at this exact spec failed. ---"]
        if repair.get("prior_code"):
            lines += ["Previous code:", "```",
                      repair["prior_code"].rstrip(), "```"]
        lines += ["", "It failed these checks:"]
        lines += [f"  - {f}" for f in repair["failures"]]
        lines += ["", "Write a corrected version. Keep whatever was already "
                       "right; fix only what's broken."]


def _pinned_deps():
    """What's ACTUALLY installed in the pinned venv (ground truth) — not the
    requirements.txt declaration, which can drift from it. Falls back to the
    file only if the venv can't answer (e.g. it doesn't exist yet)."""
    from .verify import _venv_python
    try:
        result = subprocess.run(
            [_venv_python(), "-m", "pip", "freeze"],
            capture_output=True, text=True, timeout=15,
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if lines:
            return lines
    except (OSError, subprocess.TimeoutExpired):
        pass

    path = os.path.join(REPO_ROOT, "requirements.txt")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _call_model(prompt):
    provider = get_config_value("model_provider", ["ANGL_MODEL_PROVIDER"])
    if not provider:
        raise ProviderError(
            "no Angl compiler provider is configured. Run one of:\n"
            "  angl setup codex\n"
            "  angl setup claude-code\n"
            "  angl setup ollama --model qwen2.5-coder:14b --url http://127.0.0.1:11434"
        )
    provider = provider.lower()
    if provider in {"codex", "codex-cli", "codex_cli"}:
        return _call_codex(prompt)
    if provider in {"claude", "claude-code", "claude_code"}:
        return _call_claude_code(prompt)
    if provider != "ollama":
        raise ProviderError(
            f"unsupported ANGL_MODEL_PROVIDER {provider!r}; supported providers "
            "are 'codex', 'ollama', and 'claude-code'"
        )
    return _call_ollama(prompt)


def _call_ollama(prompt):
    url = get_config_value("model_url", ["ANGL_MODEL_URL"])
    if not url:
        raise ProviderError(
            "ANGL_MODEL_URL is not set. Angl never hardcodes infra addresses in "
            "the repo — export it yourself (see ops.local.md for this dev's "
            "concrete box address), e.g.:\n"
            "  export ANGL_MODEL_URL=http://<box-tailscale-ip>:11434"
        )
    body = json.dumps({
        "model": _model(),
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 600},
    }).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise ProviderError(f"could not reach model at {url}: {e}") from e
    if "response" not in body:
        raise ProviderError(f"unexpected response shape from {url}: {body}")
    return body["response"]


def _call_claude_code(prompt):
    model = (
        get_config_value("claude_model", ["ANGL_CLAUDE_MODEL"])
        or get_config_value("model", ["ANGL_MODEL"], "sonnet")
    )
    cmd = [
        "claude",
        "-p",
        "--output-format", "text",
        "--no-session-persistence",
        "--tools", "",
        "--model", model,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(get_config_value("model_timeout", ["ANGL_MODEL_TIMEOUT"], "180")),
        )
    except FileNotFoundError as e:
        raise ProviderError(
            "Claude Code CLI is not installed or not on PATH. Install/login to "
            "Claude Code, then use ANGL_MODEL_PROVIDER=claude-code."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ProviderError(f"Claude Code timed out after {e.timeout}s") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ProviderError(f"Claude Code failed: {detail}")
    return proc.stdout


def _call_codex(prompt):
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
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(get_config_value("model_timeout", ["ANGL_MODEL_TIMEOUT"], "180")),
        )
        try:
            with open(output_path) as f:
                response = f.read()
        except OSError:
            response = ""
    except FileNotFoundError as e:
        raise ProviderError(
            "Codex CLI is not installed or not on PATH. Install/login to Codex, "
            "then use ANGL_MODEL_PROVIDER=codex."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ProviderError(f"Codex timed out after {e.timeout}s") from e
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass
    if proc.returncode != 0:
        detail = codex_failure_detail(proc.stderr or proc.stdout)
        raise ProviderError(f"Codex failed: {detail}")
    return response or proc.stdout


def _strip_fences(text):
    """Models routinely wrap output in ```python ... ``` even when told not to."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence (with optional language tag)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


def _check_generated(code, spec):
    target = spec.get("target", "python")
    if target == "python":
        tree = _check_syntax(code, spec["func"])
        _check_interface(tree, spec["func"], code)
        return
    if target == "node":
        _check_subprocess_syntax(["node", "--check"], code, spec["func"], target)
        return
    if target == "ruby":
        _check_subprocess_syntax(["ruby", "-c"], code, spec["func"], target)
        return
    if target in DOCKER_TARGET_IMAGE:
        _check_docker_syntax(code, spec["func"], target)
        return
    raise RuntimeError(f"unsupported target {target!r}")


def _check_subprocess_syntax(cmd, code, func, target):
    suffix = TARGET_EXT[target]
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
        f.write(code)
        path = f.name
    try:
        try:
            proc = subprocess.run(
                cmd + [path],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"{target} runtime is not installed") from e
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(
            f"model generated invalid {target} for {func!r}: {detail}\n---\n{code}"
        )


def _check_docker_syntax(code, func, target):
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact = os.path.join(tmpdir, f"{func}{TARGET_EXT[target]}")
        with open(artifact, "w") as f:
            f.write(code)
        cmd = _docker_syntax_command(tmpdir, func, target)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as e:
            raise RuntimeError("docker is not installed") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(
            f"model generated invalid {target} for {func!r}: {detail}\n---\n{code}"
        )


def _docker_syntax_command(tmpdir, func, target):
    mount = f"{tmpdir}:/work"
    image = DOCKER_TARGET_IMAGE[target]
    artifact_name = f"{func}{TARGET_EXT[target]}"
    if target == "go":
        with open(os.path.join(tmpdir, "go.mod"), "w") as f:
            f.write("module angl_artifact\n\ngo 1.23\n")
        return ["docker", "run", "--rm", "-v", mount, "-w", "/work", image, "go", "test", "."]
    if target == "rust":
        manifest = (
            '[package]\nname = "angl_artifact"\nversion = "0.1.0"\n'
            'edition = "2021"\n\n[dependencies]\nserde_json = "1"\n'
        )
        os.makedirs(os.path.join(tmpdir, "src"), exist_ok=True)
        os.rename(os.path.join(tmpdir, artifact_name), os.path.join(tmpdir, "src", "main.rs"))
        with open(os.path.join(tmpdir, "Cargo.toml"), "w") as f:
            f.write(manifest)
        return [
            "docker", "run", "--rm", "-v", mount, "-w", "/work", image,
            "cargo", "check", "--quiet",
        ]
    if target == "typescript":
        shell = f"npx --yes esbuild {artifact_name} --bundle --platform=node --outfile=/tmp/angl_check.js >/dev/null"
        return ["docker", "run", "--rm", "-v", mount, "-w", "/work", image, "sh", "-c", shell]
    raise RuntimeError(f"unsupported target {target!r}")


def _check_syntax(code, func):
    """Fail fast with a clear error rather than a confusing shim crash later."""
    try:
        return ast.parse(code)
    except SyntaxError as e:
        raise RuntimeError(
            f"model generated invalid Python for {func!r}: {e}\n---\n{code}"
        ) from e


def _check_interface(tree, func, code):
    """The shim does `from <func> import <func>`, which requires SOME
    module-level, callable name binding — a `def`, or an assignment aliasing
    one (e.g. `validate_config = Config.validate_config` for a classmethod).
    Whether it's actually callable correctly is the judge's job, not this
    check's; this only catches the interface name not existing at all, at
    compile time, instead of a confusing runtime ImportError in the shim."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == func for t in node.targets
        ):
            return
    raise RuntimeError(
        f"model did not bind a module-level name {func!r} (interface requires "
        f"one, either as `def {func}(...)` or an assignment aliasing a "
        f"callable to that name)\n---\n{code}"
    )
