#!/bin/bash -eu
pip3 install atheris pyinstaller
pip3 install -e packages/qa-pack
compile_python_fuzzer packages/qa-pack/fuzz/fuzz_claims.py
