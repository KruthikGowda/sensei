from sensei.reviewer import build_file_review_prompt, build_similarity_prompt, judge_similarity, parse_json_review, parse_review_output, consolidate_test_comments, load_project_rules_from_repo, _has_error_handling, review_file


def test_build_file_review_prompt_includes_diff():
    prompt = build_file_review_prompt(
        file_path="src/auth.py",
        diff="+ def login():\n+     pass",
        file_content="def login():\n    pass",
        style_profile="Priorities: readability",
        project_rules="Use type hints everywhere",
        mr_context="MR: Add login endpoint",
    )
    assert "src/auth.py" in prompt
    assert "def login" in prompt
    assert "readability" in prompt
    assert "type hints" in prompt
    assert "Code Review:" in prompt  # example format
    assert '"type"' in prompt  # new type field in output format
    assert "AGENTS/CODEX/CLAUDE" in prompt
    assert "required plan updates" in prompt
    assert "Setu components" in prompt
    assert "existing chart wrappers" in prompt
    assert "lint/CI already enforces" in prompt


def test_build_file_review_prompt_uses_diff_only_for_lockfiles():
    prompt = build_file_review_prompt(
        file_path="yarn.lock",
        diff="+ foo@1.0.0",
        file_content="x" * 5000,
        style_profile="Priorities: readability",
        project_rules="Use type hints everywhere",
        mr_context="MR: refresh dependencies",
    )
    assert "generated file omitted to keep review fast" in prompt
    assert "Generated file detected. Review the diff directly" in prompt


def test_build_file_review_prompt_truncates_large_files_to_changed_context():
    file_content = "\n".join(
        [f"line {index}" for index in range(1, 4001)]
    )
    diff = "@@ -1198,3 +1200,4 @@\n+new logic\n@@ -2998,3 +3000,4 @@\n+other logic"

    prompt = build_file_review_prompt(
        file_path="src/big_file.ts",
        diff=diff,
        file_content=file_content,
        style_profile="Priorities: readability",
        project_rules="Use type hints everywhere",
        mr_context="MR: update big file",
    )

    assert "Relevant file context near changed lines" in prompt
    assert "# Lines 1180-1223" in prompt
    assert "# Lines 2980-3023" in prompt
    assert "line 1\nline 2\nline 3" not in prompt
    assert "line 3998\nline 3999\nline 4000" not in prompt


def test_parse_json_review_new_format():
    raw = '''[
        {
            "line": 12,
            "confidence": 92,
            "type": "must",
            "comment": "Code Review: The `user` parameter is not null-checked before accessing `.name`.\\n\\n\\u2022 Crashes at runtime if caller passes undefined\\n\\nSuggestion: Add a guard clause: `if (!user) return null;`"
        },
        {
            "line": 45,
            "confidence": 85,
            "type": "nit",
            "comment": "Code Review: Nested if/else adds unnecessary complexity.\\n\\n\\u2022 Prefer early returns over else\\n\\nSuggestion: Use an early return for the falsy case."
        }
    ]'''
    comments = parse_json_review(raw, "src/auth.py")
    assert len(comments) == 2
    assert comments[0]["line"] == 12
    assert comments[0]["confidence"] == 92
    assert comments[0]["type"] == "must"
    assert "Code Review:" in comments[0]["body"]
    assert comments[1]["type"] == "nit"


def test_parse_json_review_type_defaults_by_confidence():
    """When type field is missing, it defaults based on confidence threshold."""
    raw = '''[
        {"line": 10, "confidence": 95, "comment": "Code Review: Bug found."},
        {"line": 20, "confidence": 82, "comment": "Code Review: Style issue."}
    ]'''
    comments = parse_json_review(raw, "src/foo.py")
    assert len(comments) == 2
    assert comments[0]["type"] == "must"  # confidence 95 >= 90
    assert comments[1]["type"] == "nit"   # confidence 82 < 90


def test_parse_json_review_old_format_compat():
    raw = '''[
        {
            "line": 20,
            "confidence": 90,
            "observation": "Real bug",
            "rule": "No any",
            "suggestion": "Fix it"
        }
    ]'''
    comments = parse_json_review(raw, "src/foo.py")
    assert len(comments) == 1
    assert "Real bug" in comments[0]["body"]
    assert "Per our standards" in comments[0]["body"]
    assert comments[0]["type"] == "must"  # confidence 90 >= 90


