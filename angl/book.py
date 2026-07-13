"""Render an Angl program as a readable project book.

This is intentionally separate from the compiler. The compiler turns one unit
into an artifact; the book view turns a whole Angl program into a table of
contents a human can read before looking at generated code.
"""
import argparse
import html
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .parse import parse
from .project import load_project_for
from .run import topo_order
from .spec_style import intent_style_findings

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


def load_book(spec_path, build_dir=None, results_path=None):
    build_dir = build_dir or os.path.join(REPO_ROOT, "build")
    units, paths = _load_units_with_paths(spec_path)
    order = topo_order(units)
    latest = _load_results(results_path)
    downstream = _downstream_map(units)
    return {
        "root": os.path.abspath(spec_path),
        "build_dir": os.path.abspath(build_dir),
        "project": load_project_for(spec_path),
        "order": order,
        "units": [
            _chapter(units[name], paths[name], build_dir, downstream[name], latest.get(name))
            for name in order
        ],
    }


def render_text(book):
    lines = [
        "ANGL PROJECT BOOK",
        f"root: {_rel(book['root'])}",
    ]
    if book.get("project"):
        lines += [
            f"project: {_rel(book['project']['path'])}",
            "stack: " + ", ".join(
                f"{key}={value}" for key, value in sorted(book["project"]["stack"].items())
            ),
        ]
    lines += ["", "TABLE OF CONTENTS"]
    for idx, unit in enumerate(book["units"], 1):
        status = _status_label(unit)
        deps = ", ".join(unit["uses"]) if unit["uses"] else "none"
        lines.append(
            f"{idx}. {unit['name']} [{unit['target']}] {status} "
            f"interface: {unit['interface']} deps: {deps}"
        )

    for idx, unit in enumerate(book["units"], 1):
        lines += [
            "",
            f"CHAPTER {idx}: {unit['name']}",
            f"source: {_rel(unit['source'])}",
            f"target: {unit['target']}",
            f"interface: {unit['interface']}",
            f"uses: {', '.join(unit['uses']) if unit['uses'] else 'none'}",
            f"used by: {', '.join(unit['used_by']) if unit['used_by'] else 'none'}",
            f"generated artifact: {_rel(unit['artifact']) if unit['artifact'] else 'not generated'}",
            f"evidence: {_status_label(unit)}",
            "",
            "intent:",
        ]
        lines += _indent(unit["intent"].splitlines() or [""])
        lines += ["", "contract cases:"]
        for case in unit["cases"]:
            lines.append(f"  - {case}")
    return "\n".join(lines) + "\n"


