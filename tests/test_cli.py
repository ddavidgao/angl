import io
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import angl.compile as compile_mod
from angl import cli
from angl.provider import codex_failure_detail


def test_new_scaffolds_checkable_project():
    tmpdir = tempfile.mkdtemp()
    try:
        project = os.path.join(tmpdir, "hello-angl")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["new", project])
        assert code == 0
        assert os.path.exists(os.path.join(project, "angl.project"))
        assert os.path.exists(os.path.join(project, "specs", "greet.angl"))
        assert os.path.exists(os.path.join(project, ".vscode", "tasks.json"))
        assert "angl check" in out.getvalue()

        check_out = io.StringIO()
        with redirect_stdout(check_out):
            old_cwd = os.getcwd()
            os.chdir(project)
            try:
                check_code = cli.main(["check"])
            finally:
                os.chdir(old_cwd)
        assert check_code == 0
        assert "program: 1 unit(s)  [greet]" in check_out.getvalue()
    finally:
        shutil.rmtree(tmpdir)


def test_setup_claude_code_writes_config():
    tmpdir = tempfile.mkdtemp()
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    try:
        os.environ["ANGL_CONFIG_DIR"] = tmpdir
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["setup", "claude-code", "--model", "sonnet"])
        assert code == 0
        assert "model provider: claude-code" in out.getvalue()

        config_out = io.StringIO()
        with redirect_stdout(config_out):
            config_code = cli.main(["config"])
        assert config_code == 0
        text = config_out.getvalue()
        assert "model_provider: claude-code" in text
        assert "model: sonnet" in text
    finally:
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_setup_codex_writes_config_without_forcing_model():
    tmpdir = tempfile.mkdtemp()
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    try:
        os.environ["ANGL_CONFIG_DIR"] = tmpdir
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["setup", "codex"])
        assert code == 0
        assert "model provider: codex" in out.getvalue()

        config_out = io.StringIO()
        with redirect_stdout(config_out):
            config_code = cli.main(["config"])
        assert config_code == 0
        text = config_out.getvalue()
        assert "model_provider: codex" in text
        assert "model:" not in text
    finally:
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_codex_cli_failure_detail_extracts_error_without_prompt_dump():
    detail = codex_failure_detail(
        "OpenAI Codex v0.142.5\n"
        "user\n"
        "Executable expectations the judge will enforce.\n"
        "  case: {\"x\": 1} -> 1\n"
        "ERROR: {\"type\":\"error\",\"status\":400,"
        "\"error\":{\"message\":\"The 'sonnet' model is not supported.\"}}\n"
    )
    assert detail == "The 'sonnet' model is not supported."
    assert "Executable expectations" not in detail
    assert "case:" not in detail


