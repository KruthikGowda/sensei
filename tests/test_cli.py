from click.testing import CliRunner
from sensei.cli import main
from sensei.gitlab_client import build_body_signature, build_inline_signature


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Sensei" in result.output


def test_learn_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["learn", "--help"])
    assert result.exit_code == 0
    assert "--codex" in result.output
    assert "--claude" in result.output


def test_review_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["review", "--help"])
    assert result.exit_code == 0
    assert "--codex" in result.output
    assert "--claude" in result.output
    assert "GitHub pull request" in result.output


from unittest.mock import patch, MagicMock


def test_review_batch_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["review-batch", "--help"])
    assert result.exit_code == 0
    assert "concurrency" in result.output
    assert "dry-run" in result.output
    assert "--file" in result.output
    assert "GitHub PRs" in result.output


def test_set_ai_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["set-ai", "--help"])
    assert result.exit_code == 0
    assert "--ai-cli" in result.output


def test_use_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["use", "--help"])
    assert result.exit_code == 0


def test_doctor_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--help"])
    assert result.exit_code == 0


def test_review_batch_from_file(tmp_path):
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        "# MRs to review\n"
        "https://gitlab.com/org/proj/-/merge_requests/1\n"
        "\n"
        "https://gitlab.com/org/proj/-/merge_requests/2\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["review-batch", "--file", str(url_file)])
    assert "Invalid MR URL" not in result.output


def test_review_batch_no_urls():
    runner = CliRunner()
    result = runner.invoke(main, ["review-batch"])
    assert result.exit_code != 0


def test_review_batch_rejects_invalid_urls():
    runner = CliRunner()
    fake_config = {"gitlab_url": "https://gitlab.com", "gitlab_pat": "test-token"}
    with patch("sensei.config.load_config", return_value=fake_config):
        result = runner.invoke(main, ["review-batch", "not-a-url"])
    assert result.exit_code != 0
    assert "Invalid" in result.output


def test_review_single_mr_returns_result_dict():
    mock_client = MagicMock()
    mock_client.get_mr_diff.return_value = {
        "title": "Fix bug", "description": "Fixes issue",
        "source_branch": "fix-bug", "target_branch": "main",
        "author": "testuser",
        "web_url": "https://gitlab.com/org/proj/-/merge_requests/1",
        "base_sha": "aaa", "head_sha": "bbb", "start_sha": "ccc",
        "files": [],
    }
    config = {"gitlab_url": "https://gitlab.com", "gitlab_pat": "fake", "batch_size": 30}

    from sensei.cli import _review_single_mr
    result = _review_single_mr(
        client=mock_client, config=config,
        project_path="org/proj", mr_iid=1,
        mr_url="https://gitlab.com/org/proj/-/merge_requests/1",
    )
    assert result["mr_url"] == "https://gitlab.com/org/proj/-/merge_requests/1"
    assert result["mr_iid"] == 1
    assert result["error"] is None
    assert isinstance(result["comments"], list)


def test_review_single_mr_captures_error():
    mock_client = MagicMock()
    mock_client.get_mr_diff.side_effect = Exception("API down")
    config = {"gitlab_url": "https://gitlab.com", "gitlab_pat": "fake", "batch_size": 30}

    from sensei.cli import _review_single_mr
    result = _review_single_mr(
        client=mock_client, config=config,
        project_path="org/proj", mr_iid=1,
        mr_url="https://gitlab.com/org/proj/-/merge_requests/1",
    )
    assert result["error"] is not None
    assert "API down" in result["error"]


def test_post_review_results_posts_musts_inline():
    mock_client = MagicMock()
    comments = [{"file": "src/app.tsx", "line": 10, "confidence": 95, "type": "must", "body": "Bug here"}]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}

    from sensei.cli import _post_review_results
    inline_posted, nits_posted, test_posted, skipped = _post_review_results(
        client=mock_client, project_path="org/proj", mr_iid=1,
        mr_data=mr_data, comments=comments, test_summary=None,
        diff_lines_map=diff_lines_map, existing=set(),
    )
    assert inline_posted >= 1
    mock_client.post_inline_comment.assert_called_once()


