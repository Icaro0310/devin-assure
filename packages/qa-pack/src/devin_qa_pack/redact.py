"""Output-boundary redaction for reports — powered by ``devin-redact``.

``devin-qa-pack report`` serializes the HTML report first; this module
then masks every secret-shaped span in the *serialized* text before it
reaches disk or stdout. The engine is ``devin_redact.engine.redact_text``
— the same upstream package ``devin-evals`` already depends on for its
pattern set — so the ecosystem has exactly one redaction
implementation.

Fail-closed by contract: :func:`redact_output` either returns fully
redacted text or raises :class:`RedactError`. A caller that catches the
error must treat the original text as unsafe and never emit it.
"""

from __future__ import annotations

from devin_redact.engine import redact_text


class RedactError(RuntimeError):
    """Redaction failed — the unredacted output must not be emitted."""


def redact_output(text: str) -> tuple[str, int]:
    """Mask every secret-shaped span in serialized output ``text``.

    Returns ``(redacted_text, edit_count)``. Secrets become
    ``<REDACTED:fingerprint>`` placeholders — deterministic for identical
    input, so the report's byte-stability guarantee survives.

    Raises :class:`RedactError` on any engine failure. The wrapper
    exists so this boundary has one documented, fail-closed entry point
    instead of a raw third-party call scattered through the CLI.
    """
    try:
        redacted, edits = redact_text(text)
    except Exception as exc:
        raise RedactError(f"redaction failed: {exc}") from exc
    return redacted, len(edits)
