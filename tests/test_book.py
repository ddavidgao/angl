"""Unit tests for the Angl project book renderer.

Run directly: python3 tests/test_book.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from angl.book import (
    book_json,
    load_book,
    render_html,
    render_markdown,
    render_text,
    write_html,
    write_markdown,
)


def test_book_orders_dependencies_and_downstream_links():
    book = load_book("specs/build_escalation_packet.angl")
    assert book["project"]["stack"]["stack"] == "local_multitarget_library"
    names = [unit["name"] for unit in book["units"]]
    assert names[-1] == "build_escalation_packet"
    normalize = _unit(book, "normalize_event")
    assert "build_escalation_packet" in normalize["used_by"]
    root = _unit(book, "build_escalation_packet")
    assert "normalize_event" in root["uses"]


def test_book_can_ingest_eval_results():
    tmpdir = tempfile.mkdtemp()
    try:
        results = [{
            "model": "fake",
            "spec": "specs/build_escalation_packet.angl",
            "ok": True,
            "units": [
                {
                    "name": "normalize_event",
                    "target": "python",
                    "attempts": 1,
                    "passed": 5,
                    "total": 5,
                    "ok": True,
                }
            ],
        }]
        path = os.path.join(tmpdir, "results.json")
        with open(path, "w") as f:
            json.dump(results, f)
        book = load_book("specs/build_escalation_packet.angl", results_path=path)
        assert _unit(book, "normalize_event")["result"]["passed"] == 5
        assert "green 5/5 attempts=1" in render_text(book)
    finally:
        shutil.rmtree(tmpdir)


def test_book_renders_static_html():
    tmpdir = tempfile.mkdtemp()
    try:
        book = load_book("specs/route_path.angl")
        html = render_html(book)
        assert "Angl Source Reader" in html
        assert "Project Stack" in html
        assert "local_multitarget_library" in html
        assert "route_path" in html
        assert "Show exact .angl source" in html
        assert "Examples" in html
        out = os.path.join(tmpdir, "book.html")
        write_html(book, out)
        assert os.path.exists(out)
    finally:
        shutil.rmtree(tmpdir)


def test_book_renders_scenarios_and_collapsed_source():
    book = load_book("specs/picnic_plan.angl", build_dir="build/.eval/qwen2.5-coder_14b/picnic_plan")
    html = render_html(book)
    assert "When planning a beach birthday picnic, compose the full plan." in html
    assert "Show exact .angl source" in html
    assert "Show generated python edition" in html
    assert "Dependency Graph" in html
    assert "&quot;guests&quot;: 6" in html


def test_book_renders_markdown_like_chapter_view():
    book = load_book("specs/picnic_plan.angl", build_dir="build/.eval/qwen2.5-coder_14b/picnic_plan")
    html = render_html(book, view="chapter")
    assert "Angl Chapter View" in html
    assert "What This Chapter Says" in html
    assert "Examples It Must Keep True" in html
    assert "This chapter builds on Picnic Normalize, Picnic Menu, and Picnic Pack." in html
    assert "The rest of the app gives this chapter a request, and this chapter returns a result object." in html
    assert "planning a beach birthday picnic" in html
    assert "compose the full plan" in html
    assert "guests is 6" in html
    assert "temperature_f is 88" in html
    assert "drink is &quot;lemonade&quot;" in html
    assert "location is &quot;beach&quot;" in html
    assert "mood is &quot;birthday&quot;" in html
    assert "an error mentioning &quot;guests&quot;" in html
    assert "menu is an object with" in html
    assert "Show exact case data" in html
    assert "Show exact stored .angl source" in html
    assert "Interface</strong>" not in html
    assert "Target</strong>" not in html


def test_book_renders_markdown_export():
    tmpdir = tempfile.mkdtemp()
    try:
        book = load_book("specs/picnic_plan.angl")
        md = render_markdown(book)
        assert "# Picnic Plan" in md
        assert "## 4. Picnic Plan" in md
        assert "The rest of the app gives this chapter a request, and this chapter returns a result object." in md
        assert "#### planning a beach birthday picnic" in md
        assert "- Must: compose the full plan" in md
        assert "temperature_f is 88" in md
        assert "drink is \"lemonade\"" in md
        assert "location is \"beach\"" in md
        assert "mood is \"birthday\"" in md
        assert "an error mentioning \"guests\"" in md
        assert "menu is an object with" in md
        assert "<details>" in md
        out = os.path.join(tmpdir, "book.md")
        write_markdown(book, out)
        assert os.path.exists(out)
    finally:
        shutil.rmtree(tmpdir)


def test_book_json_exposes_source_and_generated_artifacts():
    book = load_book("specs/picnic_plan.angl", build_dir="build/.eval/qwen2.5-coder_14b/picnic_plan")
    payload = book_json(book)
    assert payload["project"]["stack"]["runtime"] == "local_subprocesses_and_docker"
    assert payload["order"] == ["picnic_normalize", "picnic_menu", "picnic_pack", "picnic_plan"]
    plan = _payload_unit(payload, "picnic_plan")
    assert "# Picnic Plan" in plan["source_text"]
    assert "> Uses: `picnic_normalize`, `picnic_menu`, `picnic_pack`" in plan["source_text"]
    assert plan["artifact"].endswith("picnic_plan.py")
    assert "picnic_plan" in plan["artifact_text"]
    assert plan["artifact_status"] in {"present", "older than source"}
    assert isinstance(plan["source_mtime"], float)
    assert isinstance(plan["artifact_mtime"], float)
    assert plan["cases"][0]["situation"] == "planning a beach birthday picnic"
    assert "location is \"beach\"" in plan["cases"][0]["given_summary"]


def test_book_json_exposes_compiler_manifest_when_present():
    tmpdir = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmpdir, "route_path.py"), "w") as f:
            f.write("def route_path(edges, start, goal):\n    return {}\n")
        manifest = {
            "chapter": "route_path",
            "function": "route_path",
            "boundary": "route_path(edges: object, start: string, goal: string) -> object",
            "target": "python",
            "entrypoint": "route_path.py",
            "generated_files": ["route_path.py", "route_path_shim.py"],
            "public_files": ["route_path.py"],
            "private_files": ["route_path_shim.py"],
            "uses": [],
            "cases": 3,
        }
        with open(os.path.join(tmpdir, "route_path.manifest.json"), "w") as f:
            json.dump(manifest, f)
        payload = book_json(load_book("specs/route_path.angl", build_dir=tmpdir))
        route = _payload_unit(payload, "route_path")
        assert route["manifest"]["entrypoint"] == "route_path.py"
        assert route["manifest"]["cases"] == 3
    finally:
        shutil.rmtree(tmpdir)


def _unit(book, name):
    for unit in book["units"]:
        if unit["name"] == name:
            return unit
    raise AssertionError(f"missing unit {name}")


def _payload_unit(payload, name):
    for unit in payload["units"]:
        if unit["name"] == name:
            return unit
    raise AssertionError(f"missing unit {name}")


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