def test_post_review_results_does_not_skip_due_to_body_prefix_collision():
    mock_client = MagicMock()
    comment_body = (
        "Code Review: " + ("A" * 120) + " real difference at the end"
    )
    comments = [{
        "file": "src/app.tsx",
        "line": 10,
        "confidence": 95,
        "type": "must",
        "body": comment_body,
    }]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}

    existing = {
        build_body_signature(
            "Code Review: " + ("A" * 120) + " different existing comment"
        )
    }

    from sensei.cli import _post_review_results
    inline_posted, nits_posted, test_posted, skipped = _post_review_results(
        client=mock_client, project_path="org/proj", mr_iid=1,
        mr_data=mr_data, comments=comments, test_summary=None,
        diff_lines_map=diff_lines_map, existing=existing,
    )

    assert (inline_posted, nits_posted, test_posted, skipped) == (1, 0, 0, 0)
    mock_client.post_inline_comment.assert_called_once()


def test_post_review_results_skips_existing_general_summary_comments():
    mock_client = MagicMock()
    comments = [{
        "file": "src/app.tsx",
        "line": 10,
        "confidence": 82,
        "type": "nit",
        "body": "Rename this helper.",
    }]
    test_summary = "## Test Coverage Gaps\n\nMissing regression coverage."

    from sensei.formatter import format_nits_summary
    from sensei.cli import _post_review_results

    nits_body = format_nits_summary(comments)
    existing = {
        build_body_signature(nits_body),
        build_body_signature(test_summary),
        build_inline_signature("src/app.tsx", 99),
    }

    inline_posted, nits_posted, test_posted, skipped = _post_review_results(
        client=mock_client, project_path="org/proj", mr_iid=1,
        mr_data={"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []},
        comments=comments, test_summary=test_summary,
        diff_lines_map={}, existing=existing,
    )

    assert (inline_posted, nits_posted, test_posted, skipped) == (0, 0, 0, 2)
    mock_client.post_mr_comment.assert_not_called()


def test_post_review_results_skips_when_other_reviewer_already_covered_it():
    mock_client = MagicMock()
    comments = [{"file": "src/app.tsx", "line": 10, "confidence": 95, "type": "must", "body": "Bug here"}]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}
    others = {("src/app.tsx", 10): [{"discussion_id": "d1", "body": "already flagged this", "author": "alice"}]}

    from sensei.cli import _post_review_results
    with patch("sensei.reviewer.judge_similarity", return_value={"action": "skip", "reply_body": ""}):
        inline_posted, nits_posted, test_posted, skipped = _post_review_results(
            client=mock_client, project_path="org/proj", mr_iid=1,
            mr_data=mr_data, comments=comments, test_summary=None,
            diff_lines_map=diff_lines_map, existing=set(), others=others, ai_config={},
        )

    assert (inline_posted, nits_posted, test_posted, skipped) == (0, 0, 0, 1)
    mock_client.post_inline_comment.assert_not_called()
    mock_client.reply_to_discussion.assert_not_called()


def test_post_review_results_replies_in_gitlab_thread_when_similar_with_delta():
    mock_client = MagicMock()
    comments = [{"file": "src/app.tsx", "line": 10, "confidence": 95, "type": "must", "body": "Bug here"}]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}
    others = {("src/app.tsx", 10): [{"discussion_id": "d1", "body": "already flagged this", "author": "alice"}]}

    from sensei.cli import _post_review_results
    with patch(
        "sensei.reviewer.judge_similarity",
        return_value={"action": "reply", "reply_body": "Also: this crashes on empty input."},
    ):
        inline_posted, nits_posted, test_posted, skipped = _post_review_results(
            client=mock_client, project_path="org/proj", mr_iid=1,
            mr_data=mr_data, comments=comments, test_summary=None,
            diff_lines_map=diff_lines_map, existing=set(), others=others, ai_config={},
        )

    assert (inline_posted, nits_posted, test_posted, skipped) == (1, 0, 0, 0)
    mock_client.reply_to_discussion.assert_called_once_with(
        "org/proj", 1, "d1", "Also: this crashes on empty input."
    )
    mock_client.post_inline_comment.assert_not_called()


def test_post_review_results_replies_in_github_thread_via_comment_id():
    mock_client = MagicMock()
    comments = [{"file": "src/app.tsx", "line": 10, "confidence": 95, "type": "must", "body": "Bug here"}]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}
    others = {("src/app.tsx", 10): [{"comment_id": 42, "body": "already flagged this", "author": "alice"}]}

    from sensei.cli import _post_review_results
    with patch(
        "sensei.reviewer.judge_similarity",
        return_value={"action": "reply", "reply_body": "Also: this crashes on empty input."},
    ):
        _post_review_results(
            client=mock_client, project_path="org/repo", mr_iid=7,
            mr_data=mr_data, comments=comments, test_summary=None,
            diff_lines_map=diff_lines_map, existing=set(), others=others, ai_config={},
        )

    mock_client.reply_to_comment.assert_called_once_with(
        "org/repo", 7, 42, "Also: this crashes on empty input."
    )


