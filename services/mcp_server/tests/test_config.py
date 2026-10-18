from config import Config


def test_defaults():
    cfg = Config(_env_file=None)
    assert cfg.bridge_url == "http://localhost:8081"
    assert cfg.port == 8083


def test_required_fields_default_empty():
    cfg = Config(_env_file=None)
    assert cfg.mcp_jwt_secret == ""
    assert cfg.cf_team_domain == ""
    assert cfg.cf_mcp_audience == ""


def test_ignores_other_services_keys_in_shared_env_file(tmp_path):
    """All services read the one repo-root .env; keys meant for other services must not fail validation."""
    env = tmp_path / ".env"
    env.write_text("CF_TUNNEL_TOKEN=x\nAPP_REDIS_URL=redis://app@localhost:6379\nMCP_JWT_SECRET=s\n")
    Config(_env_file=env)
