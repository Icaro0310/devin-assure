# confkit conventions

- Public API is re-exported from `confkit/__init__.py` and listed in
  `__all__`; users only ever `import confkit`, never submodule names.
- New public functions live in `confkit/settings.py`.
