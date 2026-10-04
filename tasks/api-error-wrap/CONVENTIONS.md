# miniapi conventions

- Every failure that crosses the API layer is an `ApiError` — never let
  builtin exceptions (`KeyError`, `ValueError`, ...) leak to callers.
- Error codes are `snake_case` strings; pick the closest HTTP status.
