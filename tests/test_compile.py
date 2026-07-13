"""Unit tests for the repair loop (angl.run.compile_until_green) and the
repair-aware prompt builder in angl.compile. Mocks _call_model so these run
deterministically with no model/network dependency.

Run directly: python3 tests/test_compile.py
"""
import os
import json
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import angl.compile as compile_mod
from angl.compile import _build_prompt, _call_model
from angl.provider import codex_failure_detail
from angl.run import compile_until_green
from angl.verify import verify_spec


def _docker_daemon_available():
    if not shutil.which("docker"):
        return False
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True)
    return proc.returncode == 0


ADD_SPEC = {
    "name": "add", "func": "add", "interface": "add(a, b) -> number",
    "target": "python", "uses": [], "intent": "add two numbers",
    "cases": [{"raw": "1,2 -> 3",
               "input": {"sources": [{"literal": 1}, {"literal": 2}]},
               "expect": {"returns": 3}}],
}

NODE_ADD_SPEC = dict(ADD_SPEC, target="node")
RUBY_ADD_SPEC = dict(ADD_SPEC, target="ruby")
GO_ADD_SPEC = dict(ADD_SPEC, target="go")
RUST_ADD_SPEC = dict(ADD_SPEC, target="rust")
TS_ADD_SPEC = dict(ADD_SPEC, target="typescript")

BUNDLE_ADD_SPEC = dict(ADD_SPEC, target="bundle")
ASSEMBLY_ADD_SPEC = dict(ADD_SPEC, target="assembly")

BUNDLE_ADD = json.dumps({
    "files": [
        {
            "path": "add.py",
            "content": "def add(a, b):\n    return a + b\n",
        },
        {
            "path": "add_judge.py",
            "content": (
                "import json\n"
                "import sys\n"
                "from add import add\n\n"
                "req = json.loads(sys.stdin.read() or '{}')\n"
                "try:\n"
                "    value = add(*req.get('args', []))\n"
                "    print(json.dumps({'ok': True, 'value': value}))\n"
                "except Exception as e:\n"
                "    print(json.dumps({'ok': False, 'error': str(e)}))\n"
            ),
        },
    ],
    "build": [],
    "implementation": "add.py",
    "judge_adapter": "add_judge.py",
    "host_adapter": "add.py",
    "public_files": ["add.py"],
    "private_files": ["add_judge.py"],
})

ASSEMBLY_ADD = json.dumps({
    "files": [
        {
            "path": "add.s",
            "content": (
                ".text\n"
                ".globl _add_asm\n"
                ".p2align 2\n"
                "_add_asm:\n"
                "    add x0, x0, x1\n"
                "    ret\n"
            ),
        },
        {
            "path": "add.py",
            "content": (
                "import ctypes\n"
                "import os\n\n"
                "_lib = ctypes.CDLL(os.path.join(os.path.dirname(__file__), 'libadd.dylib'))\n"
                "_lib.add_asm.argtypes = [ctypes.c_longlong, ctypes.c_longlong]\n"
                "_lib.add_asm.restype = ctypes.c_longlong\n\n"
                "def add(a, b):\n"
                "    return int(_lib.add_asm(int(a), int(b)))\n"
            ),
        },
        {
            "path": "add_judge.py",
            "content": (
                "import json\n"
                "import sys\n"
                "from add import add\n\n"
                "req = json.loads(sys.stdin.read() or '{}')\n"
                "try:\n"
                "    value = add(*req.get('args', []))\n"
                "    print(json.dumps({'ok': True, 'value': value}))\n"
                "except Exception as e:\n"
                "    print(json.dumps({'ok': False, 'error': str(e)}))\n"
            ),
        },
    ],
    "build": [["clang", "-dynamiclib", "-o", "libadd.dylib", "add.s"]],
    "implementation": "add.s",
    "judge_adapter": "add_judge.py",
    "host_adapter": "add.py",
    "public_files": ["add.s", "add.py", "libadd.dylib"],
    "private_files": ["add_judge.py"],
})


