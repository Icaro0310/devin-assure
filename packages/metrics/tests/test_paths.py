from pathlib import Path

from devin_metrics.paths import (
    default_acp_messages_dir,
    default_data_dir,
    sessions_db_path,
)


def test_linux_defaults_split_data_and_config_stores(tmp_path):
    data_home = tmp_path / "data"
    config_home = tmp_path / "config"
    acp_dir = config_home / "Devin" / "User" / "acp-messages"
    acp_dir.mkdir(parents=True)
    env = {"XDG_DATA_HOME": str(data_home), "XDG_CONFIG_HOME": str(config_home)}

    data_dir = default_data_dir(environ=env, platform="linux")

    assert data_dir == data_home / "devin"
    assert sessions_db_path(data_dir) == data_dir / "cli" / "sessions.db"
    assert default_acp_messages_dir(environ=env, platform="linux") == acp_dir


def test_explicit_data_root_keeps_colocated_fixture_layout(tmp_path):
    root = tmp_path / "fixture-devin"
    acp_dir = root / "User" / "acp-messages"
    acp_dir.mkdir(parents=True)

    assert default_acp_messages_dir(data_dir=root) == acp_dir
