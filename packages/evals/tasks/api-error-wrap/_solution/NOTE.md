Wrap the missing-user lookup so it raises `ApiError("user_not_found",
status=404)` instead of leaking `KeyError`, per the module's ApiError
convention.
