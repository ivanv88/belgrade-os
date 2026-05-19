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
