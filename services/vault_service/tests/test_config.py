from config import Config


def test_defaults():
    cfg = Config(_env_file=None)
    assert cfg.vault_path == "/tmp/belgrade-vault"
    assert cfg.redis_url == "redis://localhost:6379"


def test_env_override(monkeypatch):
    monkeypatch.setenv("BEG_OS_VAULT_PATH", "/mnt/storage/obsidian")
    cfg = Config(_env_file=None)
    assert cfg.vault_path == "/mnt/storage/obsidian"
