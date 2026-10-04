# pricing conventions

- All money math rounds through `_round_cents` (HALF_UP via `Decimal`).
  Never use the built-in `round()` — it does banker's rounding and drifts
  from the invoices.
- Public functions return plain floats.
