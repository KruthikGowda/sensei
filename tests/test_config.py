from sensei.config import load_config, init_config, save_config, CONFIG_DIR


def test_init_config_creates_directory(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    # Pass username to skip GitLab API call
    init_config(gitlab_pat="glpat-test123", username="testuser")
    assert (config_dir / "config.yaml").exists()


def test_load_config_reads_values(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    init_config(gitlab_pat="glpat-test123", username="testuser")
    config = load_config()
    assert config["gitlab_pat"] == "glpat-test123"
    assert config["gitlab_url"] == "https://gitlab.com"
    assert config["username"] == "testuser"
    assert config["ai_cli"] == "auto"
    assert config["model"] == ""
    assert config["fallback_ai_cli"] is True


def test_load_config_env_var_overrides_pat(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    init_config(gitlab_pat="glpat-from-file", username="testuser")
    monkeypatch.setenv("GITLAB_PAT", "glpat-from-env")
    config = load_config()
    assert config["gitlab_pat"] == "glpat-from-env"


def test_load_config_env_var_overrides_ai_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    init_config(gitlab_pat="glpat-test123", username="testuser")
    monkeypatch.setenv("SENSEI_AI_CLI", "codex")
    monkeypatch.setenv("SENSEI_MODEL", "gpt-5.2")
    monkeypatch.setenv("SENSEI_FALLBACK_AI_CLI", "false")
    config = load_config()
    assert config["ai_cli"] == "codex"
    assert config["model"] == "gpt-5.2"
    assert config["fallback_ai_cli"] is False


def test_save_config_writes_file(tmp_path, monkeypatch):
    config_dir = tmp_path / ".sensei"
    monkeypatch.setattr("sensei.config.CONFIG_DIR", config_dir)
    path = save_config({"gitlab_pat": "x", "gitlab_url": "https://gitlab.com", "username": "u"})
    assert path.exists()
