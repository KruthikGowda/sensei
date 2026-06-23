# AGENTS.md

## Project overview

Sensei is a Python CLI that reviews GitLab MRs using Codex as the AI backbone. It learns the user's review style and applies it to generate human-sounding code review comments.

- **Package**: `sensei` (installed via `pip install -e .`)
- **CLI entry point**: `sensei` (maps to `sensei.cli:main`)
- **Config dir**: `~/.sensei/` (config.yaml, style-profile.md, rules/)
- **Python**: >= 3.9

## Commands

```bash
pytest tests/ -v              # Run all tests
pip install -e .              # Install in dev mode
sensei review <mr-url>        # Review an MR
sensei review <mr-url> --dry-run  # Preview without posting
sensei learn                  # Build style profile from past comments
sensei init                   # Configure GitLab credentials
```

## Architecture

```
src/sensei/
  cli.py            # Click CLI — init, learn, review, post commands
  config.py         # Config loading/saving (~/.sensei/config.yaml)
  gitlab_client.py  # GitLab API client (diffs, file content, inline comments)
  reviewer.py       # Prompt building, Codex invocation, JSON parsing, consolidation
  learner.py        # Scrape past comments, analyze style, build profile
  formatter.py      # Format comments for terminal display and GitLab markdown
```

## Key design decisions

- **Codex is called via `Codex -p` subprocess** — not the API. This keeps auth simple and piggybacks on the user's existing Codex installation.
- **Two-phase review** — Main code review runs first, then a silent-failure-hunting pass runs only if the diff contains error handling patterns.
- **Three comment types** — `must` (inline), `nit` (summary), `test` (consolidated table). This prevents test-gap noise from polluting actual code review.
- **Parallel file review** — ThreadPoolExecutor with max 8 workers. Batch size is configurable via config.yaml.
- **Deduplication** — Before posting, checks existing comments by (file, line) tuple and body prefix to avoid double-posting on re-runs.

## Conventions

- All source goes in `src/sensei/`, tests in `tests/`
- Use `typing.Optional` not `X | None` (Python 3.9 compat)
- Keep files under 500 lines
- Tests use pytest, no mocking framework needed for most tests
- Config files use YAML, style profiles use Markdown
- No console.log — use `click.echo` for user output, `print` only in batch progress

## What NOT to change without good reason

- The `Codex -p` invocation pattern — switching to the API would require API key management
- The must/nit/test classification thresholds (90/80) — these are tuned to the user's preferences
- The comment format (Code Review: / bullets / Suggestion:) — this matches the user's established style
