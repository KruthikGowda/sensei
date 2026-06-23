import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from sensei.config import CONFIG_DIR
from sensei.llm_cli import REVIEW_OUTPUT_SCHEMA, run_prompt

GENERATED_REVIEW_BASENAMES = {
    "bun.lock",
    "bun.lockb",
    "cargo.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
GENERATED_REVIEW_SUFFIXES = (".min.css", ".min.js", ".snap")
LARGE_FILE_CONTEXT_THRESHOLD = 16000
MAX_CONTEXT_WINDOWS = 4
CONTEXT_WINDOW_PADDING = 20
REVIEW_REASONING_EFFORT = "low"


def build_file_review_prompt(
    file_path: str,
    diff: str,
    file_content: str,
    style_profile: str,
    project_rules: str,
    mr_context: str,
) -> str:
    file_context_heading, file_context_body = _build_file_context(
        file_path, diff, file_content
    )
    return f"""You are a thoughtful senior engineer reviewing a teammate's code. Write like a human — direct, specific, helpful. No corporate tone, no robotic structure, no filler.

## Your Review Style
{style_profile}

## Project Rules
{project_rules}

## MR Context
{mr_context}

## File: {file_path}

### {file_context_heading}
```
{file_context_body}
```

### Diff (review these changes):
```diff
{diff}
```

## What to look for
- Bugs, type safety issues, implicit `any`
- Silent failures, swallowed errors, fallbacks that hide problems
- Naming inconsistencies, missing conventions
- Security issues, environment misconfigs
- Architecture violations (cross-layer leakage, duplication across packages)
- Reinvented UI primitives: custom components, bespoke CSS class systems, or ad-hoc charts
  when the diff should reuse existing shared patterns such as Setu components, established
  classnames/utilities, or existing chart wrappers

## Test coverage gaps
If the diff adds or changes logic that has no corresponding test, emit a SEPARATE entry with `"type": "test"`. Do NOT write a full comment — just state what needs testing in one line.
Example: `{{"line": 42, "confidence": 92, "type": "test", "comment": "New `redirectToOnboarding` logic needs tests for different productType/accountId combos"}}`
Do NOT emit per-file "missing tests" comments as "must" or "nit" — use "test" type ONLY. These will be consolidated into a single test summary.

## Writing style for comments

Write each comment like a thoughtful human colleague — concise, scannable, empathetic. Keep it SHORT.

EXAMPLE:

"Code Review: `user` param is not null-checked before `.name` access.

• Crashes at runtime if caller passes undefined

Suggestion: Add a guard clause: `if (!user) return null;`"

RULES:
1. Start with "Code Review:" — ONE short sentence, max 15 words. Be specific (use actual names, values).
2. 1-2 bullet points (•) max for the specific issues — one line each.
3. End with "Suggestion:" — one concrete, actionable sentence.
4. Only flag issues you're genuinely confident about (>= 80%).
5. Keep entire comment to 4-6 lines. If you need more than 2 bullets, you're overexplaining.

## Output Format

Return a valid JSON array. Each element:
- "line": integer (line number in the new file)
- "confidence": integer (80-100, used internally — not shown in comment)
- "type": "must", "nit", or "test" — "must" for confidence >= 90 (bugs, security, rule violations), "nit" for confidence 80-89 (naming, style, suggestions), "test" for missing test coverage (these get consolidated into one summary, not posted inline)
- "comment": string (the full human-readable comment as described above; for "test" type, just a one-line description of what needs testing)

If the changes look good, return: []

Return ONLY the JSON array, nothing else."""


def build_silent_failure_prompt(
    file_path: str,
    diff: str,
    file_content: str,
    project_rules: str,
) -> str:
    file_context_heading, file_context_body = _build_file_context(
        file_path, diff, file_content
    )
    return f"""You are reviewing error handling in a teammate's code. Look for places where errors are silently swallowed, users get no feedback, or fallbacks mask real problems.

## Project Rules
{project_rules}

## File: {file_path}

### {file_context_heading}
```
{file_context_body}
```

### Diff (focus on these changes):
```diff
{diff}
```

## What to look for
- Empty catch blocks
- Catch blocks that only log and continue — user gets no feedback
- Returning null/undefined/default on error without logging
- Optional chaining (?.) silently skipping important operations
- Fallback values that mask underlying problems
- Missing error propagation — error should bubble up but doesn't
- Generic catch blocks catching all errors when only specific ones expected

## Writing style

RULES:
1. "Code Review:" — ONE short sentence, max 15 words. Be specific.
2. 1-2 bullet points (•) max — one line each.
3. "Suggestion:" — one concrete, actionable sentence.
4. Only flag issues >= 80% confidence.
5. Keep entire comment to 4-6 lines. If you need more than 2 bullets, you're overexplaining.

## Output Format

Return a valid JSON array. Each element:
- "line": integer
- "confidence": integer (80-100)
- "type": "must" or "nit" — "must" for confidence >= 90 (bugs, security, missing error handling), "nit" for confidence 80-89 (style, suggestions)
- "comment": string (the full human-readable comment)

If no issues found, return: []

Return ONLY the JSON array."""


def _build_file_context(file_path: str, diff: str, file_content: str) -> tuple:
    if _is_generated_or_lockfile(file_path):
        return (
            "File context (generated file omitted to keep review fast)",
            "Generated file detected. Review the diff directly and only call out confident issues "
            "that are visible from the changed entries.",
        )

    if len(file_content) <= LARGE_FILE_CONTEXT_THRESHOLD:
        return ("Full file content (for context):", file_content)

    focused_context = _extract_relevant_file_context(file_content, diff)
    return (
        "Relevant file context near changed lines (truncated for speed):",
        focused_context,
    )


def _is_generated_or_lockfile(file_path: str) -> bool:
    normalized = file_path.lower()
    basename = normalized.rsplit("/", 1)[-1]
    return basename in GENERATED_REVIEW_BASENAMES or basename.endswith(
        GENERATED_REVIEW_SUFFIXES
    )


def _extract_relevant_file_context(file_content: str, diff: str) -> str:
    lines = file_content.splitlines()
    if not lines:
        return file_content

    ranges = _extract_changed_line_ranges(diff)
    if not ranges:
        return _truncate_file_content(file_content)

    windows = []
    for start, count in ranges[:MAX_CONTEXT_WINDOWS]:
        if count <= 0:
            count = 1
        window_start = max(start - CONTEXT_WINDOW_PADDING, 1)
        window_end = min(start + count + CONTEXT_WINDOW_PADDING - 1, len(lines))
        windows.append((window_start, window_end))

    merged_windows = []
    for start, end in sorted(windows):
        if not merged_windows or start > merged_windows[-1][1] + 1:
            merged_windows.append([start, end])
        else:
            merged_windows[-1][1] = max(merged_windows[-1][1], end)

    sections = []
    for start, end in merged_windows[:MAX_CONTEXT_WINDOWS]:
        chunk = "\n".join(lines[start - 1 : end])
        sections.append(f"# Lines {start}-{end}\n{chunk}")

    if not sections:
        return _truncate_file_content(file_content)

    return "\n\n...\n\n".join(sections)


def _extract_changed_line_ranges(diff: str) -> list:
    ranges = []
    for match in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.MULTILINE):
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        ranges.append((start, count))
    return ranges


