# store conventions

- Read helpers that walk records are named `iter_*` and yield lazily —
  never return a list.
- Helpers take the record iterable as their first argument.