def render_markdown(book):
    lines = [
        f"# {_title(os.path.splitext(os.path.basename(book['root']))[0])}",
        "",
        "This file is generated from real `.angl` source. It is a reading view, not the stored source.",
        "",
        "## Chapters",
        "",
    ]
    for idx, unit in enumerate(book["units"], 1):
        lines.append(f"{idx}. [{_title(unit['name'])}](#{unit['name'].replace('_', '-')})")

    for idx, unit in enumerate(book["units"], 1):
        lines += [
            "",
            f"## {idx}. {_title(unit['name'])}",
            "",
            _boundary_sentence(unit),
            "",
            _uses_sentence(unit),
            "",
            _used_by_sentence(unit),
            "",
            f"The current compiled edition is `{unit['target']}` and its evidence is `{_status_label(unit)}`.",
            f"The generated artifact status is `{unit['artifact_status']}`.",
            "",
            "### What This Chapter Says",
            "",
            unit["intent"],
            "",
            "### Examples It Must Keep True",
            "",
        ]
        for case in unit["case_rows"]:
            lines += [
                f"#### {case['situation']}",
                "",
                f"- Given: {case['given_summary']}",
                f"- Must: {case['promise']}",
                f"- Expected: {case['expected_summary']}",
                "",
            ]
        lines += [
            "<details>",
            "<summary>Exact stored .angl source</summary>",
            "",
            "```angl",
            unit["source_text"].rstrip(),
            "```",
            "",
            "</details>",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def render_html(book, view="reader"):
    if view not in {"reader", "chapter"}:
        raise ValueError("view must be 'reader' or 'chapter'")
    title = f"Angl Project Book: {os.path.basename(book['root'])}"
    chapters = "\n".join(_chapter_html(i + 1, unit, view) for i, unit in enumerate(book["units"]))
    toc = "\n".join(_toc_row(i + 1, unit) for i, unit in enumerate(book["units"]))
    summary = _summary_html(book)
    heading = "Angl Chapter View" if view == "chapter" else "Angl Source Reader"
    dek = (
        "A Markdown-like reading of the real .angl files: prose first, examples second, exact source available on demand."
        if view == "chapter"
        else "A generated view of the real .angl files: chapters, dependencies, intent, examples, source, artifacts, and evidence."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f7f5ef;
      --panel: #ffffff;
      --panel-2: #f0eee6;
      --ink: #161616;
      --muted: #6f6a60;
      --line: #d8d2c4;
      --code-bg: #1d1f1c;
      --code-ink: #f1efe3;
      --green: #167341;
      --red: #b42318;
      --amber: #a15c07;
      --blue: #2454a6;
    }}
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; scroll-behavior: smooth; }}
    body {{
      margin: 0;
      overflow-x: hidden;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-serif, Georgia, "Times New Roman", serif;
      line-height: 1.5;
    }}
    .page {{
      display: grid;
      grid-template-columns: 18rem minmax(0, 1fr) 18rem;
      min-height: 100vh;
    }}
    aside {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      border-right: 1px solid var(--line);
      background: var(--panel-2);
      padding: 1.2rem 1rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .inspector {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      border-left: 1px solid var(--line);
      background: var(--panel-2);
      padding: 1.2rem 1rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    main {{ padding: 2rem clamp(1.2rem, 4vw, 4rem) 5rem; min-width: 0; }}
    h1, h2, h3 {{ line-height: 1.1; margin: 0; }}
    h1 {{ font-size: clamp(2rem, 5vw, 4.5rem); letter-spacing: -0.06em; }}
    h2 {{ font-size: clamp(1.7rem, 3vw, 2.8rem); letter-spacing: -0.04em; }}
    h3 {{ font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); }}
    .dek {{ color: var(--muted); font-size: 1.12rem; max-width: 48rem; }}
    .toc {{ list-style: none; margin: 1.5rem 0 0; padding: 0; display: grid; gap: 0.75rem; }}
    .toc a {{
      display: block;
      color: inherit;
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 0.6rem;
      padding: 0.75rem;
      background: var(--panel);
    }}
    .toc a:hover {{ border-color: #8a7658; }}
    .tiny {{ color: var(--muted); font-size: 0.85rem; }}
    .badge {{
      display: inline-block;
      border: 1px solid currentColor;
      border-radius: 999px;
      padding: 0.12rem 0.5rem;
      font-size: 0.76rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .target {{ color: var(--blue); }}
    .pass {{ color: var(--green); }}
    .fail {{ color: var(--red); }}
    .unknown {{ color: var(--amber); }}
    .hero {{
      border-bottom: 1px solid var(--line);
      padding-bottom: 1.4rem;
      margin-bottom: 1rem;
    }}
    .chapter {{
      margin-top: 1.4rem;
      padding: clamp(1.1rem, 3vw, 2rem);
      border: 1px solid var(--line);
      border-radius: 0.75rem;
      background: var(--panel);
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.75rem;
      margin: 1.25rem 0;
    }}
    .meta div {{
      border-top: 1px solid var(--line);
      padding-top: 0.55rem;
      min-width: 0;
    }}
    .chapter-title {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }}
    .chapter-title p {{ margin: 0.4rem 0 0; color: var(--muted); }}
    code, pre {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    code {{
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: var(--code-bg);
      color: var(--code-ink);
      padding: 1rem;
      border-radius: 0.65rem;
      font-size: 0.92rem;
    }}
    .intent {{
      font-size: 1.08rem;
      max-width: 58rem;
      white-space: pre-wrap;
    }}
    .chapter-prose {{
      max-width: 62rem;
      font-size: 1.12rem;
    }}
    .chapter-prose p {{ margin: 0.75rem 0; }}
    .chapter-prose ul {{ margin: 0.5rem 0 1rem 1.2rem; padding: 0; }}
    .chapter-prose li {{ margin: 0.35rem 0; }}
    .chapter-prose .callout {{
      border-left: 4px solid var(--blue);
      background: #f2f6ff;
      padding: 0.85rem 1rem;
      border-radius: 0.45rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .scenario-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 0.8rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 0.92rem;
    }}
    .scenario-table th {{
      text-align: left;
      color: var(--muted);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      border-bottom: 1px solid var(--line);
      padding: 0.55rem;
    }}
    .scenario-table td {{
      vertical-align: top;
      border-bottom: 1px solid var(--line);
      padding: 0.65rem 0.55rem;
    }}
    .scenario-table code {{
      display: block;
      font-size: 0.82rem;
      color: #34312c;
    }}
    .example-cards {{
      display: grid;
      gap: 1rem;
      margin-top: 0.8rem;
    }}
    .example-card {{
      border: 1px solid var(--line);
      border-radius: 0.75rem;
      background: #fffaf0;
      padding: 1rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .example-card h4 {{
      margin: 0 0 0.65rem;
      color: #7b5208;
      font-family: ui-serif, Georgia, "Times New Roman", serif;
      font-size: 1.25rem;
      line-height: 1.25;
    }}
    .example-card p {{ margin: 0.45rem 0; }}
    .example-card strong {{ color: var(--muted); }}
    .example-card details {{
      margin-top: 0.8rem;
      background: #f8f2e7;
    }}
    details {{
      border: 1px solid var(--line);
      border-radius: 0.65rem;
      margin-top: 1rem;
      background: #fbfaf6;
    }}
    summary {{
      cursor: pointer;
      padding: 0.8rem 1rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
      color: #292720;
    }}
    details pre {{ margin: 0 1rem 1rem; }}
    .warning {{
      border-left: 4px solid var(--amber);
      background: #fff7ed;
      padding: 0.7rem 0.8rem;
      border-radius: 0.45rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 0.9rem;
      margin: 0.7rem 0;
    }}
    .graph {{
      display: grid;
      gap: 0.5rem;
      margin-top: 0.8rem;
    }}
    .graph a {{
      color: inherit;
      text-decoration: none;
    }}
    .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 0.6rem;
      padding: 0.8rem;
      margin-bottom: 0.8rem;
    }}
    .panel h3 {{ margin-bottom: 0.5rem; }}
    .panel p {{ margin: 0.35rem 0; }}
    .tree {{
      margin: 0.5rem 0 0;
      padding: 0;
      list-style: none;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.84rem;
    }}
    .tree li {{ margin: 0.35rem 0; }}
    .tree a {{ color: inherit; text-decoration: none; }}
    .tree a:hover {{ text-decoration: underline; }}
    .artifact {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.82rem;
      overflow-wrap: anywhere;
    }}
    @media (max-width: 900px) {{
      .page {{ display: block; }}
      aside {{ position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      .inspector {{ position: relative; height: auto; border-left: 0; border-top: 1px solid var(--line); }}
      .meta {{ grid-template-columns: 1fr; }}
      .scenario-table, .scenario-table tbody, .scenario-table tr, .scenario-table td, .scenario-table th {{ display: block; }}
      .scenario-table thead {{ display: none; }}
      .scenario-table td::before {{ display: block; color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; }}
      .scenario-table td:nth-child(1)::before {{ content: "Scenario"; }}
      .scenario-table td:nth-child(2)::before {{ content: "Input"; }}
      .scenario-table td:nth-child(3)::before {{ content: "Expected"; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <aside>
      <h3>Project Index</h3>
      <p class="tiny">{html.escape(_rel(book['root']))}</p>
      <ol class="toc">
        {toc}
      </ol>
    </aside>
    <main>
      <section class="hero">
        <h1>{html.escape(heading)}</h1>
        <p class="dek">{html.escape(dek)}</p>
      </section>
      {chapters}
    </main>
    <section class="inspector">
      {summary}
    </section>
  </div>
</body>
</html>
"""


def write_html(book, out_path, view="reader"):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(render_html(book, view=view))


def write_markdown(book, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(render_markdown(book))


def book_json(book):
    return {
        "root": book["root"],
        "build_dir": book["build_dir"],
        "project": book.get("project"),
        "order": book["order"],
        "units": [
            {
                "name": unit["name"],
                "interface": unit["interface"],
                "target": unit["target"],
                "uses": unit["uses"],
                "used_by": unit["used_by"],
                "source": unit["source"],
                "source_text": unit["source_text"],
                "source_mtime": unit["source_mtime"],
                "artifact": unit["artifact"],
                "artifact_text": unit["artifact_text"],
                "artifact_mtime": unit["artifact_mtime"],
                "artifact_status": unit["artifact_status"],
                "manifest": unit["manifest"],
                "result": unit["result"],
                "cases": unit["case_rows"],
                "style_findings": unit["style_findings"],
            }
            for unit in book["units"]
        ],
    }


def serve_book(spec_path, build_dir=None, results_path=None, view="chapter", host="127.0.0.1", port=8782):
    spec_path = os.path.abspath(spec_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlparse(self.path).path
            try:
                book = load_book(spec_path, build_dir=build_dir, results_path=results_path)
                if route in {"/", "/chapter"}:
                    self._send(render_html(book, view="chapter"), "text/html; charset=utf-8")
                elif route == "/reader":
                    self._send(render_html(book, view="reader"), "text/html; charset=utf-8")
                elif route == "/markdown":
                    self._send(render_markdown(book), "text/markdown; charset=utf-8")
                elif route == "/api/book":
                    self._send(json.dumps(book_json(book), indent=2), "application/json; charset=utf-8")
                else:
                    self.send_error(404)
            except Exception as e:
                self._send(str(e), "text/plain; charset=utf-8", status=500)

        def log_message(self, fmt, *args):
            print(f"{self.address_string()} - {fmt % args}")

        def _send(self, text, content_type, status=200):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"angl source reader: http://{host}:{port}/chapter")
    print(f"reader view:        http://{host}:{port}/reader")
    print(f"markdown view:      http://{host}:{port}/markdown")
    print(f"book json:          http://{host}:{port}/api/book")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped angl source reader")
    finally:
        server.server_close()


def _load_units_with_paths(spec_path):
    specs_dir = os.path.dirname(os.path.abspath(spec_path))
    units = {}
    paths = {}

    def load(path, requested_as=None):
        if not os.path.exists(path):
            dep = requested_as or os.path.splitext(os.path.basename(path))[0]
            raise ValueError(f"missing # uses dependency: {dep} ({path})")
        with open(path) as f:
            spec = parse(f.read())
        units[spec["name"]] = spec
        paths[spec["name"]] = os.path.abspath(path)
        for dep in spec["uses"]:
            if dep not in units:
                load(os.path.join(specs_dir, dep + ".angl"), dep)

    load(spec_path)
    return units, paths


def _chapter(spec, path, build_dir, used_by, result):
    artifact = os.path.join(build_dir, f"{spec['func']}{TARGET_EXT[spec.get('target', 'python')]}")
    with open(path) as f:
        source_text = f.read()
    artifact_text = None
    artifact_status = "missing"
    manifest = _load_manifest(build_dir, spec)
    source_mtime = os.path.getmtime(path)
    artifact_mtime = None
    if os.path.exists(artifact):
        artifact_mtime = os.path.getmtime(artifact)
        artifact_status = "older than source" if artifact_mtime < source_mtime else "present"
        with open(artifact) as f:
            artifact_text = f.read()
    return {
        "name": spec["name"],
        "func": spec["func"],
        "interface": spec["interface"],
        "target": spec.get("target", "python"),
        "uses": spec["uses"],
        "used_by": used_by,
        "intent": spec["intent"],
        "cases": [case["raw"] for case in spec["cases"]],
        "case_rows": _case_rows(spec["cases"]),
        "style_findings": intent_style_findings(spec),
        "source": path,
        "source_text": source_text,
        "source_mtime": source_mtime,
        "artifact": os.path.abspath(artifact) if os.path.exists(artifact) else None,
        "artifact_text": artifact_text,
        "artifact_mtime": artifact_mtime,
        "artifact_status": artifact_status,
        "manifest": manifest,
        "result": result,
    }


def _load_manifest(build_dir, spec):
    path = os.path.join(build_dir, f"{spec['func']}.manifest.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        manifest = json.load(f)
    manifest["path"] = os.path.abspath(path)
    return manifest


def _load_results(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        rows = json.load(f)
    latest = {}
    for row in rows:
        for unit in row.get("units", []):
            latest[unit["name"]] = unit
    return latest


def _downstream_map(units):
    downstream = {name: [] for name in units}
    for name, spec in units.items():
        for dep in spec["uses"]:
            downstream.setdefault(dep, []).append(name)
    return {name: sorted(values) for name, values in downstream.items()}


def _status_label(unit):
    result = unit.get("result")
    if not result:
        return "not run"
    status = "green" if result.get("ok") else "red"
    return f"{status} {result.get('passed')}/{result.get('total')} attempts={result.get('attempts')}"


def _status_class(unit):
    result = unit.get("result")
    if not result:
        return "unknown"
    return "pass" if result.get("ok") else "fail"


def _toc_row(idx, unit):
    label = _status_label(unit)
    return (
        f'<li><a href="#{html.escape(unit["name"])}">'
        f'<strong>{idx}. {html.escape(unit["name"])}</strong><br>'
        f'<span class="tiny">{html.escape(_rel(unit["source"]))}</span><br>'
        f'<span class="badge target">{html.escape(unit["target"])}</span> '
        f'<span class="badge {_status_class(unit)}">{html.escape(label)}</span>'
        "</a></li>"
    )


def _chapter_html(idx, unit, view="reader"):
    if view == "chapter":
        return _chapter_prose_html(idx, unit)

    cases = "\n".join(_case_row_html(case) for case in unit["case_rows"])
    uses = ", ".join(unit["uses"]) if unit["uses"] else "none"
    used_by = ", ".join(unit["used_by"]) if unit["used_by"] else "none"
    artifact = _rel(unit["artifact"]) if unit["artifact"] else "not generated"
    warnings = "\n".join(_warning_html(finding) for finding in unit["style_findings"])
    artifact_details = _artifact_details(unit)
    return f"""
      <section class="chapter" id="{html.escape(unit['name'])}">
        <div class="chapter-title">
          <div>
            <h3>Chapter {idx}</h3>
            <h2>{html.escape(_title(unit['name']))}</h2>
            <p><code>{html.escape(unit['func'])}</code> from <code>{html.escape(_rel(unit['source']))}</code></p>
          </div>
          <div>
            <span class="badge target">{html.escape(unit['target'])}</span>
            <span class="badge {_status_class(unit)}">{html.escape(_status_label(unit))}</span>
          </div>
        </div>
        <div class="meta">
          <div><strong>Interface</strong><br><code>{html.escape(unit['interface'])}</code></div>
          <div><strong>Target</strong><br>{html.escape(unit['target'])}</div>
          <div><strong>Uses</strong><br>{html.escape(uses)}</div>
          <div><strong>Used by</strong><br>{html.escape(used_by)}</div>
          <div><strong>Source</strong><br><code>{html.escape(_rel(unit['source']))}</code></div>
          <div><strong>Generated artifact</strong><br><code>{html.escape(artifact)}</code></div>
          <div><strong>Artifact status</strong><br>{html.escape(unit['artifact_status'])}</div>
        </div>
        <h3>Intent</h3>
        <p class="intent">{html.escape(unit['intent'])}</p>
        {warnings}
        <h3>Examples</h3>
        <table class="scenario-table">
          <thead>
            <tr><th>Scenario</th><th>Input</th><th>Expected</th></tr>
          </thead>
          <tbody>{cases}</tbody>
        </table>
        <details>
          <summary>Show exact .angl source</summary>
          <pre>{html.escape(unit['source_text'])}</pre>
        </details>
        {artifact_details}
      </section>
    """


def _chapter_prose_html(idx, unit):
    cases = "\n".join(_case_card_html(case) for case in unit["case_rows"])
    warnings = "\n".join(_warning_html(finding) for finding in unit["style_findings"])
    artifact_details = _artifact_details(unit)
    use_sentence = _uses_sentence(unit)
    used_by_sentence = _used_by_sentence(unit)
    artifact = _rel(unit["artifact"]) if unit["artifact"] else "No generated edition was found for this chapter."
    return f"""
      <section class="chapter" id="{html.escape(unit['name'])}">
        <div class="chapter-title">
          <div>
            <h3>Chapter {idx}</h3>
            <h2>{html.escape(_title(unit['name']))}</h2>
            <p>{html.escape(_rel(unit['source']))}</p>
          </div>
          <div>
            <span class="badge target">{html.escape(unit['target'])}</span>
            <span class="badge {_status_class(unit)}">{html.escape(_status_label(unit))}</span>
          </div>
        </div>
        <div class="chapter-prose">
          <p class="callout">{html.escape(_boundary_sentence(unit))}</p>
          <p>{html.escape(use_sentence)}</p>
          <p>{html.escape(used_by_sentence)}</p>
          <p>When compiled, the current edition is built as <strong>{html.escape(unit['target'])}</strong>. The generated artifact is <code>{html.escape(artifact)}</code>.</p>
          <p>Artifact status: <strong>{html.escape(unit['artifact_status'])}</strong>.</p>
          <h3>What This Chapter Says</h3>
          <p>{html.escape(unit['intent'])}</p>
          {warnings}
          <h3>Examples It Must Keep True</h3>
        </div>
        <div class="example-cards">{cases}</div>
        <details>
          <summary>Show exact stored .angl source</summary>
          <pre>{html.escape(unit['source_text'])}</pre>
        </details>
        {artifact_details}
      </section>
    """


def _summary_html(book):
    targets = {}
    generated = 0
    manifests = 0
    for unit in book["units"]:
        targets[unit["target"]] = targets.get(unit["target"], 0) + 1
        if unit["artifact"]:
            generated += 1
        if unit.get("manifest"):
            manifests += 1
    target_rows = "".join(
        f'<p><span class="badge target">{html.escape(target)}</span> {count} chapter{"s" if count != 1 else ""}</p>'
        for target, count in sorted(targets.items())
    )
    tree = "\n".join(
        f'<li><a href="#{html.escape(unit["name"])}">{html.escape(_rel(unit["source"]))}</a></li>'
        for unit in book["units"]
    )
    graph = "\n".join(_graph_row(unit) for unit in book["units"])
    project_panel = _project_panel_html(book.get("project"))
    return f"""
      {project_panel}
      <div class="panel">
        <h3>Source Tree</h3>
        <ul class="tree">{tree}</ul>
      </div>
      <div class="panel">
        <h3>Compiled Editions</h3>
        <p>{generated}/{len(book['units'])} generated artifacts found</p>
        <p>{manifests}/{len(book['units'])} compiler manifests found</p>
        {target_rows}
      </div>
      <div class="panel">
        <h3>Dependency Graph</h3>
        <div class="graph">{graph}</div>
      </div>
    """


def _project_panel_html(project):
    if not project:
        return ""
    stack_rows = "".join(
        f"<p><strong>{html.escape(key)}</strong><br><code>{html.escape(value)}</code></p>"
        for key, value in sorted(project["stack"].items())
    )
    rules = "".join(f"<li>{html.escape(rule)}</li>" for rule in project.get("rules", []))
    return f"""
      <div class="panel">
        <h3>Project Stack</h3>
        <p class="tiny">{html.escape(_rel(project['path']))}</p>
        {stack_rows}
        <ul>{rules}</ul>
      </div>
    """


def _boundary_sentence(unit):
    parsed = _parse_interface(unit["interface"])
    if not parsed:
        return f"The rest of the app can use this chapter through {unit['interface']}."
    args = parsed["args"]
    returns = parsed["returns"]
    if args:
        arg_text = _join_english([_article(arg["name"].replace("_", " ")) for arg in args])
        return (
            f"The rest of the app gives this chapter {arg_text}, "
            f"and this chapter returns {_return_phrase(returns)}."
        )
    return f"The rest of the app can ask this chapter for {_return_phrase(returns)}."


def _return_phrase(returns):
    normalized = returns.replace("_", " ").strip()
    if normalized == "object":
        return "a result object"
    if normalized == "number":
        return "a number"
    if normalized == "string":
        return "text"
    if normalized == "boolean":
        return "true or false"
    return _article(normalized)


def _article(text):
    stripped = text.strip()
    if not stripped:
        return stripped
    first = stripped[0].lower()
    article = "an" if first in "aeiou" else "a"
    if stripped.startswith(("a ", "an ", "the ")):
        return stripped
    return f"{article} {stripped}"


def _parse_interface(interface):
    head, sep, returns = interface.partition("->")
    if not sep or "(" not in head or ")" not in head:
        return None
    func = head.split("(", 1)[0].strip()
    args_text = head.split("(", 1)[1].rsplit(")", 1)[0].strip()
    args = []
    if args_text:
        for arg in _split_top_level_text(args_text):
            name, _, typ = arg.partition(":")
            args.append({"name": name.strip(), "type": typ.strip()})
    return {"func": func, "args": args, "returns": returns.strip()}


def _uses_sentence(unit):
    if not unit["uses"]:
        return "This chapter stands on its own; it does not call another Angl chapter."
    names = [_title(name) for name in unit["uses"]]
    return "This chapter builds on " + _join_english(names) + "."


def _used_by_sentence(unit):
    if not unit["used_by"]:
        return "No later chapter in this project currently depends on it."
    names = [_title(name) for name in unit["used_by"]]
    return "Later, " + _join_english(names) + " depends on this chapter."


def _join_english(items):
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _graph_row(unit):
    if not unit["uses"]:
        relation = "no dependencies"
    else:
        relation = "uses " + ", ".join(f'<a href="#{html.escape(dep)}">{html.escape(dep)}</a>' for dep in unit["uses"])
    return f'<p><code>{html.escape(unit["name"])}</code><br><span class="tiny">{relation}</span></p>'


def _case_rows(cases):
    rows = []
    for case in cases:
        raw = case["raw"]
        raw_left, raw_right = _split_case_raw_parts(raw)
        left, right = _split_case_raw(raw)
        scenario = case.get("scenario") or "example"
        situation, promise = _split_scenario(scenario)
        rows.append({
            "scenario": scenario,
            "situation": situation,
            "promise": promise,
            "input": left,
            "expected": right,
            "given_summary": _data_summary(raw_left, scenario),
            "expected_summary": _data_summary(raw_right, scenario),
            "raw": raw,
        })
    return rows


def _split_scenario(scenario):
    text = scenario.strip().rstrip(".")
    if text.lower().startswith("when "):
        text = text[5:]
    then_match = re.search(r"\s+then\s+", text, re.I)
    if then_match:
        left = text[:then_match.start()]
        right = text[then_match.end():]
        return left.strip(), right.strip()
    if ", " in text:
        left, right = text.split(", ", 1)
        return left.strip(), right.strip()
    return text, "the expected result must match"


def _split_case_raw(raw):
    left, right = _split_case_raw_parts(raw)
    return _pretty_case_side(left.strip()), _pretty_case_side(right.strip())


def _split_case_raw_parts(raw):
    if "->" not in raw:
        return raw, ""
    left, right = raw.split("->", 1)
    return left.strip(), right.strip()


def _pretty_case_side(value):
    if value.startswith("!error"):
        return value
    parts = []
    for piece in _split_top_level_text(value):
        piece = piece.strip()
        try:
            parts.append(json.dumps(json.loads(piece), indent=2, sort_keys=True))
        except json.JSONDecodeError:
            parts.append(piece)
    return "\n\n".join(parts)


def _data_summary(value, context=""):
    if value.startswith("!error"):
        return _error_summary(value)
    summaries = []
    for piece in _split_top_level_text(value):
        piece = piece.strip()
        try:
            summaries.append(_summarize_json(json.loads(piece), context))
        except json.JSONDecodeError:
            summaries.append(piece)
    if len(summaries) <= 1:
        return summaries[0] if summaries else ""
    return ". Also, ".join(summaries)


def _error_summary(value):
    marker = '!error contains "'
    if value.startswith(marker) and value.endswith('"'):
        return f'an error mentioning "{value[len(marker):-1]}"'
    return value


def _summarize_json(value, context=""):
    if isinstance(value, dict):
        if not value:
            return "an empty object"
        items = _prioritized_items(value, context)
        shown = []
        limit = 8
        for key, val in items[:limit]:
            shown.append(f"{_field_label(key)} is {_short_value(val)}")
        if len(items) > limit:
            shown.append(f"{len(items) - limit} more field{'s' if len(items) - limit != 1 else ''}")
        return "; ".join(shown)
    if isinstance(value, list):
        return "a list with " + ", ".join(_short_value(item) for item in value[:6])
    return _short_value(value)


def _prioritized_items(value, context):
    items = list(value.items())
    context_words = set(_words(context))

    def score(item):
        key, _ = item
        words = set(_words(key))
        return 0 if words & context_words else 1

    return [item for _, item in sorted(enumerate(items), key=lambda pair: (score(pair[1]), pair[0]))]


def _words(text):
    return [word for word in text.lower().replace("_", " ").split() if word]


def _field_label(key):
    return key


def _short_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if len(value) <= 4:
            return "a list containing " + _join_english([_short_value(item) for item in value])
        return f"a list of {len(value)} items"
    if isinstance(value, dict):
        keys = list(value)[:4]
        if len(value) <= 4:
            return "an object with " + _join_english(keys)
        remaining = len(value) - 4
        return (
            "an object with "
            + ", ".join(keys)
            + f", and {remaining} more field{'s' if remaining != 1 else ''}"
        )
    return repr(value)


def _split_top_level_text(text):
    parts, depth, cur, in_string, escaped = [], 0, "", False, False
    for ch in text:
        if escaped:
            escaped = False
        elif in_string:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1

        if ch == "," and depth == 0 and not in_string:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _case_row_html(case):
    return (
        "<tr>"
        f"<td>{html.escape(case['scenario'])}</td>"
        f"<td><code>{html.escape(case['input'])}</code></td>"
        f"<td><code>{html.escape(case['expected'])}</code></td>"
        "</tr>"
    )


def _case_card_html(case):
    return f"""
      <article class="example-card">
        <h4>{html.escape(case['situation'])}</h4>
        <p><strong>Given:</strong> {html.escape(case['given_summary'])}</p>
        <p><strong>Must:</strong> {html.escape(case['promise'])}</p>
        <p><strong>Expected:</strong> {html.escape(case['expected_summary'])}</p>
        <details>
          <summary>Show exact case data</summary>
          <pre>{html.escape(case['raw'])}</pre>
        </details>
      </article>
    """


def _warning_html(finding):
    return (
        '<div class="warning">'
        f"Intent style warning, line {finding['line']}: "
        f"{html.escape(finding['message'])}"
        f" <code>{html.escape(finding['term'])}</code>"
        "</div>"
    )


def _artifact_details(unit):
    if not unit["artifact_text"]:
        return ""
    return (
        "<details>"
        f"<summary>Show generated {html.escape(unit['target'])} edition</summary>"
        f"<p class=\"artifact\">{html.escape(_rel(unit['artifact']))}</p>"
        f"<pre>{html.escape(unit['artifact_text'])}</pre>"
        "</details>"
    )


def _title(name):
    return name.replace("_", " ").title()


def _indent(lines):
    return [f"  {line}" for line in lines]


def _rel(path):
    if not path:
        return ""
    try:
        return os.path.relpath(path, REPO_ROOT)
    except ValueError:
        return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="render an Angl program as a project book")
    parser.add_argument("spec")
    parser.add_argument("--build-dir", default=os.path.join(REPO_ROOT, "build"))
    parser.add_argument("--results", default=None, help="optional evaluation JSON")
    parser.add_argument("--html", default=None, help="write a static HTML book to this path")
    parser.add_argument("--markdown", default=None, help="write a Markdown chapter view to this path")
    parser.add_argument("--serve", action="store_true", help="serve a live local source reader")
    parser.add_argument("--host", default="127.0.0.1", help="host for --serve")
    parser.add_argument("--port", type=int, default=8782, help="port for --serve")
    parser.add_argument(
        "--view",
        choices=["reader", "chapter"],
        default="reader",
        help="reader shows IDE-style source metadata; chapter reads like Markdown prose",
    )
    args = parser.parse_args(argv)

    book = load_book(args.spec, build_dir=args.build_dir, results_path=args.results)
    if args.serve:
        serve_book(
            args.spec,
            build_dir=args.build_dir,
            results_path=args.results,
            view=args.view,
            host=args.host,
            port=args.port,
        )
    elif args.html:
        write_html(book, args.html, view=args.view)
        print(args.html)
    elif args.markdown:
        write_markdown(book, args.markdown)
        print(args.markdown)
    else:
        print(render_text(book), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
