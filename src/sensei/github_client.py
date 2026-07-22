import base64
import json
import re
import subprocess
from urllib.parse import quote, urlparse

from sensei.gitlab_client import (
    build_body_signature,
    build_inline_signature,
)


def parse_pr_url(url: str) -> tuple:
    """Extract repo path and PR number from a GitHub PR URL."""
    url = url.rstrip("/")
    parsed = urlparse(url)
    match = re.match(r"^/([^/]+/[^/]+)/pull/(\d+)$", parsed.path)
    if not match:
        raise ValueError(f"Invalid PR URL: {url}")
    return match.group(1), int(match.group(2))


class GitHubClient:
    def __init__(self, hostname: str = "github.com"):
        self.hostname = hostname
        self._current_user = None

    def _gh(self, args: list) -> str:
        cmd = ["gh"]
        if self.hostname != "github.com":
            cmd.extend(["--hostname", self.hostname])
        cmd.extend(args)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "GitHub CLI (`gh`) is not installed. Install it or use a GitLab MR URL."
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(detail or "GitHub CLI request failed.")
        return completed.stdout

    def _gh_json(self, args: list):
        output = self._gh(args)
        return json.loads(output)

    def _paginate(self, route: str) -> list:
        page = 1
        results = []
        while True:
            separator = "&" if "?" in route else "?"
            batch = self._gh_json(["api", f"{route}{separator}per_page=100&page={page}"])
            if not isinstance(batch, list):
                raise RuntimeError("Expected paginated GitHub API response to be a list.")
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results

    def get_current_username(self) -> str:
        if self._current_user is None:
            user = self._gh_json(["api", "user"])
            self._current_user = user["login"]
        return self._current_user

    def get_mr_diff(self, project_path: str, mr_iid: int) -> dict:
        """Fetch PR metadata and per-file diffs in the same shape as GitLabClient."""
        pr = self._gh_json(["api", f"repos/{project_path}/pulls/{mr_iid}"])
        files = self._paginate(f"repos/{project_path}/pulls/{mr_iid}/files")

        normalized_files = []
        for item in files:
            status = item.get("status", "")
            normalized_files.append({
                "old_path": item.get("previous_filename", item["filename"]),
                "new_path": item["filename"],
                "diff": item.get("patch", ""),
                "new_file": status == "added",
                "deleted_file": status == "removed",
                "renamed_file": status == "renamed",
            })

        return {
            "title": pr["title"],
            "description": pr.get("body") or "",
            "source_branch": pr["head"]["ref"],
            "target_branch": pr["base"]["ref"],
            "author": pr["user"]["login"],
            "web_url": pr["html_url"],
            "base_sha": pr["base"]["sha"],
            "head_sha": pr["head"]["sha"],
            "start_sha": pr["base"]["sha"],
            "files": normalized_files,
        }

    def get_file_content(self, project_path: str, file_path: str, ref: str) -> str:
        route = f"repos/{project_path}/contents/{quote(file_path, safe='')}?ref={quote(ref, safe='')}"
        try:
            payload = self._gh_json(["api", route])
        except RuntimeError:
            return ""

        encoded = (payload.get("content") or "").replace("\n", "")
        if not encoded:
            return ""
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def get_existing_comments(self, project_path: str, mr_iid: int) -> set:
        current_user = self.get_current_username()
        signatures = set()

        review_comments = self._paginate(f"repos/{project_path}/pulls/{mr_iid}/comments")
        for note in review_comments:
            if note.get("user", {}).get("login") != current_user:
                continue
            body = note.get("body", "")
            if note.get("path") and note.get("line"):
                signatures.add(build_inline_signature(note["path"], note["line"]))
            signatures.add(build_body_signature(body))

        issue_comments = self._paginate(f"repos/{project_path}/issues/{mr_iid}/comments")
        for note in issue_comments:
            if note.get("user", {}).get("login") != current_user:
                continue
            signatures.add(build_body_signature(note.get("body", "")))

        return signatures

    def get_other_reviewer_comments(self, project_path: str, mr_iid: int) -> dict:
        """Fetch inline review comments left by anyone other than the current user.

        Returns a dict keyed by (file_path, line_number) to a list of
        {"comment_id": int, "body": str, "author": str} threads, so callers
        can decide whether to skip, reply, or post a fresh comment at that spot.
        """
        current_user = self.get_current_username()
        others = {}

        review_comments = self._paginate(f"repos/{project_path}/pulls/{mr_iid}/comments")
        for note in review_comments:
            author = note.get("user", {}).get("login")
            if not author or author == current_user:
                continue
            if not (note.get("path") and note.get("line")):
                continue
            key = (note["path"], note["line"])
            others.setdefault(key, []).append({
                "comment_id": note["id"],
                "body": note.get("body", ""),
                "author": author,
            })

        return others

    def reply_to_comment(
        self, project_path: str, mr_iid: int, comment_id: int, body: str
    ) -> None:
        """Reply inline within an existing review comment thread."""
        self._gh([
            "api",
            "--method",
            "POST",
            f"repos/{project_path}/pulls/{mr_iid}/comments",
            "-f",
            f"body={body}",
            "-F",
            f"in_reply_to={comment_id}",
        ])

    def post_mr_comment(self, project_path: str, mr_iid: int, body: str) -> None:
        self._gh([
            "api",
            "--method",
            "POST",
            f"repos/{project_path}/issues/{mr_iid}/comments",
            "-f",
            f"body={body}",
        ])

    def post_inline_comment(
        self,
        project_path: str,
        mr_iid: int,
        file_path: str,
        new_line: int,
        body: str,
        base_sha: str,
        head_sha: str,
        start_sha: str,
    ) -> None:
        del base_sha
        del start_sha
        self._gh([
            "api",
            "--method",
            "POST",
            f"repos/{project_path}/pulls/{mr_iid}/comments",
            "-f",
            f"body={body}",
            "-f",
            f"commit_id={head_sha}",
            "-f",
            f"path={file_path}",
            "-F",
            f"line={new_line}",
            "-f",
            "side=RIGHT",
        ])
