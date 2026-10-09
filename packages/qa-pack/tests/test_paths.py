from pathlib import Path

from devin_qa_pack.paths import default_data_dir, default_sessions_db


def test_linux_defaults_to_xdg_data_home(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "data")}

    root = default_data_dir(environ=env, platform="linux")

    assert root == Path(env["XDG_DATA_HOME"]) / "devin"
    assert default_sessions_db(environ=env, platform="linux") == root / "cli" / "sessions.db"


def test_linux_prefers_existing_legacy_config_store(tmp_path):
    data_home = tmp_path / "data"
    config_home = tmp_path / "config"
    legacy = config_home / "devin"
    (legacy / "cli").mkdir(parents=True)
    (legacy / "cli" / "sessions.db").touch()
    env = {"XDG_DATA_HOME": str(data_home), "XDG_CONFIG_HOME": str(config_home)}

    assert default_data_dir(environ=env, platform="linux") == legacy
