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


def test_get_other_reviewer_comments_excludes_current_user():
    client = GitLabClient.__new__(GitLabClient)

    own_note = {
        "author": {"username": "sensei"},
        "body": "My own earlier comment.",
        "position": {"new_path": "src/app.py", "new_line": 14},
    }
    other_note = {
        "author": {"username": "alice"},
        "body": "Fix the nil check.",
        "position": {"new_path": "src/app.py", "new_line": 14},
    }
    unpositioned_note = {
        "author": {"username": "bob"},
        "body": "General comment, no position.",
    }

    discussions = SimpleNamespace(
        list=lambda **kwargs: [
            SimpleNamespace(id="disc-1", attributes={"notes": [own_note, other_note]}),
            SimpleNamespace(id="disc-2", attributes={"notes": [unpositioned_note]}),
        ]
    )
    mr = SimpleNamespace(discussions=discussions)
    project = SimpleNamespace(mergerequests=SimpleNamespace(get=lambda mr_iid: mr))
    client.gl = SimpleNamespace(
        user=SimpleNamespace(username="sensei"),
        projects=SimpleNamespace(get=lambda project_path: project),
    )

    others = client.get_other_reviewer_comments("org/proj", 1)

    assert others == {
        ("src/app.py", 14): [
            {"discussion_id": "disc-1", "body": "Fix the nil check.", "author": "alice"}
        ]
    }


def test_reply_to_discussion_creates_note_on_existing_discussion():
    client = GitLabClient.__new__(GitLabClient)
    created = {}

    discussion = SimpleNamespace(
        notes=SimpleNamespace(create=lambda payload: created.update(payload))
    )
    mr = SimpleNamespace(discussions=SimpleNamespace(get=lambda discussion_id: discussion))
    project = SimpleNamespace(mergerequests=SimpleNamespace(get=lambda mr_iid: mr))
    client.gl = SimpleNamespace(projects=SimpleNamespace(get=lambda project_path: project))

    client.reply_to_discussion("org/proj", 1, "disc-1", "Also handles the empty-array case.")

    assert created == {"body": "Also handles the empty-array case."}


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


def test_no_newline_marker_does_not_shift_later_line_numbers():
    """`\\ No newline at end of file` is a marker, not a line of the file.

    Counting it as context advanced the new-side counter, so every added line
    after it was reported one too high — which places a review comment on the
    wrong line.
    """
    diff = (
        "@@ -1,2 +1,2 @@\n"
        "-old tail\n"
        "+new tail\n"
        "\\ No newline at end of file\n"
        "+appended\n"
    )

    assert extract_diff_lines(diff) == {1, 2}


def test_added_line_whose_content_starts_with_plus_is_not_skipped():
    """A `+++` line inside a hunk is an addition, not a file header."""
    diff = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,0 +1,2 @@\n"
        "+++ this is content, not a header\n"
        "+ordinary\n"
    )

    assert extract_diff_lines(diff) == {1, 2}


def test_file_headers_before_the_hunk_do_not_advance_the_counter():
    diff = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        "+added\n"
    )

    assert extract_diff_lines(diff) == {2}
