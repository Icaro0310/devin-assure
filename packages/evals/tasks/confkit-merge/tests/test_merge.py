import confkit


def test_merge_flat():
    merged = confkit.merge_settings({"a": 1}, {"b": 2})
    assert merged == {"a": 1, "b": 2}


def test_override_wins():
    assert confkit.merge_settings({"a": 1}, {"a": 2}) == {"a": 2}


def test_nested_dicts_merge_recursively():
    base = {"db": {"host": "x", "port": 1}}
    over = {"db": {"port": 2}}
    merged = confkit.merge_settings(base, over)
    assert merged == {"db": {"host": "x", "port": 2}}


def test_override_replaces_non_dict():
    base = {"db": {"host": "x"}}
    over = {"db": "sqlite:///tmp.db"}
    assert confkit.merge_settings(base, over) == {"db": "sqlite:///tmp.db"}


def test_inputs_not_mutated():
    base = {"db": {"host": "x"}}
    over = {"db": {"port": 2}}
    confkit.merge_settings(base, over)
    assert base == {"db": {"host": "x"}}
    assert over == {"db": {"port": 2}}
