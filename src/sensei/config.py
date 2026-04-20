import os
from pathlib import Path
import yaml

CONFIG_DIR = Path.home() / ".sensei"


def init_config(
    gitlab_pat: str,
    gitlab_url: str = "https://gitlab.com",
    username: str = "",
    ai_cli: str = "auto",
    model: str = "",
    fallback_ai_cli: bool = True,
):
    import gitlab
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    rules_dir = CONFIG_DIR / "rules"
    rules_dir.mkdir(exist_ok=True)
    rules_dir.chmod(0o700)

    # Derive username from GitLab API if not provided
    if not username:
        gl = gitlab.Gitlab(gitlab_url, private_token=gitlab_pat)
        gl.auth()
        username = gl.user.username

    config = {
        "gitlab_pat": gitlab_pat,
        "gitlab_url": gitlab_url,
        "username": username,
        "batch_size": 30,
        "ai_cli": ai_cli,
        "model": model,
        "fallback_ai_cli": fallback_ai_cli,
    }

    config_path = CONFIG_DIR / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    # Restrict file permissions (owner only)
    config_path.chmod(0o600)

    return config


def load_config() -> dict:
    config_path = CONFIG_DIR / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            "Config not found. Run: sensei init"
        )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    config.setdefault("batch_size", 30)
    config.setdefault("ai_cli", "auto")
    config.setdefault("model", "")
    config.setdefault("fallback_ai_cli", True)

    # Allow env var override for PAT
    env_pat = os.environ.get("GITLAB_PAT")
    if env_pat:
        config["gitlab_pat"] = env_pat

    env_ai_cli = os.environ.get("SENSEI_AI_CLI")
    if env_ai_cli:
        config["ai_cli"] = env_ai_cli

    env_model = os.environ.get("SENSEI_MODEL")
    if env_model is not None:
        config["model"] = env_model

    env_fallback = os.environ.get("SENSEI_FALLBACK_AI_CLI")
    if env_fallback is not None:
        config["fallback_ai_cli"] = env_fallback.strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    return config


def save_config(config: dict) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    rules_dir = CONFIG_DIR / "rules"
    rules_dir.mkdir(exist_ok=True)
    rules_dir.chmod(0o700)

    config_path = CONFIG_DIR / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    config_path.chmod(0o600)
    return config_path