def test_new_can_save_provider_config():
    tmpdir = tempfile.mkdtemp()
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    try:
        config_dir = os.path.join(tmpdir, "config")
        os.environ["ANGL_CONFIG_DIR"] = config_dir
        project = os.path.join(tmpdir, "hello-angl")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["new", project, "--provider", "claude-code"])
        assert code == 0
        assert "angl build" in out.getvalue()

        config_out = io.StringIO()
        with redirect_stdout(config_out):
            config_code = cli.main(["config"])
        assert config_code == 0
        assert "model_provider: claude-code" in config_out.getvalue()
    finally:
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_try_builds_starter_project_with_local_config():
    tmpdir = tempfile.mkdtemp()
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    original_call_model = compile_mod._call_model
    try:
        project = os.path.join(tmpdir, "try-project")

        def fake_call_model(_prompt):
            return 'def greet(name):\n    return f"Hello, {name.strip()}."\n'

        compile_mod._call_model = fake_call_model
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main([
                "try",
                "--provider",
                "codex",
                "--path",
                project,
                "--no-provider-smoke",
            ])
        text = out.getvalue()
        assert code == 0
        assert "TRY PASSED" in text
        assert "provider: codex" in text
        assert "ALL GREEN: 1 unit(s)" in text
        assert os.path.exists(os.path.join(project, ".angl", "config.json"))
        assert os.path.exists(os.path.join(project, "build", "greet.py"))
        assert os.path.exists(os.path.join(project, "build", "greet.manifest.json"))
        assert os.environ.get("ANGL_CONFIG_DIR") == old_config_dir
    finally:
        compile_mod._call_model = original_call_model
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_project_commands_default_to_every_chapter_in_specs():
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    original_call_model = compile_mod._call_model
    try:
        specs = os.path.join(tmpdir, "specs")
        os.makedirs(specs)
        with open(os.path.join(specs, "format_name.angl"), "w") as f:
            f.write(
                "# Format Name\n\n"
                "> Boundary: `format_name(name: string) -> string`\n\n"
                "## Behavior\n\nTrim the name.\n\n"
                "## Examples\n\n### A name\n\n"
                "Input `name`:\n```json\n\" Ada \"\n```\n\n"
                "Returns:\n```json\n\"Ada\"\n```\n"
            )
        with open(os.path.join(specs, "greet.angl"), "w") as f:
            f.write(
                "# Greet\n\n"
                "> Boundary: `greet(name: string) -> string`\n"
                "> Uses: `format_name`\n\n"
                "## Behavior\n\nReturn a greeting using the formatted name.\n\n"
                "## Examples\n\n### A greeting\n\n"
                "Input `name`:\n```json\n\"Ada\"\n```\n\n"
                "Returns:\n```json\n\"Hello, Ada.\"\n```\n"
            )

        def fake_call_model(prompt):
            if "Interface (must match exactly): format_name(name: string)" in prompt:
                return "def format_name(name):\n    return name.strip()\n"
            return (
                "from format_name import format_name\n\n"
                "def greet(name):\n    return 'Hello, ' + format_name(name) + '.'\n"
            )

        compile_mod._call_model = fake_call_model
        os.chdir(tmpdir)

        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["check"]) == 0
            assert cli.main(["build"]) == 0
            assert cli.main(["verify", "specs"]) == 0
        text = out.getvalue()
        assert "program: 2 unit(s)  [format_name -> greet]" in text
        assert text.count("ALL GREEN: 2 unit(s)") == 2
        assert os.path.exists(os.path.join(tmpdir, "build", "format_name.py"))
        assert os.path.exists(os.path.join(tmpdir, "build", "greet.py"))
    finally:
        os.chdir(old_cwd)
        compile_mod._call_model = original_call_model
        shutil.rmtree(tmpdir)


def test_check_strict_rejects_chapters_outside_declared_entry_graph():
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        specs = os.path.join(tmpdir, "specs")
        os.makedirs(specs)
        with open(os.path.join(tmpdir, "angl.project"), "w") as f:
            f.write("# Project\n\n> Entry points: `serve_order`\n")
        with open(os.path.join(specs, "serve_order.angl"), "w") as f:
            f.write(
                "# Serve Order\n\n> Boundary: `serve_order() -> string`\n\n"
                "## Behavior\n\nReturn an order status.\n\n## Examples\n\n"
                "### Status\n\nReturns:\n```json\n\"ready\"\n```\n"
            )
        with open(os.path.join(specs, "forgotten.angl"), "w") as f:
            f.write(
                "# Forgotten\n\n> Boundary: `forgotten() -> string`\n\n"
                "## Behavior\n\nReturn an unused value.\n\n## Examples\n\n"
                "### Value\n\nReturns:\n```json\n\"unused\"\n```\n"
            )
        os.chdir(tmpdir)
        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["check"]) == 0
            assert cli.main(["check", "--strict"]) == 1
        assert "not reachable from declared entry points" in out.getvalue()
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmpdir)


def test_check_rejects_markdown_chapter_in_specs_directory():
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        specs = os.path.join(tmpdir, "specs")
        os.makedirs(specs)
        with open(os.path.join(specs, "forgotten.md"), "w") as f:
            f.write("# Forgotten\n\n> Boundary: `forgotten() -> string`\n")
        with open(os.path.join(specs, "real.angl"), "w") as f:
            f.write(
                "# Real\n\n> Boundary: `real() -> string`\n\n"
                "## Behavior\n\nReturn a value.\n\n## Examples\n\n"
                "### Value\n\nReturns:\n```json\n\"ok\"\n```\n"
            )
        os.chdir(tmpdir)
        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["check"]) == 1
        assert "must use the .angl extension" in out.getvalue()
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmpdir)


def test_build_rejects_markdown_chapter_before_calling_provider():
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        specs = os.path.join(tmpdir, "specs")
        os.makedirs(specs)
        with open(os.path.join(specs, "notes.md"), "w") as f:
            f.write("This does not belong in source chapters.\n")
        with open(os.path.join(specs, "real.angl"), "w") as f:
            f.write(
                "# Real\n\n> Boundary: `real() -> string`\n\n"
                "## Behavior\n\nReturn a value.\n\n## Examples\n\n"
                "### Value\n\nReturns:\n```json\n\"ok\"\n```\n"
            )
        os.chdir(tmpdir)
        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["build"]) == 1
        assert "must use the .angl extension" in out.getvalue()
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmpdir)


