import pytest

from mathtools import mean, median


def test_mean():
    assert mean([2.0, 4.0]) == 3.0


def test_mean_empty():
    with pytest.raises(ValueError):
        mean([])


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_median_single():
    assert median([7]) == 7


def test_median_empty():
    with pytest.raises(ValueError):
        median([])
