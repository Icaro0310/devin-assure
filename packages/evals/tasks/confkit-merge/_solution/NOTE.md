Add `merge_settings` to `confkit/settings.py` AND re-export it from
`confkit/__init__.py` (both the import and `__all__`) — the package
convention is that public API is only reachable via `import confkit`.
A fix that only edits settings.py still fails the tests.
