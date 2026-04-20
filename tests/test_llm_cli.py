from pathlib import Path

from sensei.llm_cli import (
    choose_auto_candidates,
    get_ai_cli_status,
    get_ai_label,
    _normalize_for_codex_schema,
    resolve_ai_cli,
    resolve_ai_cli_candidates,
    run_prompt,
    _unwrap_codex_output,
    _wrap_codex_schema,
)


def test_resolve_ai_cli_auto_prefers_codex(monkeypatch):
    monkeypatch.setattr(
        "sensei.llm_cli.choose_auto_candidates",
        lambda config=None: ["codex", "claude"],
    )
    assert resolve_ai_cli({"ai_cli": "auto"}) == "codex"


def test_get_ai_label_uses_resolved_provider(monkeypatch):
    monkeypatch.setattr(
        "sensei.llm_cli.resolve_ai_cli_candidates",
        lambda config=None: ["claude"],
    )
    assert get_ai_label({"ai_cli": "auto"}) == "Claude"


def test_resolve_ai_cli_candidates_adds_fallback(monkeypatch):
    monkeypatch.setattr(
        "sensei.llm_cli.shutil.which",
        lambda name: "/tmp/bin" if name in ("codex", "claude") else None,
    )
    assert resolve_ai_cli_candidates({"ai_cli": "codex", "fallback_ai_cli": True}) == [
        "codex",
        "claude",
    ]


def test_choose_auto_candidates_prefers_authenticated(monkeypatch):
    monkeypatch.setattr(
        "sensei.llm_cli.get_ai_cli_status",
        lambda provider: {
            "provider": provider,
            "installed": True,
            "authenticated": provider == "claude",
        },
    )
    assert choose_auto_candidates({}) == ["claude", "codex"]


def test_get_ai_cli_status_returns_not_installed(monkeypatch):
    monkeypatch.setattr("sensei.llm_cli.shutil.which", lambda name: None)
    status = get_ai_cli_status("codex")
    assert status["installed"] is False
    assert status["authenticated"] is False


def test_run_prompt_uses_claude_cli(monkeypatch):
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["input"] = input

        class Result:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return Result()

    monkeypatch.setattr("sensei.llm_cli.resolve_ai_cli", lambda config=None: "claude")
    monkeypatch.setattr("sensei.llm_cli.subprocess.run", fake_run)

    output = run_prompt("review this", timeout=30, config={"ai_cli": "claude"})

    assert output == "[]"
    assert captured["cmd"][:4] == ["claude", "-p", "--output-format", "text"]
    assert captured["input"] == "review this"


def test_run_prompt_uses_codex_exec_and_output_file(monkeypatch):
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        output_index = cmd.index("--output-last-message") + 1
        Path(cmd[output_index]).write_text('{"comments":[]}')

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("sensei.llm_cli.resolve_ai_cli", lambda config=None: "codex")
    monkeypatch.setattr("sensei.llm_cli.subprocess.run", fake_run)

    output = run_prompt(
        "review this",
        timeout=30,
        config={"ai_cli": "codex"},
        schema={"type": "array"},
    )

    assert output == "[]"
    assert captured["cmd"][:4] == ["codex", "exec", "--skip-git-repo-check", "--sandbox"]
    assert "read-only" in captured["cmd"]
    assert "--output-schema" in captured["cmd"]


def test_run_prompt_passes_reasoning_effort_to_codex(monkeypatch):
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        output_index = cmd.index("--output-last-message") + 1
        Path(cmd[output_index]).write_text("[]")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("sensei.llm_cli.subprocess.run", fake_run)

    output = run_prompt(
        "review this",
        timeout=30,
        config={"ai_cli": "codex"},
        reasoning_effort="low",
    )

    assert output == "[]"
    assert "-c" in captured["cmd"]
    assert "model_reasoning_effort=low" in captured["cmd"]


def test_wrap_codex_schema_converts_top_level_array():
    wrapped = _wrap_codex_schema({"type": "array", "items": {"type": "string"}})
    assert wrapped["type"] == "object"
    assert wrapped["properties"]["comments"]["type"] == "array"
    assert wrapped["additionalProperties"] is False


def test_wrap_codex_schema_adds_additional_properties_false_to_object():
    wrapped = _wrap_codex_schema({"type": "object", "properties": {"ok": {"type": "boolean"}}})
    assert wrapped["additionalProperties"] is False


def test_review_output_schema_disallows_unknown_fields():
    from sensei.llm_cli import REVIEW_OUTPUT_SCHEMA

    assert REVIEW_OUTPUT_SCHEMA["items"]["additionalProperties"] is False


def test_normalize_for_codex_schema_recursively_sets_object_constraints():
    normalized = _normalize_for_codex_schema({
        "type": "object",
        "properties": {
            "comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer"},
                    },
                    "required": ["line"],
                },
            },
        },
        "required": ["comments"],
    })

    assert normalized["additionalProperties"] is False
    assert normalized["properties"]["comments"]["items"]["additionalProperties"] is False


def test_unwrap_codex_output_returns_comments_array():
    raw = '{"comments":[{"line":1,"confidence":90,"type":"must","comment":"Code Review: Bug."}]}'
    output = _unwrap_codex_output(raw, {"type": "array"})
    assert output.startswith("[")
    assert '"line": 1' in output


def test_run_prompt_falls_back_on_quota_error(monkeypatch):
    calls = []
    messages = []

    def fake_claude(prompt, timeout, model):
        calls.append("claude")
        raise RuntimeError("Claude CLI failed (exit code 1): rate limit exceeded")

    def fake_codex(prompt, timeout, model, schema):
        calls.append("codex")
        return "[]"

    monkeypatch.setattr(
        "sensei.llm_cli.resolve_ai_cli_candidates",
        lambda config=None: ["claude", "codex"],
    )
    monkeypatch.setattr("sensei.llm_cli._run_claude", fake_claude)
    monkeypatch.setattr(
        "sensei.llm_cli._run_codex",
        lambda prompt, timeout, model, schema, reasoning_effort=None: fake_codex(
            prompt, timeout, model, schema
        ),
    )

    output = run_prompt("review this", timeout=30, config={"ai_cli": "claude"})

    assert output == "[]"
    assert calls == ["claude", "codex"]

    output = run_prompt(
        "review this",
        timeout=30,
        config={"ai_cli": "claude", "_status_callback": messages.append},
    )

    assert output == "[]"
    assert any("retrying with Codex" in message for message in messages)
