from sensei.github_client import GitHubClient


def test_get_other_reviewer_comments_excludes_current_user():
    client = GitHubClient()
    client.get_current_username = lambda: "sensei-bot"
    client._paginate = lambda route: [
        {"id": 1, "path": "src/app.py", "line": 10, "body": "Fix this null check.",
         "user": {"login": "alice"}},
        {"id": 2, "path": "src/app.py", "line": 10, "body": "Already reviewed by me.",
         "user": {"login": "sensei-bot"}},
        {"id": 3, "path": "src/other.py", "line": 5, "body": "Different file.",
         "user": {"login": "bob"}},
    ]

    others = client.get_other_reviewer_comments("org/proj", 7)

    assert list(others[("src/app.py", 10)]) == [
        {"comment_id": 1, "body": "Fix this null check.", "author": "alice"}
    ]
    assert others[("src/other.py", 5)] == [
        {"comment_id": 3, "body": "Different file.", "author": "bob"}
    ]


def test_get_other_reviewer_comments_ignores_comments_without_position():
    client = GitHubClient()
    client.get_current_username = lambda: "sensei-bot"
    client._paginate = lambda route: [
        {"id": 1, "body": "General comment, no path/line.", "user": {"login": "alice"}},
    ]

    others = client.get_other_reviewer_comments("org/proj", 7)

    assert others == {}


def test_reply_to_comment_sends_in_reply_to():
    client = GitHubClient()
    captured = {}

    def fake_gh(args):
        captured["args"] = args
        return ""

    client._gh = fake_gh
    client.reply_to_comment("org/proj", 7, 42, "Also handles the empty-array case.")

    args = captured["args"]
    assert "repos/org/proj/pulls/7/comments" in args
    assert "in_reply_to=42" in args
    assert "body=Also handles the empty-array case." in args