def test_post_review_results_posts_new_when_unrelated_to_existing_comment():
    mock_client = MagicMock()
    comments = [{"file": "src/app.tsx", "line": 10, "confidence": 95, "type": "must", "body": "Bug here"}]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}
    others = {("src/app.tsx", 10): [{"discussion_id": "d1", "body": "unrelated nit", "author": "alice"}]}

    from sensei.cli import _post_review_results
    with patch("sensei.reviewer.judge_similarity", return_value={"action": "post_new", "reply_body": ""}):
        inline_posted, nits_posted, test_posted, skipped = _post_review_results(
            client=mock_client, project_path="org/proj", mr_iid=1,
            mr_data=mr_data, comments=comments, test_summary=None,
            diff_lines_map=diff_lines_map, existing=set(), others=others, ai_config={},
        )

    assert (inline_posted, nits_posted, test_posted, skipped) == (1, 0, 0, 0)
    mock_client.post_inline_comment.assert_called_once()
    mock_client.reply_to_discussion.assert_not_called()


def test_post_review_results_skips_similarity_check_when_no_other_comments_there():
    mock_client = MagicMock()
    comments = [{"file": "src/app.tsx", "line": 10, "confidence": 95, "type": "must", "body": "Bug here"}]
    diff_lines_map = {"src/app.tsx": {10}}
    mr_data = {"base_sha": "a", "head_sha": "b", "start_sha": "c", "files": []}

    from sensei.cli import _post_review_results
    with patch("sensei.reviewer.judge_similarity") as mock_judge:
        _post_review_results(
            client=mock_client, project_path="org/proj", mr_iid=1,
            mr_data=mr_data, comments=comments, test_summary=None,
            diff_lines_map=diff_lines_map, existing=set(), others={}, ai_config={},
        )

    mock_judge.assert_not_called()
    mock_client.post_inline_comment.assert_called_once()


def test_set_ai_updates_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)

    from sensei.config import init_config

    init_config(gitlab_pat="glpat-test123", username="testuser")

    runner = CliRunner()
    result = runner.invoke(main, ["set-ai", "--ai-cli", "codex", "--no-fallback-ai"])

    assert result.exit_code == 0
    assert "ai=codex" in result.output

    from sensei.config import load_config

    config = load_config()
    assert config["ai_cli"] == "codex"
    assert config["fallback_ai_cli"] is False


def test_doctor_reports_backend_status(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)

    from sensei.config import init_config

    init_config(gitlab_pat="glpat-test123", username="testuser")
    monkeypatch.setattr(
        "sensei.cli.resolve_ai_cli_candidates",
        lambda config=None: ["codex", "claude"],
    )
    monkeypatch.setattr(
        "sensei.cli.get_ai_cli_status",
        lambda provider: {
            "label": provider.title(),
            "installed": True,
            "authenticated": provider == "codex",
            "detail": "ok",
            "path": f"/tmp/{provider}",
        },
    )
    monkeypatch.setattr(
        "sensei.cli._get_github_cli_status",
        lambda: {
            "installed": True,
            "authenticated": True,
            "detail": "Authenticated",
            "path": "/tmp/gh",
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "Resolved order: codex, claude" in result.output
    assert "Codex: installed=yes, authenticated=yes" in result.output
    assert "GitHub CLI: installed=yes, authenticated=yes" in result.output


def test_use_command_updates_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)

    from sensei.config import init_config, load_config

    init_config(gitlab_pat="glpat-test123", username="testuser")

    runner = CliRunner()
    result = runner.invoke(main, ["use", "codex"])

    assert result.exit_code == 0
    assert "Now using codex" in result.output

    config = load_config()
    assert config["ai_cli"] == "codex"


def test_apply_ai_overrides_changes_runtime_config():
    from sensei.cli import _apply_ai_overrides

    config = {"ai_cli": "claude", "model": "", "_resolved_ai_cli_candidates": ["claude"]}
    runtime_config = _apply_ai_overrides(config, "codex", "gpt-5.2")

    assert runtime_config["ai_cli"] == "codex"
    assert runtime_config["model"] == "gpt-5.2"
    assert "_resolved_ai_cli_candidates" not in runtime_config


def test_build_mr_title_comments_flags_missing_ticket():
    from sensei.cli import build_mr_title_comments

    comments = build_mr_title_comments({"title": "fix: harden shared axios base URL handling"})

    assert len(comments) == 1
    assert comments[0]["type"] == "nit"
    assert "type(JIRA-ID): Title" in comments[0]["body"]
    assert "fix: harden shared axios base URL handling" in comments[0]["body"]


def test_build_mr_title_comments_allows_standard_title():
    from sensei.cli import build_mr_title_comments

    comments = build_mr_title_comments({"title": "fix(BRIDGE-2254): Harden axios base URL handling"})

    assert comments == []


def test_build_mr_description_comments_flags_placeholder_description():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n<!-- Provide a brief description -->\n"
            "## Preview URL\n<!-- Provide a preview URL -->\n"
            "## Screenshots\n<!-- Include screenshots or GIFs that demonstrate the changes -->\n"
        ),
        "files": [{"new_path": "src/components/InvoiceTable.tsx"}],
    })

    assert len(comments) == 4
    bodies = "\n".join(comment["body"] for comment in comments)
    assert "description still looks incomplete" in bodies
    assert "Jira ticket id" in bodies
    assert "preview URL" in bodies
    assert "screenshots or screen recordings" in bodies


