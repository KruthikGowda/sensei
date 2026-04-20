from pathlib import Path
from typing import Callable, Optional
import click
from sensei.llm_cli import (
    AI_CLI_CHOICES,
    get_ai_cli_status,
    get_ai_label,
    resolve_ai_cli_candidates,
)


@click.group()
def main():
    """Sensei: AI-powered GitLab MR reviewer."""
    pass


def _apply_ai_overrides(
    config: dict,
    ai_cli_override: Optional[str] = None,
    model_override: Optional[str] = None,
) -> dict:
    runtime_config = dict(config)
    if ai_cli_override:
        runtime_config["ai_cli"] = ai_cli_override
        runtime_config.pop("_resolved_ai_cli_candidates", None)
    if model_override is not None:
        runtime_config["model"] = model_override
    return runtime_config


@main.command()
@click.option("--pat", prompt="GitLab PAT", hide_input=True, help="Your GitLab Personal Access Token")
@click.option("--url", default="https://gitlab.com", help="GitLab instance URL")
@click.option("--username", default="", help="GitLab username (auto-detected if omitted)")
@click.option(
    "--ai-cli",
    default="auto",
    type=click.Choice(AI_CLI_CHOICES, case_sensitive=False),
    show_default=True,
    help="AI CLI backend to use for reviews and learning",
)
@click.option("--model", default="", help="Optional model override for the selected AI CLI")
@click.option(
    "--fallback-ai/--no-fallback-ai",
    default=True,
    show_default=True,
    help="Fallback to the other installed AI CLI on auth/quota style failures",
)
def init(pat, url, username, ai_cli, model, fallback_ai):
    """Initialize Sensei with your GitLab credentials."""
    from sensei.config import init_config
    config = init_config(
        gitlab_pat=pat,
        gitlab_url=url,
        username=username,
        ai_cli=ai_cli,
        model=model,
        fallback_ai_cli=fallback_ai,
    )
    click.echo(
        f"Config saved to ~/.sensei/config.yaml "
        f"(user: {config['username']}, ai: {config['ai_cli']}, "
        f"fallback: {'on' if config['fallback_ai_cli'] else 'off'})"
    )


@main.command("set-ai")
@click.option(
    "--ai-cli",
    required=True,
    type=click.Choice(AI_CLI_CHOICES, case_sensitive=False),
    help="Preferred AI CLI backend",
)
@click.option("--model", default=None, help="Optional model override")
@click.option(
    "--fallback-ai/--no-fallback-ai",
    default=None,
    help="Enable or disable automatic fallback to the other installed AI CLI",
)
def set_ai(ai_cli, model, fallback_ai):
    """Update the configured AI backend without rerunning init."""
    from sensei.config import load_config, save_config

    config = load_config()
    config["ai_cli"] = ai_cli
    if model is not None:
        config["model"] = model
    if fallback_ai is not None:
        config["fallback_ai_cli"] = fallback_ai

    path = save_config(config)
    click.echo(
        f"Updated AI settings in {path}: "
        f"ai={config['ai_cli']}, model={config.get('model', '') or 'default'}, "
        f"fallback={'on' if config.get('fallback_ai_cli', True) else 'off'}"
    )


@main.command("use")
@click.argument("ai_cli", type=click.Choice(AI_CLI_CHOICES, case_sensitive=False))
@click.option("--model", default=None, help="Optional model override")
@click.option(
    "--fallback-ai/--no-fallback-ai",
    default=None,
    help="Enable or disable automatic fallback to the other installed AI CLI",
)
def use_ai(ai_cli, model, fallback_ai):
    """Shortcut for switching the preferred AI backend."""
    from sensei.config import load_config, save_config

    config = load_config()
    config["ai_cli"] = ai_cli
    if model is not None:
        config["model"] = model
    if fallback_ai is not None:
        config["fallback_ai_cli"] = fallback_ai

    path = save_config(config)
    click.echo(
        f"Now using {config['ai_cli']} "
        f"(model: {config.get('model', '') or 'default'}, "
        f"fallback: {'on' if config.get('fallback_ai_cli', True) else 'off'}) "
        f"[{path}]"
    )


