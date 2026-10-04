import inspect

import store


def test_returns_lazy_iterator():
    result = store.iter_active_users(store.USERS)
    assert inspect.isgenerator(result)


def test_filters_inactive_users():
    names = [u["name"] for u in store.iter_active_users(store.USERS)]
    assert names == ["Ana", "Cid"]


def test_works_on_any_iterable():
    assert len(list(store.iter_active_users(iter(store.USERS)))) == 2


def test_all_inactive_yields_nothing():
    users = [{"id": "u9", "name": "Zed", "active": False}]
    assert list(store.iter_active_users(users)) == []