def test_build_mr_description_comments_flags_missing_jira_ticket():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "Adds the incoming invoice table and wires the status filters for the reviewer flow.\n"
            "## Preview URL\n"
            "Preview: https://preview.example.com/invoices\n"
            "## Screenshots\n"
            "![invoice table](https://preview.example.com/invoices.png)"
        ),
    })

    assert len(comments) == 1
    assert "Jira ticket id" in comments[0]["body"]


def test_build_mr_description_comments_flags_missing_preview_url():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "BRIDGE-2254 adds the incoming invoice table and wires status filters for reviewers.\n"
            "## Screenshots\n"
            "![invoice table](https://preview.example.com/invoices.png)"
        ),
        "files": [{"new_path": "src/components/InvoiceTable.tsx"}],
    })

    assert len(comments) == 1
    assert "preview URL" in comments[0]["body"]


def test_build_mr_description_comments_flags_missing_screenshot():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "BRIDGE-2254 adds the incoming invoice table and wires status filters for reviewers.\n"
            "## Preview URL\n"
            "Preview: https://preview.example.com/invoices"
        ),
        "files": [{"new_path": "src/components/InvoiceTable.tsx"}],
    })

    assert len(comments) == 1
    assert "screenshots or screen recordings" in comments[0]["body"]


def test_build_mr_description_comments_allows_complete_description():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "BRIDGE-2254 adds the incoming invoice table and wires status filters for reviewers.\n"
            "## Preview URL\n"
            "Preview: https://preview.example.com/invoices\n"
            "## Screenshots\n"
            "![invoice table](/uploads/invoices.png)"
        ),
        "files": [{"new_path": "src/components/InvoiceTable.tsx"}],
    })

    assert comments == []


def test_build_mr_description_comments_skips_preview_checks_for_non_ui_diff():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "BRIDGE-2420 adds a manual security remediation trigger job to the pipeline.\n"
            "## Preview URL\n"
            "<!--Provide a preview URL-->"
        ),
        "files": [{"new_path": ".gitlab-ci.yml"}],
    })

    assert comments == []


def test_build_mr_description_comments_flags_ui_files_outside_component_dirs():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "BRIDGE-2254 restyles the invoice summary card for narrow viewports.\n"
        ),
        "files": [{"new_path": "app/invoice-summary.scss"}],
    })

    bodies = [comment["body"] for comment in comments]
    assert any("preview URL" in body for body in bodies)
    assert any("screenshots or screen recordings" in body for body in bodies)


def test_build_mr_description_comments_stays_quiet_without_file_list():
    from sensei.cli import build_mr_description_comments

    comments = build_mr_description_comments({
        "description": (
            "## Description\n"
            "BRIDGE-2254 adds the incoming invoice table and wires status filters for reviewers.\n"
        ),
    })

    assert comments == []


