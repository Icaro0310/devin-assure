from pricing import line_price, total_with_tax, unit_price


def test_unit_price_rounds_half_up():
    assert unit_price(9.995) == 10.0


def test_line_price():
    assert line_price(3.33, 3) == 9.99


def test_total_no_tax():
    assert total_with_tax(10.0, 0.0) == 10.0


def test_total_half_cent_rounds_up():
    assert total_with_tax(0.125, 0.0) == 0.13


def test_total_with_tax():
    assert total_with_tax(10.0, 0.075) == 10.75


def test_total_float_edge():
    assert total_with_tax(2.005, 0.0) == 2.01