def test_check_rejects_root_chapter_when_project_uses_specs_directory():
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        specs = os.path.join(tmpdir, "specs")
        os.makedirs(specs)
        with open(os.path.join(tmpdir, "angl.project"), "w") as f:
            f.write("# Project\n\n> Entry points: `real`\n")
        with open(os.path.join(specs, "real.angl"), "w") as f:
            f.write(
                "# Real\n\n> Boundary: `real() -> string`\n\n"
                "## Behavior\n\nReturn a value.\n\n## Examples\n\n"
                "### Value\n\nReturns:\n```json\n\"ok\"\n```\n"
            )
        with open(os.path.join(tmpdir, "stray.angl"), "w") as f:
            f.write("This is stray source.\n")
        os.chdir(tmpdir)
        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["check"]) == 1
        assert "belongs under specs/" in out.getvalue()
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmpdir)


def test_init_refuses_to_overwrite_starter_files():
    tmpdir = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("existing\n")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["init", tmpdir])
        assert code == 1
        assert "refusing to overwrite existing files" in out.getvalue()
    finally:
        shutil.rmtree(tmpdir)


def test_doctor_reports_spec_load():
    tmpdir = tempfile.mkdtemp()
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_url = os.environ.get("ANGL_MODEL_URL")
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    try:
        path = os.path.join(tmpdir, "hello.angl")
        with open(path, "w") as f:
            f.write(
                "# Hello\n\n"
                "> Boundary: `hello() -> string`\n\n"
                "## Behavior\n\n"
                "Return hello.\n\n"
                "## Examples\n\n"
                "### Greeting\n\n"
                "Returns:\n"
                "```json\n"
                "\"hello\"\n"
                "```\n"
            )
        os.environ["ANGL_MODEL_PROVIDER"] = "ollama"
        os.environ["ANGL_MODEL_URL"] = "http://127.0.0.1:11434"
        os.environ["ANGL_CONFIG_DIR"] = os.path.join(tmpdir, "config")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["doctor", "--spec", path])
        assert code == 0
        text = out.getvalue()
        assert "program loads: hello" in text
        assert "Ollama model URL is configured" in text
    finally:
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_url is None:
            os.environ.pop("ANGL_MODEL_URL", None)
        else:
            os.environ["ANGL_MODEL_URL"] = old_url
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_doctor_auto_detects_single_spec():
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    try:
        os.environ["ANGL_MODEL_PROVIDER"] = "claude-code"
        os.environ["ANGL_CONFIG_DIR"] = os.path.join(tmpdir, "config")
        with redirect_stdout(io.StringIO()):
            cli.main(["new", os.path.join(tmpdir, "hello-angl")])
        os.chdir(os.path.join(tmpdir, "hello-angl"))
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["doctor"])
        assert code in {0, 1}
        assert "spec exists: specs/greet.angl" in out.getvalue()
        assert "program loads: greet" in out.getvalue()
    finally:
        os.chdir(old_cwd)
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
        shutil.rmtree(tmpdir)


def test_doctor_provider_smoke_catches_claude_auth_failure():
    tmpdir = tempfile.mkdtemp()
    old_provider = os.environ.get("ANGL_MODEL_PROVIDER")
    old_config_dir = os.environ.get("ANGL_CONFIG_DIR")
    old_which = cli.shutil.which
    old_run = cli.subprocess.run
    try:
        os.environ["ANGL_MODEL_PROVIDER"] = "claude-code"
        os.environ["ANGL_CONFIG_DIR"] = os.path.join(tmpdir, "config")
        cli.shutil.which = lambda name: "/tmp/fake-claude" if name == "claude" else old_which(name)

        class Proc:
            returncode = 1
            stdout = ""
            stderr = "Failed to authenticate. API Error: 401 Invalid authentication credentials"

        cli.subprocess.run = lambda *args, **kwargs: Proc()
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["doctor", "--provider-smoke"])
        text = out.getvalue()
        assert code == 1
        assert "fail Claude Code smoke request" in text
        assert "401 Invalid authentication credentials" in text
    finally:
        cli.shutil.which = old_which
        cli.subprocess.run = old_run
        if old_provider is None:
            os.environ.pop("ANGL_MODEL_PROVIDER", None)
        else:
            os.environ["ANGL_MODEL_PROVIDER"] = old_provider
        if old_config_dir is None:
            os.environ.pop("ANGL_CONFIG_DIR", None)
        else:
            os.environ["ANGL_CONFIG_DIR"] = old_config_dir
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
