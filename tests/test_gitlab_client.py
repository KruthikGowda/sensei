from types import SimpleNamespace

import gitlab

from sensei.gitlab_client import (
    GitLabClient,
    build_body_signature,
    build_inline_signature,
    extract_diff_lines,
    parse_mr_url,
)


def test_parse_mr_url_standard():
    project, mr_iid = parse_mr_url(
        "https://gitlab.com/acme/widgets/backend/-/merge_requests/123"
    )
    assert project == "acme/widgets/backend"
    assert mr_iid == 123


def test_parse_mr_url_with_trailing_slash():
    project, mr_iid = parse_mr_url(
        "https://gitlab.com/acme/widgets/backend/-/merge_requests/123/"
    )
    assert project == "acme/widgets/backend"
    assert mr_iid == 123


def test_parse_mr_url_nested_group():
    project, mr_iid = parse_mr_url(
        "https://gitlab.com/acme/widgets/docs-site/-/merge_requests/45"
    )
    assert project == "acme/widgets/docs-site"
    assert mr_iid == 45


def test_extract_diff_lines():
    diff = """@@ -10,6 +10,8 @@ some context
 unchanged line
+added line 11
+added line 12
 unchanged line
-removed line
 unchanged line"""
    lines = extract_diff_lines(diff)
    assert 11 in lines
    assert 12 in lines
    assert 10 not in lines  # context line, not added


def test_get_existing_comments_uses_typed_signatures():
    client = GitLabClient.__new__(GitLabClient)

    inline_note = {
        "author": {"username": "sensei"},
        "body": "Code Review: Fix the nil check.",
        "position": {"new_path": "src/app.py", "new_line": 14},
    }
    general_note = SimpleNamespace(
        author={"username": "sensei"},
        body="## Nits\n\n### `src/app.py`\n**L20:** Rename this.",
    )

    discussions = SimpleNamespace(
        list=lambda **kwargs: [SimpleNamespace(attributes={"notes": [inline_note]})]
    )
    notes = SimpleNamespace(list=lambda **kwargs: [general_note])
    mr = SimpleNamespace(discussions=discussions, notes=notes)
    project = SimpleNamespace(mergerequests=SimpleNamespace(get=lambda mr_iid: mr))
    client.gl = SimpleNamespace(
        user=SimpleNamespace(username="sensei"),
        projects=SimpleNamespace(get=lambda project_path: project),
    )

    signatures = client.get_existing_comments("org/proj", 1)

    assert build_inline_signature("src/app.py", 14) in signatures
    assert build_body_signature("Code Review: Fix the nil check.") in signatures
    assert build_body_signature(general_note.body) in signatures


def test_get_file_content_returns_empty_for_binary_file():
    client = GitLabClient.__new__(GitLabClient)
    binary_file = SimpleNamespace(
        decode=lambda: b"\x89PNG\r\n\x1a\n"
    )
    project = SimpleNamespace(
        files=SimpleNamespace(get=lambda **kwargs: binary_file)
    )
    client.gl = SimpleNamespace(
        projects=SimpleNamespace(get=lambda project_path: project)
    )

    assert client.get_file_content("org/proj", "image.png", "main") == ""


def test_get_file_content_returns_empty_when_gitlab_file_missing():
    client = GitLabClient.__new__(GitLabClient)

    def raise_missing(**kwargs):
        raise gitlab.exceptions.GitlabGetError(error_message="missing", response_code=404)

    project = SimpleNamespace(
        files=SimpleNamespace(get=raise_missing)
    )
    client.gl = SimpleNamespace(
        projects=SimpleNamespace(get=lambda project_path: project)
    )

    assert client.get_file_content("org/proj", "missing.txt", "main") == ""
