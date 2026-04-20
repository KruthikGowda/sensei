from sensei.learner import chunk_comments, build_analysis_prompt


def test_chunk_comments_respects_batch_size():
    comments = [{"body": f"comment {i}", "mr_url": f"url/{i}"} for i in range(10)]
    chunks = chunk_comments(comments, batch_size=3)
    assert len(chunks) == 4  # 3+3+3+1
    assert len(chunks[0]) == 3
    assert len(chunks[-1]) == 1


def test_build_analysis_prompt_includes_comments():
    comments = [
        {"body": "Use early returns here", "mr_url": "url/1", "file_path": "src/foo.py"},
        {"body": "Naming: prefer snake_case", "mr_url": "url/2", "file_path": "src/bar.py"},
    ]
    prompt = build_analysis_prompt(comments)
    assert "Use early returns here" in prompt
    assert "snake_case" in prompt


def test_build_style_profile_uses_configured_ai(monkeypatch):
    from sensei.learner import build_style_profile

    calls = []

    def fake_analyze(prompt, ai_config):
        calls.append(ai_config["ai_cli"])
        if "Synthesize" in prompt:
            return "Final profile"
        return "Partial profile"

    monkeypatch.setattr("sensei.learner.analyze_with_ai", fake_analyze)

    comments = [{"body": f"comment {i}", "mr_url": f"url/{i}"} for i in range(55)]
    profile = build_style_profile(comments, {"ai_cli": "codex"})

    assert profile == "Final profile"
    assert calls == ["codex", "codex", "codex"]
