Add `iter_active_users(users)` — a lazy generator named per the module's
`iter_*` convention. A `def active_users(...)` returning a list fails both
the naming (tests call `store.iter_active_users`) and laziness checks.
