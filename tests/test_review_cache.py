from sensei.review_cache import (
    load_review_artifact,
    merge_review_artifacts,
    save_review_artifact,
)


def test_save_and_load_review_artifact(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("sensei.review_cache.CONFIG_DIR", config_dir)

    mr_data = {
        "title": "Fix bug",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "start_sha": "ccc",
    }
    comments = [{"file": "src/app.ts", "line": 10, "type": "must", "body": "Bug"}]
    path = save_review_artifact(
        mr_url="https://gitlab.com/org/proj/-/merge_requests/1",
        project_path="org/proj",
        mr_iid=1,
        mr_data=mr_data,
        comments=comments,
        test_summary="Test summary",
    )

    assert path.exists()
    payload = load_review_artifact(
        mr_url="https://gitlab.com/org/proj/-/merge_requests/1",
        project_path="org/proj",
        mr_iid=1,
        mr_data=mr_data,
    )

    assert payload is not None
    assert payload["comments"] == comments
    assert payload["test_summary"] == "Test summary"


def test_load_review_artifact_rejects_sha_mismatch(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("sensei.review_cache.CONFIG_DIR", config_dir)

    save_review_artifact(
        mr_url="https://gitlab.com/org/proj/-/merge_requests/1",
        project_path="org/proj",
        mr_iid=1,
        mr_data={"base_sha": "aaa", "head_sha": "bbb", "start_sha": "ccc"},
        comments=[],
        test_summary=None,
    )

    payload = load_review_artifact(
        mr_url="https://gitlab.com/org/proj/-/merge_requests/1",
        project_path="org/proj",
        mr_iid=1,
        mr_data={"base_sha": "aaa", "head_sha": "changed", "start_sha": "ccc"},
    )

    assert payload is None


def test_merge_review_artifacts_recovers_missing_cached_comments():
    fresh_comments = [
        {"file": "src/new.ts", "line": 20, "type": "must", "body": "Fresh comment"}
    ]
    cached_review = {
        "comments": [
            {"file": "src/old.ts", "line": 10, "type": "must", "body": "Cached comment"},
            {"file": "src/new.ts", "line": 20, "type": "must", "body": "Fresh comment"},
        ],
        "test_summary": "Cached tests",
    }

    comments, test_summary, recovered = merge_review_artifacts(
        fresh_comments,
        None,
        cached_review,
    )

    assert recovered == 1
    assert len(comments) == 2
    assert test_summary == "Cached tests"
