import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

AI_CLI_CHOICES = ("auto", "codex", "claude")
AI_CLI_LABELS = {
    "codex": "Codex",
    "claude": "Claude",
}
FALLBACK_ERROR_PATTERNS = (
    "invalid_json_schema",
    "schema must be",
    "rate limit",
    "usage limit",
    "quota",
    "credit",
    "billing",
    "subscription",
    "not logged in",
    "login required",
    "authentication",
    "unauthorized",
    "forbidden",
    "expired",
    "overloaded",
    "capacity",
    "permission denied",
    "readonly database",
    "thread/start",
)

REVIEW_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "line": {"type": "integer"},
            "confidence": {"type": "integer"},
            "type": {"type": "string", "enum": ["must", "nit", "test"]},
            "comment": {"type": "string"},
        },
        "required": ["line", "confidence", "type", "comment"],
        "additionalProperties": False,
    },
}


def normalize_ai_cli(value: Optional[str]) -> str:
    if not value:
        return "auto"

    normalized = value.strip().lower()
    aliases = {
        "code": "codex",
        "code-cli": "codex",
        "claude-code": "claude",
    }
    return aliases.get(normalized, normalized)


def resolve_ai_cli(config: Optional[dict] = None) -> str:
    config = config or {}
    requested = normalize_ai_cli(
        os.environ.get("SENSEI_AI_CLI") or config.get("ai_cli")
    )

    if requested == "auto":
        return choose_auto_candidates(config)[0]

    if requested not in AI_CLI_LABELS:
        raise RuntimeError(
            f"Unsupported AI CLI '{requested}'. "
            f"Choose one of: {', '.join(AI_CLI_CHOICES)}."
        )

    if not shutil.which(requested):
        raise RuntimeError(
            f"{AI_CLI_LABELS[requested]} CLI is not installed or not on PATH."
        )

    return requested


def get_ai_cli_status(provider: str) -> dict:
    if provider not in AI_CLI_LABELS:
        raise RuntimeError(f"Unsupported AI CLI '{provider}'.")

    path = shutil.which(provider)
    if not path:
        return {
            "provider": provider,
            "label": AI_CLI_LABELS[provider],
            "installed": False,
            "authenticated": False,
            "detail": "Not installed",
            "path": "",
        }

    if provider == "claude":
        status = _get_claude_status(path)
    else:
        status = _get_codex_status(path)

    status["provider"] = provider
    status["label"] = AI_CLI_LABELS[provider]
    status["installed"] = True
    status["path"] = path
    return status


def choose_auto_candidates(config: Optional[dict] = None) -> list:
    ordered = []
    fallback_order = []

    for provider in ("codex", "claude"):
        status = get_ai_cli_status(provider)
        if not status["installed"]:
            continue
        if status.get("authenticated"):
            ordered.append(provider)
        else:
            fallback_order.append(provider)

    ordered.extend(fallback_order)
    if not ordered:
        raise RuntimeError(
            "No supported AI CLI found. Install Codex CLI or Claude CLI, "
            "or set SENSEI_AI_CLI to a valid provider."
        )
    return ordered


def resolve_ai_cli_candidates(config: Optional[dict] = None) -> list:
    config = config or {}
    cached_candidates = config.get("_resolved_ai_cli_candidates")
    if cached_candidates:
        return cached_candidates

    requested = normalize_ai_cli(
        os.environ.get("SENSEI_AI_CLI") or config.get("ai_cli")
    )
    fallback_enabled = config.get("fallback_ai_cli", True)

    if requested == "auto":
        candidates = choose_auto_candidates(config)
        if not fallback_enabled:
            candidates = candidates[:1]
        config["_resolved_ai_cli_candidates"] = candidates
        return candidates

    primary = resolve_ai_cli(config)
    candidates = [primary]
    if fallback_enabled:
        for candidate in ("codex", "claude"):
            if candidate != primary and shutil.which(candidate):
                candidates.append(candidate)
    config["_resolved_ai_cli_candidates"] = candidates
    return candidates


def get_ai_label(config: Optional[dict] = None) -> str:
    candidates = resolve_ai_cli_candidates(config)
    if not candidates:
        return "AI"
    return AI_CLI_LABELS.get(candidates[0], "AI")


