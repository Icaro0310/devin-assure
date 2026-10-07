#!/bin/sh
# Install devin-qa-pack — pipx preferred, pip --user as fallback.
#
#   curl -fsSL https://raw.githubusercontent.com/Icaro0310/devin-qa-pack/main/install.sh | sh
#
# Set DEVIN_QA_PACK_REF to install a specific tag instead of main:
#   DEVIN_QA_PACK_REF=v0.1.0 sh install.sh
set -eu

REF="${DEVIN_QA_PACK_REF:-}"
if [ -n "$REF" ]; then
    PKG="git+https://github.com/Icaro0310/devin-qa-pack.git@$REF"
else
    PKG="devin-qa-pack"
fi

if command -v pipx >/dev/null 2>&1; then
    pipx install "$PKG"
elif command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --user "$PKG"
elif command -v py >/dev/null 2>&1; then
    py -m pip install --user "$PKG"
else
    echo "install.sh: need pipx, python3 or py on PATH" >&2
    exit 1
fi

if command -v devin-qa-pack >/dev/null 2>&1; then
    devin-qa-pack --help >/dev/null
    echo "devin-qa-pack installed."
else
    echo "installed; ensure your pip/pipx bin dir is on PATH" >&2
fi
