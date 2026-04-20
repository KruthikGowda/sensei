from pathlib import Path
import gitlab
from sensei.config import CONFIG_DIR
from sensei.llm_cli import run_prompt


def fetch_user_comments(gl: gitlab.Gitlab, username: str, since: str) -> list:
    """Fetch all MR comments by the user since a given date."""
    comments = []
    user = gl.users.list(username=username)[0]

    events = user.events.list(
        action="commented",
        after=since,
        per_page=100,
        iterator=True,
    )

    for event in events:
        if event.target_type == "Note" and event.note is not None:
            comments.append({
                "body": event.note.get("body", ""),
                "project_id": event.project_id,
                "mr_url": event.target_title or "",
                "created_at": event.created_at,
            })

    return comments


def chunk_comments(comments: list, batch_size: int = 50) -> list:
    """Split comments into chunks for processing."""
    return [
        comments[i : i + batch_size]
        for i in range(0, len(comments), batch_size)
    ]


def build_analysis_prompt(comments: list) -> str:
    """Build a prompt for Claude to analyze review style."""
    formatted = "\n\n---\n\n".join(
        f"File: {c.get('file_path', 'unknown')}\nComment: {c['body']}"
        for c in comments
    )

    return f"""Analyze these code review comments by a single reviewer. Extract their:

1. **Priorities** — What do they care most about? (performance, readability, naming, security, etc.)
2. **Tone** — How do they phrase feedback? (direct, gentle, questioning, etc.)
3. **Patterns** — Recurring themes or rules they enforce
4. **Pet peeves** — Things that consistently bother them
5. **Praise patterns** — What do they approve of or compliment?

Comments:

{formatted}

Output a structured style profile in markdown format."""


def analyze_with_ai(prompt: str, ai_config: dict) -> str:
    """Run a prompt through the configured AI CLI and return the output."""
    return run_prompt(prompt, timeout=120, config=ai_config)


def build_style_profile(comments: list, ai_config: dict) -> str:
    """Analyze comments in batches, then synthesize a final profile."""
    chunks = chunk_comments(comments, batch_size=50)
    partial_analyses = []

    for i, chunk in enumerate(chunks):
        print(f"  Analyzing batch {i + 1}/{len(chunks)}...")
        prompt = build_analysis_prompt(chunk)
        analysis = analyze_with_ai(prompt, ai_config)
        partial_analyses.append(analysis)

    if len(partial_analyses) == 1:
        return partial_analyses[0]

    synthesis_prompt = f"""You have {len(partial_analyses)} partial analyses of a code reviewer's style.
Synthesize them into ONE definitive style profile.

Sections:
1. **Priorities** (ranked)
2. **Tone & Voice**
3. **Recurring Rules** (bullet list)
4. **Pet Peeves**
5. **What They Praise**
6. **Example Phrases** (actual quotes they use)

Partial analyses:

{"---".join(partial_analyses)}"""

    return analyze_with_ai(synthesis_prompt, ai_config)


def save_style_profile(profile: str) -> Path:
    """Save the style profile to disk."""
    path = CONFIG_DIR / "style-profile.md"
    path.write_text(profile)
    path.chmod(0o600)
    return path