def test_build_metadata_comments_combines_title_and_description_rules():
    from sensei.cli import build_metadata_comments

    comments = build_metadata_comments({
        "source_branch": "fix/auth-retry",
        "title": "Fix auth retry",
        "description": (
            "## Description\n"
            "BRIDGE-2254 adds the incoming invoice table and wires status filters for reviewers.\n"
            "## Preview URL\n"
            "Preview: https://preview.example.com/invoices\n"
            "## Screenshots\n"
            "![invoice table](/uploads/invoices.png)"
        ),
    })

    assert len(comments) == 1
    assert "type(JIRA-ID): Title" in comments[0]["body"]


def test_drop_deprecated_metadata_comments_removes_codex_branch_rule():
    from sensei.cli import _drop_deprecated_metadata_comments

    comments = [
        {
            "file": "Merge request metadata",
            "line": 0,
            "type": "nit",
            "body": "Code Review: Source branch `feat/foo` does not use the `codex/` prefix.",
        },
        {
            "file": "Merge request metadata",
            "line": 0,
            "type": "nit",
            "body": "Code Review: MR title does not follow the expected format.",
        },
    ]

    filtered = _drop_deprecated_metadata_comments(comments)

    assert filtered == [comments[1]]


def test_review_merges_cached_artifact_with_fresh_run(monkeypatch):
    mock_client = MagicMock()
    mock_client.get_mr_diff.return_value = {
        "title": "Fix bug",
        "description": "Fixes issue",
        "source_branch": "fix-bug",
        "target_branch": "main",
        "author": "testuser",
        "web_url": "https://gitlab.com/org/proj/-/merge_requests/1",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "start_sha": "ccc",
        "files": [],
    }
    monkeypatch.setattr("sensei.gitlab_client.GitLabClient", lambda *args, **kwargs: mock_client)
    monkeypatch.setattr("sensei.config.load_config", lambda: {"gitlab_url": "https://gitlab.com", "gitlab_pat": "fake", "batch_size": 30})
    monkeypatch.setattr("sensei.reviewer.load_style_profile", lambda: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules", lambda project_path: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules_from_repo", lambda client, project_path, ref: "")
    monkeypatch.setattr("sensei.review_cache.load_review_artifact", lambda *args, **kwargs: {
        "created_at": "2026-06-08T10:00:00+00:00",
        "comments": [{"file": "src/old.ts", "line": 10, "type": "must", "body": "Cached comment"}],
        "test_summary": "Cached tests",
    })
    monkeypatch.setattr("sensei.reviewer.review_mr_files", lambda *args, **kwargs: [{"file": "src/new.ts", "line": 20, "type": "must", "body": "Fresh comment"}])
    monkeypatch.setattr("sensei.review_cache.save_review_artifact", lambda **kwargs: "/tmp/review.json")

    runner = CliRunner()
    result = runner.invoke(main, ["review", "https://gitlab.com/org/proj/-/merge_requests/1"], input="discard\n")

    assert result.exit_code == 0
    assert "Merging fresh review with cached snapshot from 2026-06-08T10:00:00+00:00" in result.output
    assert "Recovered 1 cached comment(s) missing from the fresh run." in result.output
    assert "Cached comment" in result.output
    assert "Fresh comment" in result.output