def _with_fake_model(responses):
    """Context manager-ish helper: monkeypatch _call_model to return each of
    `responses` in order, recording every prompt it was called with."""
    calls = []
    it = iter(responses)

    def fake(prompt):
        calls.append(prompt)
        return next(it)

    original = compile_mod._call_model
    compile_mod._call_model = fake
    return calls, original


def test_succeeds_first_try_needs_no_repair_context():
    calls, original = _with_fake_model(["def add(a, b):\n    return a + b\n"])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(ADD_SPEC, tmpdir)
        assert attempts == 1
        assert result["passed"] == result["total"]
        assert "previous attempt" not in calls[0].lower()
        assert build["manifest"].endswith("add.manifest.json")
        with open(build["manifest"]) as f:
            manifest = json.load(f)
        assert manifest["chapter"] == "add"
        assert manifest["boundary"] == "add(a, b) -> number"
        assert manifest["target"] == "python"
        assert manifest["implementation"] == "add.py"
        assert manifest["host_adapter"] is None
        assert manifest["judge_adapter"] == "add_shim.py"
        assert manifest["entrypoint"] == "add.py"
        assert manifest["generated_files"] == ["add.py", "add_shim.py"]
        assert manifest["cases"] == 1
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_retries_with_failure_fed_back_and_recovers():
    # First attempt is wrong on purpose (subtracts instead of adds). Second
    # attempt "fixes" it — proving the loop actually retries AND that the
    # second prompt contains the concrete failure from the first.
    calls, original = _with_fake_model([
        "def add(a, b):\n    return a - b\n",
        "def add(a, b):\n    return a + b\n",
    ])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(ADD_SPEC, tmpdir, max_attempts=3)
        assert attempts == 2
        assert result["passed"] == result["total"]
        assert len(calls) == 2
        second_prompt = calls[1].lower()
        assert "previous attempt" in second_prompt
        assert "return a - b" in calls[1]  # the actual failing code was shown
        assert "expected return 3" in second_prompt  # the actual failure was shown
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_gives_up_after_max_attempts_and_reports_the_last_failure():
    calls, original = _with_fake_model([
        "def add(a, b):\n    return 0\n",
        "def add(a, b):\n    return 0\n",
        "def add(a, b):\n    return 0\n",
    ])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(ADD_SPEC, tmpdir, max_attempts=3)
        assert attempts == 3
        assert result["passed"] == 0
        assert len(calls) == 3
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_compile_time_error_is_also_fed_back_not_a_crash():
    # First attempt is a syntax error (caught by _check_syntax, raises
    # RuntimeError inside compile_spec). The loop must catch that and retry,
    # not propagate it and abort the whole run on one bad generation.
    calls, original = _with_fake_model([
        "def add(a, b)\n    return a + b\n",   # missing colon: SyntaxError
        "def add(a, b):\n    return a + b\n",
    ])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(ADD_SPEC, tmpdir, max_attempts=3)
        assert attempts == 2
        assert result["passed"] == result["total"]
        assert "compile failed" in calls[1].lower()
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_repeated_compile_time_errors_return_red_report():
    calls, original = _with_fake_model([
        "def add(a, b)\n    return a + b\n",
        "def add(a, b)\n    return a + b\n",
    ])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(ADD_SPEC, tmpdir, max_attempts=2)
        assert attempts == 2
        assert build["compile_error"]
        assert result["passed"] == 0
        assert result["total"] == 1
        assert "compile failed" in result["results"][0]["detail"]
        assert len(calls) == 2
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_dependency_prompt_imports_dependency_function_not_unit_name():
    # A unit name is the .angl file identity; the compiled Python artifact is
    # named after the interface function. Composition has to tell the model to
    # import what will actually exist in build/.
    target = {
        "name": "checkout",
        "func": "checkout",
        "interface": "checkout(url: string) -> number",
        "uses": ["price_unit"],
        "intent": "call the dependency",
        "cases": ADD_SPEC["cases"],
    }
    units = {
        "price_unit": {
            "name": "price_unit",
            "func": "fetch_price",
            "interface": "fetch_price(url: string) -> number",
            "uses": [],
            "intent": "fetch a price",
            "cases": ADD_SPEC["cases"],
        }
    }
    prompt = _build_prompt(target, units)
    assert "from fetch_price import fetch_price" in prompt
    assert "from price_unit import price_unit" not in prompt
    assert "Verified behavior examples for fetch_price" in prompt
    assert "case: 1,2 -> 3" in prompt


