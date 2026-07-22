import re
from urllib.parse import urlparse
import gitlab


def normalize_comment_body(body: str) -> str:
    """Normalize comment text so duplicate checks ignore whitespace-only drift."""
    return re.sub(r"\s+", " ", body or "").strip()


def build_body_signature(body: str) -> tuple:
    """Create a stable signature for a general comment body."""
    return ("body", normalize_comment_body(body))


def build_inline_signature(file_path: str, line_number: int) -> tuple:
    """Create a stable signature for an inline comment location."""
    return ("inline", file_path, line_number)


def extract_diff_lines(diff: str) -> set:
    """Extract new-side line numbers that are part of the diff."""
    lines = set()
    current_line = 0
    for line in diff.splitlines():
        # Parse hunk headers: @@ -old,count +new,count @@
        hunk_match = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue
        if line.startswith('+') and not line.startswith('+++'):
            lines.add(current_line)
            current_line += 1
        elif line.startswith('-') and not line.startswith('---'):
            # Removed lines don't advance the new-side counter
            pass
        else:
            # Context line
            current_line += 1
    return lines


def parse_mr_url(url: str) -> tuple:
    """Extract project path and MR IID from a GitLab MR URL.

    Returns (project_path, mr_iid) tuple.
    """
    url = url.rstrip("/")
    parsed = urlparse(url)
    match = re.match(
        r"^/(.+?)/-/merge_requests/(\d+)",
        parsed.path,
    )
    if not match:
        raise ValueError(f"Invalid MR URL: {url}")
    return match.group(1), int(match.group(2))


def validate_mr_url_origin(url: str, configured_url: str) -> None:
    """Verify MR URL hostname matches the configured GitLab instance."""
    mr_host = urlparse(url).netloc
    configured_host = urlparse(configured_url).netloc
    if mr_host != configured_host:
        raise ValueError(
            f"MR URL host ({mr_host}) does not match configured GitLab ({configured_host})."
        )


class GitLabClient:
    def __init__(self, url: str, pat: str):
        self.gl = gitlab.Gitlab(url, private_token=pat)
        self.gl.auth()

    def get_mr_diff(self, project_path: str, mr_iid: int) -> dict:
        """Fetch MR metadata and per-file diffs."""
        project = self.gl.projects.get(project_path)
        mr = project.mergerequests.get(mr_iid)
        changes = mr.changes()

        # Get diff refs for inline commenting
        diff_refs = changes.get("diff_refs", {}) or {}

        return {
            "title": mr.title,
            "description": mr.description or "",
            "source_branch": mr.source_branch,
            "target_branch": mr.target_branch,
            "author": mr.author["username"],
            "web_url": mr.web_url,
            "base_sha": diff_refs.get("base_sha", ""),
            "head_sha": diff_refs.get("head_sha", ""),
            "start_sha": diff_refs.get("start_sha", ""),
            "files": [
                {
                    "old_path": c["old_path"],
                    "new_path": c["new_path"],
                    "diff": c["diff"],
                    "new_file": c["new_file"],
                    "deleted_file": c["deleted_file"],
                    "renamed_file": c["renamed_file"],
                }
                for c in changes["changes"]
            ],
        }

    def get_file_content(self, project_path: str, file_path: str, ref: str) -> str:
        """Fetch full file content at a given ref."""
        project = self.gl.projects.get(project_path)
        try:
            f = project.files.get(file_path=file_path, ref=ref)
            try:
                return f.decode().decode("utf-8")
            except UnicodeDecodeError:
                return ""
        except gitlab.exceptions.GitlabGetError:
            return ""

    def get_existing_comments(self, project_path: str, mr_iid: int) -> set:
        """Fetch existing comment signatures to avoid duplicates.
        Returns a set of typed signatures for inline locations and comment bodies
        posted by the current user.
        """
        project = self.gl.projects.get(project_path)
        mr = project.mergerequests.get(mr_iid)
        current_user = self.gl.user.username
        signatures = set()

        # Check discussions (inline comments)
        for discussion in mr.discussions.list(per_page=100, iterator=True):
            for note in discussion.attributes.get("notes", []):
                if note.get("author", {}).get("username") != current_user:
                    continue
                body = note.get("body", "")
                pos = note.get("position")
                if pos and pos.get("new_path") and pos.get("new_line"):
                    signatures.add(
                        build_inline_signature(pos["new_path"], pos["new_line"])
                    )
                signatures.add(build_body_signature(body))

        # Check notes (general comments)
        for note in mr.notes.list(per_page=100, iterator=True):
            if note.author.get("username") != current_user:
                continue
            signatures.add(build_body_signature(note.body))

        return signatures

    def get_other_reviewer_comments(self, project_path: str, mr_iid: int) -> dict:
        """Fetch inline comments left by anyone other than the current user.

        Returns a dict keyed by (file_path, line_number) to a list of
        {"discussion_id": str, "body": str, "author": str} threads, so callers
        can decide whether to skip, reply, or post a fresh comment at that spot.
        """
        project = self.gl.projects.get(project_path)
        mr = project.mergerequests.get(mr_iid)
        current_user = self.gl.user.username
        others = {}

        for discussion in mr.discussions.list(per_page=100, iterator=True):
            for note in discussion.attributes.get("notes", []):
                author = note.get("author", {}).get("username")
                if not author or author == current_user:
                    continue
                pos = note.get("position")
                if not (pos and pos.get("new_path") and pos.get("new_line")):
                    continue
                key = (pos["new_path"], pos["new_line"])
                others.setdefault(key, []).append({
                    "discussion_id": discussion.id,
                    "body": note.get("body", ""),
                    "author": author,
                })

        return others

    def reply_to_discussion(
        self, project_path: str, mr_iid: int, discussion_id: str, body: str
    ) -> None:
        """Reply inline within an existing discussion thread."""
        project = self.gl.projects.get(project_path)
        mr = project.mergerequests.get(mr_iid)
        discussion = mr.discussions.get(discussion_id)
        discussion.notes.create({"body": body})

    def post_mr_comment(
        self, project_path: str, mr_iid: int, body: str
    ) -> None:
        """Post a general comment on an MR."""
        project = self.gl.projects.get(project_path)
        mr = project.mergerequests.get(mr_iid)
        mr.notes.create({"body": body})

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
        """Post an inline discussion on a specific line."""
        project = self.gl.projects.get(project_path)
        mr = project.mergerequests.get(mr_iid)
        mr.discussions.create({
            "body": body,
            "position": {
                "base_sha": base_sha,
                "head_sha": head_sha,
                "start_sha": start_sha,
                "position_type": "text",
                "new_path": file_path,
                "new_line": new_line,
            },
        })
