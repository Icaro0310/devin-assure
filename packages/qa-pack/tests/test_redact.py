"""report --redact: output-boundary secret masking (devin-redact engine).

The flag masks secret-shaped spans in the *serialized* HTML report just
before it is written. The contract is fail-closed: if the redaction
engine raises, the unredacted report is never written.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import add_message, add_session
from devin_qa_pack.cli import main
from devin_qa_pack.redact import RedactError, redact_output

# Canonical fake AWS access key — flagged by devin-redact's api_key
# pattern, harmless to publish (it is AWS's own documentation example).
SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def secret_db(sessions_db, tmp_path):
    """The standard fixture plus a session whose title leaks a secret."""
    con = sqlite3.connect(sessions_db)
    with con:
        add_session(con, "sess-secret", str(tmp_path / "nope"),
                    f"deploy notes for {SECRET}")
        add_message(con, "sess-secret", 1, "agent",
                    f"I copied {SECRET} into the deploy notes.")
    con.close()
    return sessions_db


# -- redact_output (library boundary) ------------------------------------------


def test_redact_output_masks_and_counts():
    redacted, n = redact_output(f"the key {SECRET} leaked")
    assert SECRET not in redacted
    assert "<REDACTED:" in redacted
    assert n == 1


def test_redact_output_survives_plain_text():
    text = "ordinary report text with paths src/x.py and numbers 123"
    redacted, n = redact_output(text)
    assert redacted == text
    assert n == 0


def test_redact_output_fails_closed(monkeypatch):
    def boom(_text):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("devin_qa_pack.redact.redact_text", boom)
    with pytest.raises(RedactError):
        redact_output(f"key {SECRET}")


# -- report --redact (cli) ------------------------------------------------------


def test_report_without_redact_keeps_raw_secret(secret_db, tmp_path, capsys):
    out = tmp_path / "raw.html"
    rc = main(["report", "--sessions-db", str(secret_db), "--out", str(out)])
    capsys.readouterr()
    assert rc == 1  # mixed verdicts — the file is still written
    html = out.read_text(encoding="utf-8")
    assert SECRET in html


def test_report_redact_masks_secret(secret_db, tmp_path, capsys):
    out = tmp_path / "redacted.html"
    rc = main(["report", "--sessions-db", str(secret_db), "--out", str(out),
               "--redact"])
    stdout = capsys.readouterr().out
    assert rc == 1
    html = out.read_text(encoding="utf-8")
    assert SECRET not in html
    assert "<REDACTED:" in html
    # non-secret content survives the masking pass
    assert "deploy notes for" in html
    assert "sess-secret" in html
    assert "redacted" in stdout


def test_report_redact_is_deterministic(secret_db, tmp_path, capsys):
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    main(["report", "--sessions-db", str(secret_db), "--out", str(a),
          "--redact"])
    main(["report", "--sessions-db", str(secret_db), "--out", str(b),
          "--redact"])
    capsys.readouterr()
    assert a.read_bytes() == b.read_bytes()


def test_report_redact_failure_writes_nothing(secret_db, tmp_path, capsys,
                                              monkeypatch):
    """Fail closed: an engine error must not emit the unredacted file."""

    def boom(_text):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("devin_qa_pack.redact.redact_text", boom)
    out = tmp_path / "never.html"
    rc = main(["report", "--sessions-db", str(secret_db), "--out", str(out),
               "--redact"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not written" in captured.err
    assert not out.exists()
