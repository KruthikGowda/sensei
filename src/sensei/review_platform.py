from typing import Dict
from urllib.parse import urlparse

from sensei.github_client import parse_pr_url
from sensei.gitlab_client import parse_mr_url, validate_mr_url_origin


def parse_review_url(url: str) -> Dict[str, object]:
    parsed = urlparse(url.rstrip("/"))
    path = parsed.path

    if "/-/merge_requests/" in path:
        project_path, review_id = parse_mr_url(url)
        return {
            "provider": "gitlab",
            "host": parsed.netloc,
            "project_path": project_path,
            "review_id": review_id,
            "label": f"MR !{review_id}",
        }

    if "/pull/" in path:
        project_path, review_id = parse_pr_url(url)
        return {
            "provider": "github",
            "host": parsed.netloc or "github.com",
            "project_path": project_path,
            "review_id": review_id,
            "label": f"PR #{review_id}",
        }

    raise ValueError(f"Invalid review URL: {url}")


def validate_review_target(target: Dict[str, object], config: dict) -> None:
    if target["provider"] == "gitlab":
        validate_mr_url_origin(
            f"https://{target['host']}/{target['project_path']}/-/merge_requests/{target['review_id']}",
            config["gitlab_url"],
        )
