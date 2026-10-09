"""ClusterFuzzLite fuzz target: claim extraction on arbitrary transcript text."""

import sys

import atheris

with atheris.instrument_imports():
    from devin_qa_pack.claims import (
        _claims_in_line,
        _decode_message,
        claims_from_pairs,
    )


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    line = fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 2048))
    list(_claims_in_line(line))
    _decode_message(line)
    claims_from_pairs([("user", line), ("assistant", line)], "fuzz")


atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