def test_python_prompt_mentions_runtime_and_json_serializable_returns():
    prompt = _build_prompt(ADD_SPEC, {})
    assert "The artifact will run on Python" in prompt
    assert "Return only JSON-serializable Python values" in prompt
    assert "Do not return dataclass, Pydantic model, or custom class instances" in prompt
    assert "Do not return the shim protocol yourself" in prompt


def test_prompt_includes_authoritative_contract_cases():
    prompt = _build_prompt(ADD_SPEC, {})
    assert "Executable expectations the judge will enforce" in prompt
    assert "case: 1,2 -> 3" in prompt


def test_prompt_frames_chapter_as_english_not_pseudocode():
    prompt = _build_prompt(ADD_SPEC, {})
    assert "chapter of product behavior, not pseudocode" in prompt
    assert "Chapter behavior:" in prompt
    assert "Do not ask the source to provide loops, classes" in prompt


def test_unsupported_model_provider_is_rejected():
    old = os.environ.get("ANGL_MODEL_PROVIDER")
    os.environ["ANGL_MODEL_PROVIDER"] = "bogus"
    try:
        try:
            _call_model("x")
            assert False, "expected RuntimeError for unsupported provider"
        except RuntimeError as e:
            assert "unsupported ANGL_MODEL_PROVIDER" in str(e)
    finally:
        if old is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old


