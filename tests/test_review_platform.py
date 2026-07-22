from sensei.github_client import parse_pr_url
from sensei.github_client import GitHubClient
from sensei.review_platform import parse_review_url


def test_parse_pr_url_standard():
    project, pr_number = parse_pr_url(
        "https://github.com/SetuHQ/setu-components/pull/179"
    )
    assert project == "SetuHQ/setu-components"
    assert pr_number == 179


def test_parse_review_url_gitlab():
    target = parse_review_url(
        "https://gitlab.com/acme/widgets/backend/-/merge_requests/123"
    )
    assert target["provider"] == "gitlab"
    assert target["project_path"] == "acme/widgets/backend"
    assert target["review_id"] == 123
    assert target["label"] == "MR !123"


def test_parse_review_url_github():
    target = parse_review_url(
        "https://github.com/SetuHQ/setu-components/pull/179"
    )
    assert target["provider"] == "github"
    assert target["project_path"] == "SetuHQ/setu-components"
    assert target["review_id"] == 179
    assert target["label"] == "PR #179"


def test_github_get_file_content_returns_empty_for_binary_payload():
    client = GitHubClient()
    client._gh_json = lambda args: {"content": "rq4="}

    assert client.get_file_content("org/proj", "asset.bin", "main") == ""
