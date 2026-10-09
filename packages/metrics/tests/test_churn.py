"""ME-4: churn report over a synthetic graph.db."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from devin_metrics.churn import churn_report, render_churn


def _graph(path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, key TEXT,
                        owner TEXT, attrs TEXT DEFAULT '{}');
    CREATE TABLE edges (kind TEXT, src TEXT, dst TEXT, owner TEXT,
                        attrs TEXT DEFAULT '{}',
                        PRIMARY KEY (kind, src, dst));
    """)
    con.execute("INSERT INTO nodes VALUES ('session:s1','session','s1',NULL,"
                "'{\"model\": \"m-a\"}')")
    con.execute("INSERT INTO nodes VALUES ('session:s2','session','s2',NULL,"
                "'{\"model\": \"m-b\"}')")
    con.execute("INSERT INTO nodes VALUES ('file:/x.py','file','/x.py',NULL,'{}')")
    con.execute("INSERT INTO nodes VALUES ('file:/y.py','file','/y.py',NULL,'{}')")
    # s1 touches /x.py via 3 calls, /y.py once; s2 touches /y.py once
    for i in range(3):
        con.execute("INSERT INTO edges VALUES ('file_touched',?, 'file:/x.py',"
                    "'s1','{}')", (f"tool_call:s1:t{i}",))
    con.execute("INSERT INTO edges VALUES ('file_touched','tool_call:s1:t9',"
                "'file:/y.py','s1','{}')")
    con.execute("INSERT INTO edges VALUES ('file_touched','tool_call:s2:t1',"
                "'file:/y.py','s2','{}')")
    con.commit(); con.close()


def test_churn_report(tmp_path):
    db = tmp_path / "g.db"
    _graph(db)
    r = churn_report(db)
    assert r["sessions_with_churn"] == 1
    s1 = r["sessions"][0]
    assert (s1["session_id"], s1["model"], s1["reworked_files"],
            s1["repeat_calls"], s1["files_touched"]) == ("s1", "m-a", 1, 2, 2)
    assert r["models"][0]["model"] == "m-a"
    assert r["top_files"][0]["file"] == "/x.py"
    assert "s2" not in {s["session_id"] for s in r["sessions"]}


def test_missing_graph_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        churn_report(tmp_path / "nope.db")


def test_render(tmp_path):
    db = tmp_path / "g.db"; _graph(db)
    out = render_churn(churn_report(db))
    assert "m-a" in out and "x.py" in out


def test_foreign_schema_errors(tmp_path):
    db = tmp_path / "other.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT);"
        "CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT);")
    con.commit(); con.close()
    with pytest.raises(ValueError, match="devin-graph"):
        churn_report(db)