def test_parse_json_review_filters_low_confidence():
    raw = '''[
        {"line": 10, "confidence": 75, "comment": "Minor nitpick"},
        {"line": 20, "confidence": 90, "comment": "Code Review: Real bug here."}
    ]'''
    comments = parse_json_review(raw, "src/foo.py")
    assert len(comments) == 1
    assert comments[0]["line"] == 20


def test_parse_json_review_empty_array():
    comments = parse_json_review("[]", "src/auth.py")
    assert len(comments) == 0


def test_parse_json_review_handles_markdown_wrapped():
    raw = '''```json
    [{"line": 5, "confidence": 85, "type": "nit", "comment": "Code Review: Bad name."}]
    ```'''
    comments = parse_json_review(raw, "src/foo.py")
    assert len(comments) == 1
    assert comments[0]["type"] == "nit"


def test_parse_json_review_handles_object_wrapped_comments():
    raw = '{"comments":[{"line":5,"confidence":85,"type":"nit","comment":"Code Review: Bad name."}]}'
    comments = parse_json_review(raw, "src/foo.py")
    assert len(comments) == 1
    assert comments[0]["type"] == "nit"


def test_parse_review_output_lgtm():
    raw = "LGTM"
    comments = parse_review_output(raw, "src/auth.py")
    assert len(comments) == 0


def test_has_error_handling_detects_patterns():
    assert _has_error_handling("try { foo() } catch (e) {}")
    assert _has_error_handling("promise.catch(err => {})")
    assert _has_error_handling("user?.name")
    assert _has_error_handling("value ?? fallback")
    assert not _has_error_handling("const x = 1 + 2;")


def test_parse_json_review_preserves_test_type():
    """Test type comments are preserved as 'test' and not coerced to must/nit."""
    raw = '''[
        {"line": 42, "confidence": 92, "type": "test", "comment": "New redirect logic needs tests"},
        {"line": 10, "confidence": 95, "type": "must", "comment": "Code Review: Bug."}
    ]'''
    comments = parse_json_review(raw, "src/foo.py")
    assert len(comments) == 2
    assert comments[0]["type"] == "test"
    assert comments[1]["type"] == "must"


def test_consolidate_test_comments_separates_types():
    comments = [
        {"file": "src/a.py", "line": 10, "type": "must", "body": "Bug found"},
        {"file": "src/a.py", "line": 20, "type": "test", "body": "Needs tests for redirect logic"},
        {"file": "src/b.py", "line": 5, "type": "nit", "body": "Rename var"},
        {"file": "src/b.py", "line": 15, "type": "test", "body": "Missing test for error handler"},
    ]
    review, test_summary = consolidate_test_comments(comments)

    # Review comments should not contain test type
    assert len(review) == 2
    assert all(c["type"] != "test" for c in review)

    # Test summary should be a markdown string with table
    assert test_summary is not None
    assert "## Test Coverage Summary" in test_summary
    assert "~80%" in test_summary
    assert "redirect logic" in test_summary
    assert "error handler" in test_summary


def test_consolidate_test_comments_no_test_gaps():
    comments = [
        {"file": "src/a.py", "line": 10, "type": "must", "body": "Bug found"},
    ]
    review, test_summary = consolidate_test_comments(comments)
    assert len(review) == 1
    assert test_summary is None


def test_consolidate_test_comments_deduplicates():
    comments = [
        {"file": "src/a.py", "line": 10, "type": "test", "body": "Needs tests for redirect logic with different params"},
        {"file": "src/a.py", "line": 20, "type": "test", "body": "Needs tests for redirect logic with different params"},
    ]
    review, test_summary = consolidate_test_comments(comments)
    assert len(review) == 0
    assert test_summary is not None
    # Should only have one row for the deduplicated entry
    assert test_summary.count("redirect logic") == 1


def test_load_project_rules_from_repo_returns_string():
    result = load_project_rules_from_repo(None, "fake/project", "main")
    assert isinstance(result, str)


def test_load_project_rules_from_repo_includes_agents_md():
    class FakeClient:
        def get_file_content(self, project_path, rule_file, ref):
            if rule_file == "AGENTS.md":
                return "Use Optional from typing."
            return ""

    result = load_project_rules_from_repo(FakeClient(), "fake/project", "main")
    assert "# From AGENTS.md" in result
    assert "Use Optional from typing." in result