def test_review_dry_run_saves_review_artifact(monkeypatch):
    mock_client = MagicMock()
    mock_client.get_mr_diff.return_value = {
        "title": "Fix bug",
        "description": "Fixes issue",
        "source_branch": "fix-bug",
        "target_branch": "main",
        "author": "testuser",
        "web_url": "https://gitlab.com/org/proj/-/merge_requests/1",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "start_sha": "ccc",
        "files": [],
    }
    monkeypatch.setattr("sensei.gitlab_client.GitLabClient", lambda *args, **kwargs: mock_client)
    monkeypatch.setattr("sensei.config.load_config", lambda: {"gitlab_url": "https://gitlab.com", "gitlab_pat": "fake", "batch_size": 30})
    monkeypatch.setattr("sensei.reviewer.load_style_profile", lambda: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules", lambda project_path: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules_from_repo", lambda client, project_path, ref: "")
    monkeypatch.setattr("sensei.reviewer.review_mr_files", lambda *args, **kwargs: [{"file": "src/app.ts", "line": 10, "type": "must", "body": "Bug"}])
    saved = {}

    def fake_save_review_artifact(**kwargs):
        saved.update(kwargs)
        return "/tmp/review.json"

    monkeypatch.setattr("sensei.review_cache.load_review_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("sensei.review_cache.save_review_artifact", fake_save_review_artifact)

    runner = CliRunner()
    result = runner.invoke(main, ["review", "https://gitlab.com/org/proj/-/merge_requests/1", "--dry-run"])

    assert result.exit_code == 0
    assert saved["mr_url"] == "https://gitlab.com/org/proj/-/merge_requests/1"
    assert saved["project_path"] == "org/proj"


def test_review_fresh_recovers_cached_comments(monkeypatch):
    mock_client = MagicMock()
    mock_client.get_mr_diff.return_value = {
        "title": "Fix bug",
        "description": "Fixes issue",
        "source_branch": "fix-bug",
        "target_branch": "main",
        "author": "testuser",
        "web_url": "https://gitlab.com/org/proj/-/merge_requests/1",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "start_sha": "ccc",
        "files": [],
    }
    monkeypatch.setattr("sensei.gitlab_client.GitLabClient", lambda *args, **kwargs: mock_client)
    monkeypatch.setattr("sensei.config.load_config", lambda: {"gitlab_url": "https://gitlab.com", "gitlab_pat": "fake", "batch_size": 30})
    monkeypatch.setattr("sensei.reviewer.load_style_profile", lambda: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules", lambda project_path: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules_from_repo", lambda client, project_path, ref: "")
    monkeypatch.setattr("sensei.review_cache.load_review_artifact", lambda *args, **kwargs: {
        "created_at": "2026-06-08T10:00:00+00:00",
        "comments": [{"file": "src/old.ts", "line": 10, "type": "must", "body": "Cached comment"}],
        "test_summary": "Cached tests",
    })
    monkeypatch.setattr("sensei.reviewer.review_mr_files", lambda *args, **kwargs: [{"file": "src/new.ts", "line": 20, "type": "must", "body": "Fresh comment"}])
    monkeypatch.setattr("sensei.review_cache.save_review_artifact", lambda **kwargs: "/tmp/review.json")

    runner = CliRunner()
    result = runner.invoke(main, ["review", "https://gitlab.com/org/proj/-/merge_requests/1", "--fresh"], input="discard\n")

    assert result.exit_code == 0
    assert "Fresh comment" in result.output
    assert "Cached comment" not in result.output
    assert "Recovered 1 cached comment(s) missing from the fresh run." not in result.output


def test_review_supports_github_pr_urls(monkeypatch):
    mock_client = MagicMock()
    mock_client.get_mr_diff.return_value = {
        "title": "Add component",
        "description": "Adds a new component",
        "source_branch": "feature/pr",
        "target_branch": "main",
        "author": "octocat",
        "web_url": "https://github.com/org/proj/pull/7",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "start_sha": "ccc",
        "files": [],
    }
    monkeypatch.setattr("sensei.github_client.GitHubClient", lambda *args, **kwargs: mock_client)
    monkeypatch.setattr("sensei.config.load_config", lambda: {"batch_size": 30})
    monkeypatch.setattr("sensei.reviewer.load_style_profile", lambda: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules", lambda project_path: "")
    monkeypatch.setattr("sensei.reviewer.load_project_rules_from_repo", lambda client, project_path, ref: "")
    monkeypatch.setattr("sensei.reviewer.review_mr_files", lambda *args, **kwargs: [])
    monkeypatch.setattr("sensei.review_cache.load_review_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("sensei.review_cache.save_review_artifact", lambda **kwargs: "/tmp/review.json")

    runner = CliRunner()
    result = runner.invoke(main, ["review", "https://github.com/org/proj/pull/7"], input="discard\n")

    assert result.exit_code == 0
    assert "Fetching PR #7 from org/proj" in result.output


def test_post_supports_github_pr_urls(monkeypatch, tmp_path):
    review_file = tmp_path / "review.md"
    review_file.write_text("Code Review: Looks good.")

    mock_client = MagicMock()
    monkeypatch.setattr("sensei.github_client.GitHubClient", lambda *args, **kwargs: mock_client)
    monkeypatch.setattr("sensei.config.load_config", lambda: {})

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["post", "https://github.com/org/proj/pull/7", str(review_file)],
    )

    assert result.exit_code == 0
    mock_client.post_mr_comment.assert_called_once_with(
        "org/proj", 7, "Code Review: Looks good."
    )
