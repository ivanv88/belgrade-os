from config import Config


def test_defaults():
    cfg = Config(_env_file=None)
    assert cfg.vault_path == "/tmp/belgrade-vault"
    assert cfg.redis_url == "redis://localhost:6379"


def test_env_override(monkeypatch):
    monkeypatch.setenv("BEG_OS_VAULT_PATH", "/mnt/storage/obsidian")
    cfg = Config(_env_file=None)
    assert cfg.vault_path == "/mnt/storage/obsidian"


def test_ignores_other_services_keys_in_shared_env_file(tmp_path):
    """All services read the one repo-root .env; keys meant for other services must not fail validation."""
    env = tmp_path / ".env"
    env.write_text("CF_TUNNEL_TOKEN=x\nAPP_REDIS_URL=redis://app@localhost:6379\nMCP_JWT_SECRET=s\n")
    Config(_env_file=env)
