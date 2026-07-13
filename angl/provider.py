from __future__ import annotations

import json

from .config import get_config_value


def codex_model() -> str | None:
    model = get_config_value("codex_model", ["ANGL_CODEX_MODEL"])
    if model:
        return model
    generic = get_config_value("model", ["ANGL_MODEL"])
    if generic and generic.lower() not in {"sonnet", "opus", "haiku"}:
        return generic
    return None


def codex_failure_detail(output: str) -> str:
    detail = output.strip()
    if not detail:
        return "no error output"
    if "failed to initialize in-process app-server client" in detail:
        return (
            "failed to initialize Codex exec in this process: Operation not permitted\n"
            "Codex CLI is installed, but this process cannot start nested "
            "Codex execution. If you are running inside Codex, retry from a "
            "normal terminal."
        )
    messages = []
    for line in detail.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ERROR:"):
            continue
        payload = stripped[len("ERROR:"):].strip()
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            messages.append(payload)
            continue
        message = body.get("message")
        if isinstance(body.get("error"), dict):
            message = body["error"].get("message") or message
        messages.append(message or payload)
    if messages:
        return "\n".join(dict.fromkeys(messages))
    lines = [
        line
        for line in detail.splitlines()
        if line.strip()
        and not line.startswith("OpenAI Codex v")
        and not line.startswith("--------")
        and not line.startswith("workdir:")
        and not line.startswith("model:")
        and not line.startswith("provider:")
        and not line.startswith("approval:")
        and not line.startswith("sandbox:")
        and not line.startswith("reasoning")
        and not line.startswith("session id:")
        and line.strip() != "user"
    ]
    compact = "\n".join(lines[:12]) if lines else detail
    if len(compact) > 1200:
        compact = compact[:1200].rstrip() + "\n... output truncated ..."
    return compact