@main.command()
def doctor():
    """Show AI CLI availability and auth health."""
    from sensei.config import load_config

    config = load_config()
    configured_ai = config.get("ai_cli", "auto")
    click.echo("Sensei AI Doctor")
    click.echo(f"Configured backend: {configured_ai}")
    click.echo(f"Configured model: {config.get('model', '') or 'default'}")
    click.echo(
        f"Fallback enabled: {'yes' if config.get('fallback_ai_cli', True) else 'no'}"
    )

    try:
        candidates = resolve_ai_cli_candidates(dict(config))
        click.echo(f"Resolved order: {', '.join(candidates)}")
    except RuntimeError as exc:
        click.echo(f"Resolved order: unavailable ({exc})")

    for provider in ("codex", "claude"):
        status = get_ai_cli_status(provider)
        installed = "yes" if status["installed"] else "no"
        authenticated = "yes" if status.get("authenticated") else "no"
        click.echo(
            f"{status['label']}: installed={installed}, authenticated={authenticated}"
        )
        if status.get("path"):
            click.echo(f"  path: {status['path']}")
        click.echo(f"  detail: {status.get('detail', 'Unknown')}")


@main.command()
@click.option("--codex", "ai_cli_override", flag_value="codex", default=None, help="Use Codex for this run")
@click.option("--claude", "ai_cli_override", flag_value="claude", help="Use Claude for this run")
@click.option("--auto-ai", "ai_cli_override", flag_value="auto", help="Use auto backend selection for this run")
@click.option("--model", "model_override", default=None, help="Optional model override for this run")
def learn(ai_cli_override, model_override):
    """Scrape your GitLab comments and build a review style profile."""
    import gitlab as gl_module
    from datetime import datetime, timedelta
    from sensei.config import load_config
    from sensei.learner import (
        fetch_user_comments,
        build_style_profile,
        save_style_profile,
    )

    config = load_config()
    config = _apply_ai_overrides(config, ai_cli_override, model_override)
    config["_status_callback"] = click.echo
    click.echo("Connecting to GitLab...")
    gl = gl_module.Gitlab(config["gitlab_url"], private_token=config["gitlab_pat"])
    gl.auth()

    since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    click.echo(f"Fetching comments since {since}...")
    comments = fetch_user_comments(gl, config["username"], since)
    click.echo(f"Found {len(comments)} comments.")

    if not comments:
        click.echo("No comments found. Nothing to learn from.")
        return

    click.echo(f"Analyzing your review style with {get_ai_label(config)}...")
    profile = build_style_profile(comments, config)
    path = save_style_profile(profile)
    click.echo(f"Style profile saved to {path}")