def test_load_project_rules_from_repo_includes_plan_and_agent_rule_files():
    contents = {
        "PLANS.md": "Keep implementation plan in sync with behavior changes.",
        ".agents/rules.md": "Agents must preserve reviewer ownership boundaries.",
    }

    class FakeClient:
        def get_file_content(self, project_path, rule_file, ref):
            return contents.get(rule_file, "")

    result = load_project_rules_from_repo(FakeClient(), "fake/project", "main")
    assert "# From PLANS.md" in result
    assert "Keep implementation plan in sync" in result
    assert "# From .agents/rules.md" in result
    assert "preserve reviewer ownership boundaries" in result


def test_review_file_preserves_comment_types_for_any_provider(monkeypatch):
    responses = iter([
        '[{"line": 12, "confidence": 95, "type": "must", "comment": "Code Review: Bug."}, {"line": 18, "confidence": 84, "type": "test", "comment": "Needs tests."}]',
        '[{"line": 12, "confidence": 91, "type": "must", "comment": "Code Review: Duplicate bug."}, {"line": 22, "confidence": 86, "type": "nit", "comment": "Code Review: Error handling could be clearer."}]',
    ])

    monkeypatch.setattr(
        "sensei.reviewer.run_prompt",
        lambda prompt, timeout, config, schema, reasoning_effort=None: next(responses),
    )

    comments = review_file(
        file_path="src/foo.py",
        diff="try { risky() } catch (err) { handle(err) }",
        file_content="def foo():\n    pass",
        style_profile="direct",
        project_rules="rules",
        mr_context="mr",
        ai_config={"ai_cli": "codex"},
    )

    assert [comment["type"] for comment in comments] == ["must", "test", "nit"]
    assert comments[0]["line"] == 12
    assert comments[1]["line"] == 18
    assert comments[2]["line"] == 22


def test_build_similarity_prompt_includes_draft_and_existing_comments():
    prompt = build_similarity_prompt(
        draft_comment={"line": 10, "body": "Code Review: null check missing."},
        existing_comments=[{"author": "alice", "body": "Same issue, please fix."}],
        file_path="src/app.py",
    )
    assert "src/app.py" in prompt
    assert "null check missing" in prompt
    assert "alice" in prompt
    assert "Same issue, please fix." in prompt
    assert '"action"' in prompt


def test_judge_similarity_returns_post_new_when_no_existing_comments():
    result = judge_similarity(
        draft_comment={"line": 10, "body": "Bug here"},
        existing_comments=[],
        file_path="src/app.py",
        ai_config={},
    )
    assert result == {"action": "post_new", "reply_body": ""}


def test_judge_similarity_parses_skip_verdict(monkeypatch):
    monkeypatch.setattr(
        "sensei.reviewer.run_prompt",
        lambda prompt, timeout, config, schema, reasoning_effort=None: '{"action": "skip", "reply_body": ""}',
    )
    result = judge_similarity(
        draft_comment={"line": 10, "body": "Bug here"},
        existing_comments=[{"author": "alice", "body": "Bug here too"}],
        file_path="src/app.py",
        ai_config={},
    )
    assert result == {"action": "skip", "reply_body": ""}


def test_judge_similarity_parses_reply_verdict(monkeypatch):
    monkeypatch.setattr(
        "sensei.reviewer.run_prompt",
        lambda prompt, timeout, config, schema, reasoning_effort=None: (
            '{"action": "reply", "reply_body": "Also handles the empty-array case."}'
        ),
    )
    result = judge_similarity(
        draft_comment={"line": 10, "body": "Bug here"},
        existing_comments=[{"author": "alice", "body": "Similar bug"}],
        file_path="src/app.py",
        ai_config={},
    )
    assert result == {"action": "reply", "reply_body": "Also handles the empty-array case."}


def test_judge_similarity_fails_open_to_post_new_on_malformed_output(monkeypatch):
    monkeypatch.setattr(
        "sensei.reviewer.run_prompt",
        lambda prompt, timeout, config, schema, reasoning_effort=None: "not json",
    )
    result = judge_similarity(
        draft_comment={"line": 10, "body": "Bug here"},
        existing_comments=[{"author": "alice", "body": "Similar bug"}],
        file_path="src/app.py",
        ai_config={},
    )
    assert result == {"action": "post_new", "reply_body": ""}


def test_judge_similarity_fails_open_to_post_new_on_runtime_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("AI CLI unavailable")

    monkeypatch.setattr("sensei.reviewer.run_prompt", _raise)
    result = judge_similarity(
        draft_comment={"line": 10, "body": "Bug here"},
        existing_comments=[{"author": "alice", "body": "Similar bug"}],
        file_path="src/app.py",
        ai_config={},
    )
    assert result == {"action": "post_new", "reply_body": ""}
