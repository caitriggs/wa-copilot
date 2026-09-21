import pytest

from copilot import config, paths


def test_load_merges_defaults(user_env):
    cfg = user_env
    assert cfg.targets_per_week == 3
    assert cfg.get("ranking.title_similarity") == 2.0     # from DEFAULTS
    assert "usajobs" in cfg.enabled_sources()
    assert "feeds" not in cfg.enabled_sources()           # disabled in the fixture config


def test_missing_titles_raises(data_root):
    paths.ensure_scaffold("u2", data_root)
    paths.config_path("u2", data_root).write_text("user: u2\nsearch:\n  titles: []\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load("u2", data_root=data_root)


def test_comp_min_must_be_numeric_raises(data_root):
    paths.ensure_scaffold("u3", data_root)
    paths.config_path("u3", data_root).write_text(
        "user: u3\nsearch:\n  titles: ['x']\n  comp_min: 'lots'\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load("u3", data_root=data_root)


def test_secret_falls_back_to_secrets_env(monkeypatch, data_root):
    # Force the keyring path off so we deterministically test the secrets.env fallback.
    monkeypatch.setattr(config, "keyring", None)
    paths.ensure_scaffold("u4", data_root)
    paths.secrets_env_path("u4", data_root).write_text('USAJOBS="abc123"\nSMTP_PASS=pw\n', encoding="utf-8")
    assert config.get_secret("u4", "usajobs", data_root=data_root) == "abc123"
    assert config.get_secret("u4", "smtp_pass", data_root=data_root) == "pw"
    assert config.get_secret("u4", "absent", data_root=data_root) is None


def test_set_secret_writes_secrets_env_when_no_keyring(monkeypatch, data_root):
    monkeypatch.setattr(config, "keyring", None)
    paths.ensure_scaffold("u5", data_root)
    backend = config.set_secret("u5", "smtp_user", "me@example.com", data_root=data_root)
    assert backend == "secrets.env"
    assert config.get_secret("u5", "smtp_user", data_root=data_root) == "me@example.com"


def test_config_repr_hides_details(user_env):
    assert "password" not in repr(user_env).lower()


def test_weekly_notes_override_file_beats_config(data_root):
    paths.ensure_scaffold("u6", data_root)
    paths.config_path("u6", data_root).write_text(
        "user: u6\nsearch:\n  titles: ['x']\n  weekly_notes: 'from config'\n", encoding="utf-8")
    cfg = config.load("u6", data_root=data_root)
    assert cfg.weekly_notes == "from config"              # no override file yet -> config value

    paths.weekly_notes_path("u6", data_root).write_text("  steer remote analytics  \n", encoding="utf-8")
    assert cfg.weekly_notes == "steer remote analytics"   # file overrides config, stripped


def test_weekly_notes_empty_override_file_is_authoritative(data_root):
    paths.ensure_scaffold("u7", data_root)
    paths.config_path("u7", data_root).write_text(
        "user: u7\nsearch:\n  titles: ['x']\n  weekly_notes: 'from config'\n", encoding="utf-8")
    cfg = config.load("u7", data_root=data_root)
    # An empty steering file means "no focus this week" and still overrides the config note.
    paths.weekly_notes_path("u7", data_root).write_text("   \n", encoding="utf-8")
    assert cfg.weekly_notes == ""
