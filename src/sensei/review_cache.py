import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sensei.config import CONFIG_DIR


def _reviews_dir() -> Path:
    path = CONFIG_DIR / "reviews"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _cache_path(project_path: str, mr_iid: int) -> Path:
    safe_name = project_path.replace("/", "_").replace("..", "")
    return _reviews_dir() / f"{safe_name}_{mr_iid}.json"


def save_review_artifact(
    mr_url: str,
    project_path: str,
    mr_iid: int,
    mr_data: dict,
    comments: list,
    test_summary: Optional[str],
) -> Path:
    """Persist a review so the exact dry-run result can be posted later."""
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mr_url": mr_url,
        "project_path": project_path,
        "mr_iid": mr_iid,
        "title": mr_data.get("title", ""),
        "diff_refs": {
            "base_sha": mr_data.get("base_sha", ""),
            "head_sha": mr_data.get("head_sha", ""),
            "start_sha": mr_data.get("start_sha", ""),
        },
        "comments": comments,
        "test_summary": test_summary,
    }
    path = _cache_path(project_path, mr_iid)
    path.write_text(json.dumps(payload, indent=2))
    path.chmod(0o600)
    return path


def load_review_artifact(
    mr_url: str,
    project_path: str,
    mr_iid: int,
    mr_data: dict,
) -> Optional[dict]:
    """Load a cached review only when it matches the current MR revision."""
    path = _cache_path(project_path, mr_iid)
    if not path.exists():
        return None

    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        return None
    if payload.get("mr_url") != mr_url:
        return None

    cached_refs = payload.get("diff_refs", {})
    current_refs = {
        "base_sha": mr_data.get("base_sha", ""),
        "head_sha": mr_data.get("head_sha", ""),
        "start_sha": mr_data.get("start_sha", ""),
    }
    if cached_refs != current_refs:
        return None

    return payload


def merge_review_artifacts(
    fresh_comments: list,
    fresh_test_summary: Optional[str],
    cached_review: Optional[dict],
) -> tuple:
    """Keep fresh results, but preserve cached comments that disappeared."""
    if not cached_review:
        return fresh_comments, fresh_test_summary, 0

    merged_comments = list(fresh_comments)
    fresh_keys = {
        (
            comment.get("type"),
            comment.get("file"),
            comment.get("line"),
            comment.get("body"),
        )
        for comment in fresh_comments
    }

    recovered = 0
    for comment in cached_review.get("comments", []):
        key = (
            comment.get("type"),
            comment.get("file"),
            comment.get("line"),
            comment.get("body"),
        )
        if key not in fresh_keys:
            merged_comments.append(comment)
            fresh_keys.add(key)
            recovered += 1

    merged_test_summary = fresh_test_summary or cached_review.get("test_summary")
    return merged_comments, merged_test_summary, recovered
