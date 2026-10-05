# devin-qa-pack — Windows guide

This guide covers Windows setup only. See [README.md](README.md) for features, shared commands, limitations, and the safety model.

## Prerequisites

- `uv` and Python 3.10 or newer; `uv` can manage Python.

## Install

Install the isolated Python CLI:

```powershell
uv tool install "https://github.com/Icaro0310/devin-qa-pack/archive/cfc5960aa625faf32eae3b52d07e3ab19124d55c.tar.gz"
```

## Devin paths

Session data normally lives under `%APPDATA%\devin\cli\`; UI state and ACP stores under `%APPDATA%\Devin\User\`.
Use the tool's documented `--data-dir` or `--config-dir` flags for non-default locations.

## Platform notes

- Windows and Linux are the initial tested platforms.
- macOS is planned but not claimed as tested.

## Troubleshooting

- If a command is not found, reopen PowerShell and run `uv tool update-shell`.