def test_missing_model_provider_has_setup_hint():
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    tmpdir = tempfile.mkdtemp()
    try:
        os.environ.pop("ANGL_MODEL_PROVIDER", None)
        os.environ["ANGL_CONFIG_DIR"] = tmpdir
        try:
            _call_model("x")
            assert False, "expected RuntimeError for missing provider"
        except RuntimeError as e:
            assert "no Angl compiler provider is configured" in str(e)
            assert "angl setup claude-code" in str(e)
    finally:
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_codex_provider_uses_exec_output_file():
    calls = []
    original_run = compile_mod.subprocess.run
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_model = os.environ.get("ANGL_MODEL")
    old_codex_model = os.environ.get("ANGL_CODEX_MODEL")

    def fake_run(cmd, input, capture_output, text, timeout):
        calls.append({
            "cmd": cmd,
            "input": input,
            "capture_output": capture_output,
            "text": text,
            "timeout": timeout,
        })
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w") as f:
            f.write("def add(a, b):\n    return a + b\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="ignored event log", stderr="")

    compile_mod.subprocess.run = fake_run
    os.environ["ANGL_MODEL_PROVIDER"] = "codex"
    os.environ["ANGL_CODEX_MODEL"] = "gpt-5.4-codex"
    os.environ.pop("ANGL_MODEL", None)
    try:
        out = _call_model("PROMPT")
        assert out.startswith("def add")
        cmd = calls[0]["cmd"]
        assert cmd[:2] == ["codex", "exec"]
        assert "--sandbox" in cmd
        assert "read-only" in cmd
        assert "--ephemeral" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "--output-last-message" in cmd
        assert "--model" in cmd
        assert "gpt-5.4-codex" in cmd
        assert cmd[-1] == "-"
        assert calls[0]["input"] == "PROMPT"
    finally:
        compile_mod.subprocess.run = original_run
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_model is None:
            os.environ.pop("ANGL_MODEL", None)
        else:
            os.environ["ANGL_MODEL"] = old_model
        if old_codex_model is None:
            os.environ.pop("ANGL_CODEX_MODEL", None)
        else:
            os.environ["ANGL_CODEX_MODEL"] = old_codex_model


def test_codex_provider_ignores_claude_generic_model_default():
    calls = []
    original_run = compile_mod.subprocess.run
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_model = os.environ.get("ANGL_MODEL")
    old_codex_model = os.environ.get("ANGL_CODEX_MODEL")

    def fake_run(cmd, input, capture_output, text, timeout):
        calls.append(cmd)
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w") as f:
            f.write("def add(a, b):\n    return a + b\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    compile_mod.subprocess.run = fake_run
    os.environ["ANGL_MODEL_PROVIDER"] = "codex"
    os.environ["ANGL_MODEL"] = "sonnet"
    os.environ.pop("ANGL_CODEX_MODEL", None)
    try:
        _call_model("PROMPT")
        assert "--model" not in calls[0]
    finally:
        compile_mod.subprocess.run = original_run
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_model is None:
            os.environ.pop("ANGL_MODEL", None)
        else:
            os.environ["ANGL_MODEL"] = old_model
        if old_codex_model is None:
            os.environ.pop("ANGL_CODEX_MODEL", None)
        else:
            os.environ["ANGL_CODEX_MODEL"] = old_codex_model


def test_codex_failure_detail_extracts_error_without_prompt_dump():
    detail = codex_failure_detail(
        "OpenAI Codex v0.142.5\n"
        "--------\n"
        "user\n"
        "Executable expectations the judge will enforce.\n"
        "  case: {\"x\": 1} -> 1\n"
        "ERROR: {\"type\":\"error\",\"status\":400,"
        "\"error\":{\"message\":\"The 'sonnet' model is not supported.\"}}\n"
    )
    assert detail == "The 'sonnet' model is not supported."
    assert "Executable expectations" not in detail
    assert "case:" not in detail


def test_codex_failure_detail_explains_nested_codex_block():
    detail = codex_failure_detail(
        "failed to initialize in-process app-server client: Operation not permitted"
    )
    assert "retry from a normal terminal" in detail


def test_claude_code_provider_passes_prompt_on_stdin():
    calls = []
    original_run = compile_mod.subprocess.run
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_model = os.environ.get("ANGL_MODEL")

    def fake_run(cmd, input, capture_output, text, timeout):
        calls.append({
            "cmd": cmd,
            "input": input,
            "capture_output": capture_output,
            "text": text,
            "timeout": timeout,
        })
        return subprocess.CompletedProcess(
            cmd, 0, stdout="def add(a, b):\n    return a + b\n", stderr=""
        )

    compile_mod.subprocess.run = fake_run
    os.environ["ANGL_MODEL_PROVIDER"] = "claude-code"
    os.environ["ANGL_MODEL"] = "sonnet"
    try:
        out = _call_model("PROMPT")
        assert out.startswith("def add")
        assert calls[0]["cmd"][:2] == ["claude", "-p"]
        assert "--no-session-persistence" in calls[0]["cmd"]
        assert "--tools" in calls[0]["cmd"]
        assert calls[0]["input"] == "PROMPT"
    finally:
        compile_mod.subprocess.run = original_run
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_model is None:
            os.environ.pop("ANGL_MODEL", None)
        else:
            os.environ["ANGL_MODEL"] = old_model


def test_subprocess_target_prompts_say_args_is_positional_array():
    for spec in [NODE_ADD_SPEC, RUBY_ADD_SPEC, GO_ADD_SPEC, RUST_ADD_SPEC, TS_ADD_SPEC]:
        prompt = _build_prompt(spec, {})
        assert "positional array" in prompt
        assert "args[0]" in prompt or "req['args'][0]" in prompt


def test_node_target_json_filter_is_judged_black_box():
    calls, original = _with_fake_model([
        "const fs = require('fs');\n"
        "const req = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');\n"
        "const [a, b] = req.args || [];\n"
        "console.log(JSON.stringify({ok: true, value: a + b}));\n"
    ])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(NODE_ADD_SPEC, tmpdir)
        assert attempts == 1
        assert build["target"] == "node"
        assert build["implementation"].endswith(".js")
        assert build["host_adapter"].endswith(".py")
        assert build["judge_adapter"].endswith("_shim.py")
        assert build["artifact"].endswith(".js")
        assert build["proxy"].endswith(".py")
        assert result["passed"] == result["total"]
        assert "Write Node.js JavaScript" in calls[0]
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_ruby_target_json_filter_is_judged_black_box():
    calls, original = _with_fake_model([
        "require 'json'\n"
        "raw = STDIN.read\n"
        "req = JSON.parse(raw.empty? ? '{}' : raw)\n"
        "a, b = req['args']\n"
        "puts JSON.generate({'ok' => true, 'value' => a + b})\n"
    ])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(RUBY_ADD_SPEC, tmpdir)
        assert attempts == 1
        assert build["target"] == "ruby"
        assert build["implementation"].endswith(".rb")
        assert build["host_adapter"].endswith(".py")
        assert build["judge_adapter"].endswith("_shim.py")
        assert build["artifact"].endswith(".rb")
        assert build["proxy"].endswith(".py")
        assert result["passed"] == result["total"]
        assert "Write Ruby" in calls[0]
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_python_proxy_can_call_node_dependency():
    calls, original = _with_fake_model([
        "const fs = require('fs');\n"
        "const req = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');\n"
        "const [a, b] = req.args || [];\n"
        "console.log(JSON.stringify({ok: true, value: a + b}));\n",
        "from add import add\n\n"
        "def double_add(a, b):\n"
        "    return add(a, b) * 2\n",
    ])
    try:
        dep = dict(NODE_ADD_SPEC)
        target = {
            "name": "double_add",
            "func": "double_add",
            "interface": "double_add(a, b) -> number",
            "target": "python",
            "uses": ["add"],
            "intent": "call add and double the result",
            "cases": [{"raw": "1,2 -> 6",
                       "input": {"sources": [{"literal": 1}, {"literal": 2}]},
                       "expect": {"returns": 6}}],
        }
        units = {"add": dep, "double_add": target}
        tmpdir = tempfile.mkdtemp()
        compile_until_green(dep, tmpdir, units)
        build = compile_mod.compile_spec(target, tmpdir, units)
        result = verify_spec(target, build)
        assert result["passed"] == result["total"]
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_docker_backed_targets_generate_shims_and_proxies_without_local_toolchains():
    if not _docker_daemon_available():
        return

    responses = [
        "package main\nfunc main() {}\n",
        "fn main() {}\n",
        "console.log(JSON.stringify({ok: true, value: 0}));\n",
    ]
    calls, original = _with_fake_model(responses)
    try:
        tmpdir = tempfile.mkdtemp()
        for spec, suffix, marker in [
            (GO_ADD_SPEC, ".go", "Write Go"),
            (RUST_ADD_SPEC, ".rs", "Write Rust"),
            (TS_ADD_SPEC, ".ts", "Write TypeScript"),
        ]:
            build = compile_mod.compile_spec(spec, tmpdir)
            assert build["artifact"].endswith(suffix)
            assert build["proxy"].endswith(".py")
            assert build["manifest"].endswith("add.manifest.json")
            with open(build["manifest"]) as f:
                manifest = json.load(f)
            assert manifest["target"] == spec["target"]
            assert manifest["implementation"].endswith(suffix)
            assert manifest["host_adapter"] == "add.py"
            assert manifest["judge_adapter"] == "add_shim.py"
            assert "add.py" in manifest["generated_files"]
            with open(build["shim"]) as f:
                shim_src = f.read()
            compile(shim_src, build["shim"], "exec")
            assert "--cap-drop" in shim_src
            assert "no-new-privileges" in shim_src
            assert "--pids-limit" in shim_src
            assert "--memory" in shim_src
            assert "--cpus" in shim_src
            assert "--read-only" in shim_src
            assert "ANGL_EXECUTION_MODE" in shim_src
            assert "/tmp:rw,exec,nosuid,nodev,mode=1777,size=512m" in shim_src
            assert ":/work:ro" in shim_src
            if spec["target"] == "go":
                assert "golang:1.23-alpine" in shim_src
            if spec["target"] == "rust":
                assert "rust:1.85" in shim_src
                assert "CARGO_HOME=/tmp/cargo" in shim_src
            if spec["target"] == "typescript":
                assert "node:22-alpine" in shim_src
                assert "npm_config_cache=/tmp/npm-cache" in shim_src
            assert marker in calls[-1]
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_bundle_target_writes_model_declared_files_and_judges_black_box():
    calls, original = _with_fake_model([BUNDLE_ADD])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(
            BUNDLE_ADD_SPEC, tmpdir, max_attempts=1
        )
        assert attempts == 1
        assert result["passed"] == result["total"], result
        assert build["target"] == "bundle"
        assert build["implementation"].endswith("add.py")
        assert build["host_adapter"].endswith("add.py")
        assert build["judge_adapter"].endswith("add_judge.py")
        with open(build["manifest"]) as f:
            manifest = json.load(f)
        assert manifest["target"] == "bundle"
        assert manifest["implementation"] == "add.py"
        assert manifest["judge_adapter"] == "add_judge.py"
        assert manifest["generated_files"] == ["add.py", "add_judge.py"]
        assert "Write a generated code bundle" in calls[0]
        assert "judge_adapter" in calls[0]
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_assembly_target_builds_shared_library_and_judges_black_box():
    if not shutil.which("clang"):
        return
    calls, original = _with_fake_model([ASSEMBLY_ADD])
    try:
        tmpdir = tempfile.mkdtemp()
        build, result, attempts = compile_until_green(
            ASSEMBLY_ADD_SPEC, tmpdir, max_attempts=1
        )
        assert attempts == 1
        assert result["passed"] == result["total"], result
        assert build["target"] == "assembly"
        assert build["implementation"].endswith("add.s")
        assert build["host_adapter"].endswith("add.py")
        assert os.path.exists(os.path.join(tmpdir, "libadd.dylib"))
        with open(build["manifest"]) as f:
            manifest = json.load(f)
        assert manifest["target"] == "assembly"
        assert manifest["implementation"] == "add.s"
        assert "add.s" in manifest["generated_files"]
        assert "clang" in json.dumps(manifest["build"])
        assert "This target is assembly" in calls[0]
        assert "ctypes.CDLL" in calls[0]
    finally:
        compile_mod._call_model = original
        shutil.rmtree(tmpdir)


def test_go_docker_target_runs_under_sandbox_and_cannot_write_to_build_dir():
    if not _docker_daemon_available():
        return

    calls, original = _with_fake_model([
        """package main

import (
  "encoding/json"
  "fmt"
  "os"
)

func main() {
  err := os.WriteFile("/work/owned.txt", []byte("owned"), 0644)
  if err != nil {
    out, _ := json.Marshal(map[string]interface{}{"ok": true, "value": err.Error()})
    fmt.Println(string(out))
    return
  }
  out, _ := json.Marshal(map[string]interface{}{"ok": true, "value": "write succeeded"})
  fmt.Println(string(out))
}
""",
    ])
    spec = {
        "name": "sandbox_probe",
        "func": "sandbox_probe",
        "interface": "sandbox_probe() -> string",
        "target": "go",
        "uses": [],
        "intent": "Return whether the artifact could write into its build directory.",
        "cases": [{
            "raw": "case: -> contains read-only",
            "input": {"sources": []},
            "expect": {"returns": "open /work/owned.txt: read-only file system"},
        }],
    }
    tmpdir = tempfile.mkdtemp()
    try:
        build = compile_mod.compile_spec(spec, tmpdir)
        result = verify_spec(spec, build, timeout=180)
        assert result["passed"] == result["total"], result
        assert not os.path.exists(os.path.join(tmpdir, "owned.txt"))
        assert calls
    finally:
        compile_mod._call_model = original
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