def run_prompt(
    prompt: str,
    timeout: int,
    config: Optional[dict] = None,
    schema: Optional[dict] = None,
    reasoning_effort: Optional[str] = None,
) -> str:
    config = config or {}
    candidates = resolve_ai_cli_candidates(config)
    model = os.environ.get("SENSEI_MODEL") or config.get("model", "")
    status_callback = _get_status_callback(config)
    errors = []

    for index, provider in enumerate(candidates):
        try:
            if provider == "claude":
                return _run_claude(prompt, timeout, model)
            return _run_codex(prompt, timeout, model, schema, reasoning_effort)
        except RuntimeError as exc:
            errors.append(f"{AI_CLI_LABELS[provider]}: {exc}")
            has_fallback = index < len(candidates) - 1
            if has_fallback and _should_retry_with_fallback(str(exc)):
                next_provider = candidates[index + 1]
                if status_callback:
                    status_callback(
                        f"{AI_CLI_LABELS[provider]} failed ({_summarize_error(str(exc))}); "
                        f"retrying with {AI_CLI_LABELS[next_provider]}."
                    )
                continue
            break

    raise RuntimeError(" ; ".join(errors))


def _run_claude(prompt: str, timeout: int, model: str) -> str:
    cmd = ["claude", "-p", "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(_format_cli_error("Claude CLI", result))
    return result.stdout.strip()


def _run_codex(
    prompt: str,
    timeout: int,
    model: str,
    schema: Optional[dict],
    reasoning_effort: Optional[str],
) -> str:
    output_path = None
    schema_path = None

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as output_file:
            output_path = output_file.name

        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-last-message",
            output_path,
        ]
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])

        if schema:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as schema_file:
                json.dump(_wrap_codex_schema(schema), schema_file)
                schema_path = schema_file.name
            cmd.extend(["--output-schema", schema_path])

        cmd.append("-")

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(_format_cli_error("Codex CLI", result))

        return _unwrap_codex_output(Path(output_path).read_text().strip(), schema)
    finally:
        if output_path and os.path.exists(output_path):
            os.unlink(output_path)
        if schema_path and os.path.exists(schema_path):
            os.unlink(schema_path)


def _format_cli_error(label: str, result: subprocess.CompletedProcess) -> str:
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    details = stderr or stdout or "No error details were returned."
    return f"{label} failed (exit code {result.returncode}): {details}"


def _should_retry_with_fallback(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in FALLBACK_ERROR_PATTERNS)


def _summarize_error(message: str) -> str:
    lowered = message.lower()
    for pattern in FALLBACK_ERROR_PATTERNS:
        if pattern in lowered:
            return pattern
    return "runtime error"


def _wrap_codex_schema(schema: dict) -> dict:
    normalized_schema = _normalize_for_codex_schema(schema)

    if normalized_schema.get("type") == "object":
        return normalized_schema

    return _normalize_for_codex_schema({
        "type": "object",
        "properties": {
            "comments": normalized_schema,
        },
        "required": ["comments"],
        "additionalProperties": False,
    })


def _unwrap_codex_output(raw_output: str, schema: Optional[dict]) -> str:
    if not schema or schema.get("type") == "object":
        return raw_output

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        return raw_output

    if isinstance(parsed, dict) and isinstance(parsed.get("comments"), list):
        return json.dumps(parsed["comments"])

    return raw_output


def _normalize_for_codex_schema(node: dict) -> dict:
    normalized = dict(node)

    if normalized.get("type") == "object":
        properties = normalized.get("properties", {})
        normalized["properties"] = {
            key: _normalize_for_codex_schema(value)
            if isinstance(value, dict) else value
            for key, value in properties.items()
        }
        normalized["additionalProperties"] = False
        return normalized

    if normalized.get("type") == "array" and isinstance(normalized.get("items"), dict):
        normalized["items"] = _normalize_for_codex_schema(normalized["items"])
        return normalized

    if isinstance(normalized.get("items"), dict):
        normalized["items"] = _normalize_for_codex_schema(normalized["items"])

    if isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {
            key: _normalize_for_codex_schema(value)
            if isinstance(value, dict) else value
            for key, value in normalized["properties"].items()
        }

    return normalized


def _get_status_callback(config: dict) -> Optional[Callable[[str], None]]:
    callback = config.get("_status_callback")
    if callable(callback):
        return callback
    return None


def _get_claude_status(path: str) -> dict:
    result = subprocess.run(
        [path, "auth", "status"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return {
            "authenticated": False,
            "detail": (result.stderr or result.stdout or "Unable to read auth status.").strip(),
        }

    raw = (result.stdout or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        authenticated = "loggedin" in raw.replace(" ", "").lower() or "subscription" in raw.lower()
        return {
            "authenticated": authenticated,
            "detail": raw or "Status available",
        }

    detail = parsed.get("subscriptionType") or parsed.get("authMethod") or "Authenticated"
    return {
        "authenticated": bool(parsed.get("loggedIn")),
        "detail": detail,
    }


def _get_codex_status(path: str) -> dict:
    result = subprocess.run(
        [path, "login", "status"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    raw = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    authenticated = result.returncode == 0 and "logged in" in raw.lower()
    detail = raw or "Status unavailable"
    return {
        "authenticated": authenticated,
        "detail": detail,
    }
