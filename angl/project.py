"""Parse project-level Angl stack files.

Chapter files define public capabilities. Project files define stack defaults
that many chapters can refer to without repeating runtime decisions.
"""
import os
import re


DEFAULT_PROJECT_FILENAMES = ("angl.project", "angl.project.md", "angl.project.angl")


def find_project_file(start_path):
    cur = os.path.abspath(start_path)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while True:
        for name in DEFAULT_PROJECT_FILENAMES:
            path = os.path.join(cur, name)
            if os.path.exists(path):
                return path
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def load_project_for(start_path):
    path = find_project_file(start_path)
    if not path:
        return None
    with open(path) as f:
        data = parse_project(f.read())
    data["path"] = os.path.abspath(path)
    return data


def parse_project(text):
    title = None
    stack = {}
    rules = []
    entry_points = []
    section = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and title is None:
            title = stripped[2:].strip()
            continue
        anchor = _anchor(stripped)
        if anchor:
            key, value = anchor
            if _normalize_key(key) == "entry_points":
                entry_points = _parse_entry_points(value)
            else:
                stack[_normalize_key(key)] = value
            continue
        if stripped.startswith("## "):
            section = stripped[3:].strip().lower()
            continue
        if section == "rules" and stripped:
            rules.append(stripped[2:].strip() if stripped.startswith("- ") else stripped)

    return {
        "title": title or "Project",
        "stack": stack,
        "rules": rules,
        "entry_points": entry_points,
    }


def _anchor(stripped):
    m = re.match(r">\s*([^:]+):\s*(.+)$", stripped)
    if not m:
        return None
    value = m.group(2).strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1].strip()
    return m.group(1).strip(), value


def _normalize_key(key):
    return key.strip().lower().replace(" ", "_")


def _parse_entry_points(value):
    return re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", value)