def _review_single_mr(
    client,
    config: dict,
    project_path: str,
    mr_iid: int,
    mr_url: str,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Fetch, review, and consolidate comments for a single MR.

    Never raises — captures exceptions into the 'error' field.
    """
    from sensei.reviewer import (
        review_mr_files,
        consolidate_test_comments,
        load_style_profile,
        load_project_rules,
        load_project_rules_from_repo,
    )

    result = {
        "mr_url": mr_url,
        "mr_iid": mr_iid,
        "project_path": project_path,
        "mr_data": None,
        "comments": [],
        "test_summary": None,
        "error": None,
    }

    try:
        runtime_config = dict(config)
        if progress_callback:
            runtime_config["_status_callback"] = lambda message: progress_callback(
                mr_iid, project_path, message
            )

        if progress_callback:
            progress_callback(mr_iid, project_path, "fetching MR data")

        mr_data = client.get_mr_diff(project_path, mr_iid)
        result["mr_data"] = mr_data

        if progress_callback:
            progress_callback(mr_iid, project_path, "fetching file contents")

        file_contents = {}
        for f in mr_data["files"]:
            if not f["deleted_file"]:
                content = client.get_file_content(
                    project_path, f["new_path"], mr_data["source_branch"]
                )
                file_contents[f["new_path"]] = content

        style_profile = load_style_profile()
        project_rules = load_project_rules(project_path)

        repo_rules = load_project_rules_from_repo(
            client, project_path, mr_data["target_branch"]
        )
        if repo_rules:
            project_rules = f"{project_rules}\n\n{repo_rules}" if project_rules else repo_rules

        mr_context = (
            f"Title: {mr_data['title']}\n"
            f"Description: {mr_data['description']}\n"
            f"Author: {mr_data['author']}"
        )

        if progress_callback:
            progress_callback(mr_iid, project_path, "reviewing files")

        all_comments = review_mr_files(
            files=mr_data["files"],
            file_contents=file_contents,
            style_profile=style_profile,
            project_rules=project_rules,
            mr_context=mr_context,
            ai_config=runtime_config,
            batch_size=config.get("batch_size", 30),
        )

        comments, test_summary = consolidate_test_comments(all_comments)
        result["comments"] = comments
        result["test_summary"] = test_summary

        if progress_callback:
            progress_callback(mr_iid, project_path, "done")

    except Exception as e:
        result["error"] = str(e)
        if progress_callback:
            progress_callback(mr_iid, project_path, f"error: {e}")

    return result


def _post_review_results(
    client,
    project_path: str,
    mr_iid: int,
    mr_data: dict,
    comments: list,
    test_summary: Optional[str],
    diff_lines_map: dict,
    existing: set,
) -> tuple:
    """Post review comments to GitLab. Returns (inline_posted, nits_posted, test_posted, skipped)."""
    from sensei.formatter import format_inline_comment, format_nits_summary

    musts = [c for c in comments if c.get("type") == "must"]
    nits = [c for c in comments if c.get("type") == "nit"]

    inline_posted = 0
    skipped = 0

    for c in musts:
        if c["line"] == 0:
            continue

        body = format_inline_comment(c)
        if (c["file"], c["line"]) in existing or body[:100] in existing:
            skipped += 1
            continue

        valid_lines = diff_lines_map.get(c["file"], set())

        if c["line"] in valid_lines:
            try:
                client.post_inline_comment(
                    project_path=project_path,
                    mr_iid=mr_iid,
                    file_path=c["file"],
                    new_line=c["line"],
                    body=body,
                    base_sha=mr_data["base_sha"],
                    head_sha=mr_data["head_sha"],
                    start_sha=mr_data["start_sha"],
                )
                inline_posted += 1
                continue
            except Exception as exc:
                click.echo(f"  Inline failed for {c['file']}:L{c['line']}, trying as general comment...", err=True)

        file_body = f"**`{c['file']}` L{c['line']}**\n\n{body}"
        try:
            client.post_mr_comment(project_path, mr_iid, file_body)
            inline_posted += 1
        except Exception as e:
            click.echo(f"  Failed: {c['file']}:L{c['line']}: {e}")

    nits_posted = 0
    if nits:
        nits_body = format_nits_summary(nits)
        try:
            client.post_mr_comment(project_path, mr_iid, nits_body)
            nits_posted = 1
        except Exception as e:
            click.echo(f"  Failed posting nits summary: {e}")

    test_posted = 0
    if test_summary:
        try:
            client.post_mr_comment(project_path, mr_iid, test_summary)
            test_posted = 1
        except Exception as e:
            click.echo(f"  Failed posting test summary: {e}")

    return (inline_posted, nits_posted, test_posted, skipped)


def _handle_approval(client, result: dict, dry_run: bool) -> None:
    """Prompt the user to approve, edit, or discard a review result."""
    from sensei.gitlab_client import extract_diff_lines
    from sensei.formatter import format_for_gitlab

    comments = result["comments"]
    test_summary = result["test_summary"]
    mr_data = result["mr_data"]

    if mr_data is None:
        return

    mr_url = result["mr_url"]
    project_path = result["project_path"]
    mr_iid = result["mr_iid"]

    if (not comments and not test_summary) or dry_run:
        return

    action = click.prompt(
        "\nAction", type=click.Choice(["approve", "edit", "discard"]), default="discard"
    )

    if action == "approve":
        click.echo("Checking for existing comments...")
        existing = client.get_existing_comments(project_path, mr_iid)

        diff_lines_map = {}
        for f in mr_data["files"]:
            if f["diff"]:
                diff_lines_map[f["new_path"]] = extract_diff_lines(f["diff"])

        click.echo("Posting comments to GitLab...")
        inline_posted, nits_posted, test_posted, skipped = _post_review_results(
            client=client,
            project_path=project_path,
            mr_iid=mr_iid,
            mr_data=mr_data,
            comments=comments,
            test_summary=test_summary,
            diff_lines_map=diff_lines_map,
            existing=existing,
        )

        parts = [f"{inline_posted} must-fix inline"]
        if nits_posted:
            parts.append(f"{nits_posted} nits summary")
        if test_posted:
            parts.append(f"{test_posted} test coverage summary")
        if skipped:
            parts.append(f"{skipped} skipped")
        click.echo(f"Posted {' + '.join(parts)}")
    elif action == "edit":
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp.write(format_for_gitlab(comments))
            tmp_path = tmp.name
        os.chmod(tmp_path, 0o600)
        click.echo(f"Review saved to {tmp_path} — edit it, then run:")
        click.echo(f"  sensei post {mr_url} {tmp_path}")
    else:
        click.echo("Review discarded.")


@main.command()
@click.argument("mr_url")
@click.option("--dry-run", is_flag=True, help="Show review without posting option")
@click.option("--codex", "ai_cli_override", flag_value="codex", default=None, help="Use Codex for this run")
@click.option("--claude", "ai_cli_override", flag_value="claude", help="Use Claude for this run")
@click.option("--auto-ai", "ai_cli_override", flag_value="auto", help="Use auto backend selection for this run")
@click.option("--model", "model_override", default=None, help="Optional model override for this run")
def review(mr_url, dry_run, ai_cli_override, model_override):
    """Review a GitLab Merge Request."""
    from sensei.config import load_config
    from sensei.gitlab_client import parse_mr_url, validate_mr_url_origin, GitLabClient, extract_diff_lines
    from sensei.reviewer import (
        review_mr_files,
        consolidate_test_comments,
        load_style_profile,
        load_project_rules,
        load_project_rules_from_repo,
    )
    from sensei.formatter import format_review, format_for_gitlab, format_inline_comment, format_nits_summary

    config = load_config()
    config = _apply_ai_overrides(config, ai_cli_override, model_override)
    config["_status_callback"] = lambda message: click.echo(message, err=True)
    try:
        project_path, mr_iid = parse_mr_url(mr_url)
        validate_mr_url_origin(mr_url, config["gitlab_url"])
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"Fetching MR !{mr_iid} from {project_path}...")
    client = GitLabClient(config["gitlab_url"], config["gitlab_pat"])
    mr_data = client.get_mr_diff(project_path, mr_iid)

    click.echo(f"MR: {mr_data['title']}")
    click.echo(f"Files changed: {len(mr_data['files'])}")

    # Fetch full file contents for context
    click.echo("Fetching file contents...")
    file_contents = {}
    for f in mr_data["files"]:
        if not f["deleted_file"]:
            content = client.get_file_content(
                project_path, f["new_path"], mr_data["source_branch"]
            )
            file_contents[f["new_path"]] = content

    # Load review context
    style_profile = load_style_profile()
    project_rules = load_project_rules(project_path)

    # Fetch rules from the repo itself (CLAUDE.md, etc.)
    repo_rules = load_project_rules_from_repo(
        client, project_path, mr_data["target_branch"]
    )
    if repo_rules:
        project_rules = f"{project_rules}\n\n{repo_rules}" if project_rules else repo_rules

    mr_context = (
        f"Title: {mr_data['title']}\n"
        f"Description: {mr_data['description']}\n"
        f"Author: {mr_data['author']}"
    )

    # Review
    click.echo(f"Reviewing files with {get_ai_label(config)}...")
    all_comments = review_mr_files(
        files=mr_data["files"],
        file_contents=file_contents,
        style_profile=style_profile,
        project_rules=project_rules,
        mr_context=mr_context,
        ai_config=config,
        batch_size=config.get("batch_size", 30),
    )

    # Consolidate test-gap comments into a single summary
    comments, test_summary = consolidate_test_comments(all_comments)

    # Display
    click.echo("\n" + "=" * 60)
    click.echo(format_review(comments, test_summary))
    click.echo("=" * 60)

    if (not comments and not test_summary) or dry_run:
        return

    # Approval flow
    action = click.prompt(
        "\nAction", type=click.Choice(["approve", "edit", "discard"]), default="discard"
    )

    if action == "approve":
        click.echo("Checking for existing comments...")
        existing = client.get_existing_comments(project_path, mr_iid)

        # Build diff line sets per file for validation
        diff_lines_map = {}
        for f in mr_data["files"]:
            if f["diff"]:
                diff_lines_map[f["new_path"]] = extract_diff_lines(f["diff"])

        # Split comments into must-fix and nits
        musts = [c for c in comments if c.get("type") == "must"]
        nits = [c for c in comments if c.get("type") == "nit"]

        click.echo("Posting comments to GitLab...")
        inline_posted = 0
        skipped = 0

        # Post must-fix comments as inline
        for c in musts:
            if c["line"] == 0:
                continue

            body = format_inline_comment(c)
            if (c["file"], c["line"]) in existing or body[:100] in existing:
                skipped += 1
                continue

            valid_lines = diff_lines_map.get(c["file"], set())

            if c["line"] in valid_lines:
                try:
                    client.post_inline_comment(
                        project_path=project_path,
                        mr_iid=mr_iid,
                        file_path=c["file"],
                        new_line=c["line"],
                        body=body,
                        base_sha=mr_data["base_sha"],
                        head_sha=mr_data["head_sha"],
                        start_sha=mr_data["start_sha"],
                    )
                    inline_posted += 1
                    continue
                except Exception as exc:
                    click.echo(f"  Inline failed for {c['file']}:L{c['line']}, trying as general comment...", err=True)

            # Line not in diff or inline failed — post as general comment
            file_body = f"**`{c['file']}` L{c['line']}**\n\n{body}"
            try:
                client.post_mr_comment(project_path, mr_iid, file_body)
                inline_posted += 1
            except Exception as e:
                click.echo(f"  Failed: {c['file']}:L{c['line']}: {e}")

        # Post all nits as one summary comment
        nits_posted = 0
        if nits:
            nits_body = format_nits_summary(nits)
            try:
                client.post_mr_comment(project_path, mr_iid, nits_body)
                nits_posted = 1
            except Exception as e:
                click.echo(f"  Failed posting nits summary: {e}")

        # Post test coverage summary as one comment
        test_posted = 0
        if test_summary:
            try:
                client.post_mr_comment(project_path, mr_iid, test_summary)
                test_posted = 1
            except Exception as e:
                click.echo(f"  Failed posting test summary: {e}")

        parts = [f"{inline_posted} must-fix inline"]
        if nits_posted:
            parts.append(f"{nits_posted} nits summary")
        if test_posted:
            parts.append(f"{test_posted} test coverage summary")
        if skipped:
            parts.append(f"{skipped} skipped")
        click.echo(f"Posted {' + '.join(parts)}")
    elif action == "edit":
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp.write(format_for_gitlab(comments))
            tmp_path = tmp.name
        os.chmod(tmp_path, 0o600)
        click.echo(f"Review saved to {tmp_path} — edit it, then run:")
        click.echo(f"  sensei post {mr_url} {tmp_path}")
    else:
        click.echo("Review discarded.")


@main.command()
@click.argument("mr_url")
@click.argument("review_file", type=click.Path(exists=True))
def post(mr_url, review_file):
    """Post an edited review file to a GitLab MR."""
    from sensei.config import load_config
    from sensei.gitlab_client import parse_mr_url, GitLabClient

    config = load_config()
    project_path, mr_iid = parse_mr_url(mr_url)
    body = Path(review_file).read_text()

    client = GitLabClient(config["gitlab_url"], config["gitlab_pat"])
    client.post_mr_comment(project_path, mr_iid, body)
    click.echo("Review posted!")


@main.command("review-batch")
@click.argument("urls", nargs=-1)
@click.option("--file", "url_file", type=click.Path(exists=True), help="File with MR URLs (one per line)")
@click.option("--concurrency", default=3, type=click.IntRange(1, 10), help="Max parallel reviews (1-10)")
@click.option("--dry-run", is_flag=True, help="Show results without approval prompt")
def review_batch(urls, url_file, concurrency, dry_run):
    """Review multiple GitLab MRs in parallel.

    Pass URLs as arguments or use --file with a file containing one URL per line.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sensei.config import load_config
    from sensei.gitlab_client import parse_mr_url, validate_mr_url_origin, GitLabClient
    from sensei.formatter import format_review, format_batch_progress

    # Merge URLs from args and --file
    all_urls = list(urls)
    if url_file:
        with open(url_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    all_urls.append(line)

    if not all_urls:
        click.echo("Error: provide MR URLs as arguments or via --file", err=True)
        raise SystemExit(1)

    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    all_urls = unique_urls

    MAX_BATCH_SIZE = 20
    if len(all_urls) > MAX_BATCH_SIZE:
        click.echo(f"Error: too many MRs ({len(all_urls)}). Maximum is {MAX_BATCH_SIZE}.", err=True)
        raise SystemExit(1)

    config = load_config()

    # Parse & validate all URLs upfront
    parsed = []
    for url in all_urls:
        try:
            project_path, mr_iid = parse_mr_url(url)
            validate_mr_url_origin(url, config["gitlab_url"])
            parsed.append((url, project_path, mr_iid))
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

    client = GitLabClient(config["gitlab_url"], config["gitlab_pat"])

    click.echo(f"Reviewing {len(parsed)} MRs with concurrency={concurrency}...")

    import threading
    progress_lock = threading.Lock()

    def _progress(mr_iid, project_path, status):
        with progress_lock:
            click.echo(format_batch_progress(mr_iid, project_path, status), err=True)

    results_map = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_parsed = {}
        for url, project_path, mr_iid in parsed:
            future = pool.submit(
                _review_single_mr,
                client=client,
                config=config,
                project_path=project_path,
                mr_iid=mr_iid,
                mr_url=url,
                progress_callback=_progress,
            )
            future_to_parsed[future] = (url, project_path, mr_iid)

        for future in as_completed(future_to_parsed):
            url, project_path, mr_iid = future_to_parsed[future]
            try:
                results_map[url] = future.result()
            except Exception as e:
                results_map[url] = {
                    "mr_url": url,
                    "mr_iid": mr_iid,
                    "project_path": project_path,
                    "mr_data": None,
                    "comments": [],
                    "test_summary": None,
                    "error": str(e),
                }

    # Sequential results + approval in input order
    for url, project_path, mr_iid in parsed:
        result = results_map[url]

        click.echo(f"\n{'=' * 60}")
        click.echo(f"MR !{mr_iid} — {project_path}")
        click.echo("=" * 60)

        if result.get("error"):
            click.echo(f"Error: {result['error']}")
            continue

        mr_data = result["mr_data"]
        click.echo(f"Title: {mr_data['title']}")
        click.echo(format_review(result["comments"], result["test_summary"]))

        _handle_approval(client, result, dry_run)

    click.echo("\nBatch review complete.")


if __name__ == "__main__":
    main()