def _truncate_file_content(file_content: str, limit: int = LARGE_FILE_CONTEXT_THRESHOLD) -> str:
    if len(file_content) <= limit:
        return file_content
    head = file_content[: limit // 2].rstrip()
    tail = file_content[-(limit // 2) :].lstrip()
    return (
        f"{head}\n\n...\n\n"
        f"[truncated {len(file_content) - limit} characters to keep review fast]\n\n"
        f"{tail}"
    )


def parse_json_review(raw: str, file_path: str) -> list:
    """Parse JSON review output into structured comments."""
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON array from the response
        # Sometimes the model wraps it in markdown code blocks
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            return []
        try:
            parsed = json.loads(json_match.group())
        except json.JSONDecodeError:
            return []

    if isinstance(parsed, dict) and isinstance(parsed.get("comments"), list):
        items = parsed["comments"]
    else:
        items = parsed

    comments = []
    for item in items:
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence", 0)
        if confidence < 80:
            continue

        # Support both new format ("comment") and old format ("observation"/"rule"/"suggestion")
        if item.get("comment"):
            body = item["comment"]
        else:
            body_parts = []
            if item.get("observation"):
                body_parts.append(item["observation"])
            if item.get("rule"):
                body_parts.append(f'Per our standards: "{item["rule"]}"')
            if item.get("suggestion"):
                body_parts.append(f"Suggestion: {item['suggestion']}")
            body = "\n".join(body_parts)

        # Determine type: "test" preserved as-is, else "must" >= 90, "nit" 80-89
        raw_type = item.get("type", "")
        if raw_type == "test":
            comment_type = "test"
        else:
            comment_type = raw_type if raw_type in ("must", "nit") else ("must" if confidence >= 90 else "nit")

        comments.append({
            "file": file_path,
            "line": item.get("line", 0),
            "confidence": confidence,
            "type": comment_type,
            "body": body,
        })

    return comments


# Keep backward compatibility for old format
def parse_review_output(raw: str, file_path: str) -> list:
    """Parse review output — tries JSON first, falls back to text format."""
    comments = parse_json_review(raw, file_path)
    if comments:
        return comments

    # Fallback: old text-based parsing
    if raw.strip() == "LGTM":
        return []

    blocks = re.split(r'^---\s*$', raw, flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        header_match = re.match(r'L(\d+)\s*\[(\w+)\]', lines[0].strip())
        if header_match:
            comments.append({
                "file": file_path,
                "line": int(header_match.group(1)),
                "confidence": 85,
                "severity": "important",
                "category": header_match.group(2),
                "body": "\n".join(lines[1:]).strip(),
            })

    return comments


def _has_error_handling(diff: str) -> bool:
    """Check if diff contains error handling patterns worth hunting."""
    patterns = [
        r'catch\s*\(',
        r'\.catch\(',
        r'try\s*\{',
        r'onError',
        r'\.error\(',
        r'fallback',
        r'\?\.',  # optional chaining
        r'\?\?',  # nullish coalescing
    ]
    for pattern in patterns:
        if re.search(pattern, diff):
            return True
    return False


def review_file(
    file_path: str,
    diff: str,
    file_content: str,
    style_profile: str,
    project_rules: str,
    mr_context: str,
    ai_config: dict,
) -> list:
    """Review a single file using the configured AI CLI."""
    # Phase 1: Main code review
    prompt = build_file_review_prompt(
        file_path, diff, file_content, style_profile, project_rules, mr_context
    )
    try:
        raw_review = run_prompt(
            prompt,
            timeout=180,
            config=ai_config,
            schema=REVIEW_OUTPUT_SCHEMA,
            reasoning_effort=REVIEW_REASONING_EFFORT,
        )
    except RuntimeError as exc:
        return [{"file": file_path, "line": 0, "confidence": 100, "type": "must",
                 "body": f"Review failed for this file. {exc}"}]

    comments = parse_json_review(raw_review, file_path)

    # Phase 2: Silent failure hunting (only if diff has error handling patterns)
    if _has_error_handling(diff):
        sf_prompt = build_silent_failure_prompt(
            file_path, diff, file_content, project_rules
        )
        try:
            sf_raw_review = run_prompt(
                sf_prompt,
                timeout=180,
                config=ai_config,
                schema=REVIEW_OUTPUT_SCHEMA,
                reasoning_effort=REVIEW_REASONING_EFFORT,
            )
            sf_comments = parse_json_review(sf_raw_review, file_path)
            # Deduplicate by line number
            existing_lines = {c["line"] for c in comments}
            for c in sf_comments:
                if c["line"] not in existing_lines:
                    comments.append(c)
        except RuntimeError:
            pass

    return comments


def review_mr_files(
    files: list,
    file_contents: dict,
    style_profile: str,
    project_rules: str,
    mr_context: str,
    ai_config: dict,
    batch_size: int = 30,
) -> list:
    """Review all MR files in parallel batches."""
    all_comments = []

    for batch_start in range(0, len(files), batch_size):
        batch = files[batch_start : batch_start + batch_size]
        print(f"  Reviewing batch {batch_start // batch_size + 1} "
              f"({len(batch)} files)...")

        with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as executor:
            futures = {}
            for f in batch:
                path = f["new_path"]
                if f["deleted_file"]:
                    continue
                future = executor.submit(
                    review_file,
                    file_path=path,
                    diff=f["diff"],
                    file_content=file_contents.get(path, ""),
                    style_profile=style_profile,
                    project_rules=project_rules,
                    mr_context=mr_context,
                    ai_config=ai_config,
                )
                futures[future] = path

            for future in as_completed(futures):
                path = futures[future]
                try:
                    comments = future.result()
                    all_comments.extend(comments)
                    status = "LGTM" if not comments else f"{len(comments)} comments"
                    print(f"    {path}: {status}")
                except subprocess.TimeoutExpired:
                    all_comments.append({
                        "file": path, "line": 0, "confidence": 100, "type": "must",
                        "body": "Review timed out for this file.",
                    })
                except Exception:
                    all_comments.append({
                        "file": path, "line": 0, "confidence": 100, "type": "must",
                        "body": "Review failed unexpectedly for this file.",
                    })

    # Sort by confidence (highest first), then by file
    all_comments.sort(key=lambda c: (-c.get("confidence", 0), c.get("file", "")))

    return all_comments


def consolidate_test_comments(comments: list) -> tuple:
    """Separate test-gap comments from review comments and build a summary.

    Returns (review_comments, test_summary_comment_or_none).
    """
    review = [c for c in comments if c.get("type") != "test"]
    test_gaps = [c for c in comments if c.get("type") == "test"]

    if not test_gaps:
        return review, None

    # Group by file, deduplicate similar descriptions
    from itertools import groupby
    from operator import itemgetter

    sorted_gaps = sorted(test_gaps, key=itemgetter("file"))
    rows = []
    for file_path, file_comments in groupby(sorted_gaps, key=itemgetter("file")):
        for c in file_comments:
            rows.append((file_path, c.get("body", c.get("comment", ""))))

    # Deduplicate rows with near-identical descriptions (same file, same first 60 chars)
    seen = set()
    unique_rows = []
    for file_path, desc in rows:
        key = (file_path, desc[:60])
        if key not in seen:
            seen.add(key)
            unique_rows.append((file_path, desc))

    # Build markdown table
    lines = [
        "## Test Coverage Summary",
        "",
        "This MR adds/changes logic without corresponding tests. "
        "Target ~80% coverage for new code. Key areas needing tests:",
        "",
        "| Area | What to test |",
        "|---|---|",
    ]
    for file_path, desc in unique_rows:
        # Use short filename for readability
        short = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
        lines.append(f"| `{short}` | {desc} |")

    lines.append("")
    lines.append(
        "If multiple files share the same pattern (e.g., identical prop wiring), "
        "one well-tested example + replication is fine — focus depth on unique logic."
    )

    return review, "\n".join(lines)


def load_style_profile() -> str:
    path = CONFIG_DIR / "style-profile.md"
    if path.exists():
        return path.read_text()
    return "No style profile found. Review using general best practices."


def load_project_rules(project_path: str) -> str:
    """Try to load project-specific rules from ~/.sensei/rules/."""
    safe_name = project_path.replace("/", "_").replace("..", "")
    rules_file = CONFIG_DIR / "rules" / f"{safe_name}.md"
    # Ensure resolved path stays within the rules directory
    if not rules_file.resolve().is_relative_to((CONFIG_DIR / "rules").resolve()):
        return "No project-specific rules found."
    if rules_file.exists():
        return rules_file.read_text()
    return "No project-specific rules found."


def load_project_rules_from_repo(client, project_path: str, ref: str) -> str:
    """Fetch AI instruction files from the repo."""
    if client is None:
        return ""

    rules = []
    rule_files = [
        "AGENTS.md",
        "CLAUDE.md",
        "CODEX.md",
        ".claude/rules.md",
        "CODING_PRINCIPLES.md",
    ]

    for rule_file in rule_files:
        content = client.get_file_content(project_path, rule_file, ref)
        if content:
            rules.append(f"# From {rule_file}\n\n{content}")

    return "\n\n---\n\n".join(rules) if rules else ""
