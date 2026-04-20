from click.testing import CliRunner
from sensei.cli import main


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


from unittest.mock import patch, MagicMock


def test_review_batch_command_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["review-batch", "--help"])
    assert result.exit_code == 0
    assert "concurrency" in result.output
    assert "dry-run" in result.output
    assert "--file" in result.output


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

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "Resolved order: codex, claude" in result.output
    assert "Codex: installed=yes, authenticated=yes" in result.output


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
