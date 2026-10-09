"""ME-2: advisory context guard."""
import json
import sqlite3
from devin_metrics.cli import main


def test_watch_all_clear(data_dir, capsys):
    assert main(["watch", "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "all clear" in out


def test_watch_flags_sessions(data_dir, sessions_db, capsys):
    # inject a real context signal (num_tokens_preceding) into one node
    con = sqlite3.connect(sessions_db)
    row = con.execute(
        "SELECT session_id, node_id FROM message_nodes LIMIT 1").fetchone()
    with con:
        con.execute(
            "UPDATE message_nodes SET metadata = ? "
            "WHERE session_id = ? AND node_id = ?",
            (json.dumps({"num_tokens_preceding": 500_000}), row[0], row[1]))
    con.close()
    rc = main(["watch", "--data-dir", str(data_dir),
               "--session-warn", "400000", "--fail"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "finding(s)" in out and "500,000" in out


def test_watch_json_shape(data_dir, capsys):
    main(["watch", "--data-dir", str(data_dir), "--json"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["advisory"] is True
    assert "cost" in rep["note"]  # honest about the ME-3 finding
