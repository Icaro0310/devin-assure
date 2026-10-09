from cart import subtotal, total_with_discount, total_with_tax

ITEMS = [{"price": 10.0, "qty": 2}, {"price": 3.5, "qty": 4}]


def test_subtotal():
    assert subtotal(ITEMS) == 34.0


def test_subtotal_empty():
    assert subtotal([]) == 0.0


def test_discount():
    assert total_with_discount(ITEMS, 0.25) == 25.5


def test_tax():
    assert total_with_tax(ITEMS, 0.25) == 42.5


def test_zero_discount():
    assert total_with_discount(ITEMS, 0.0) == 34.0
