from texttools import collapse_spaces, snake_to_camel


def test_collapse_spaces():
    assert collapse_spaces("a  b\t c") == "a b c"


def test_single_word():
    assert snake_to_camel("foo") == "foo"


def test_two_words():
    assert snake_to_camel("foo_bar") == "fooBar"


def test_three_words():
    assert snake_to_camel("foo_bar_baz") == "fooBarBaz"


def test_upper_word_gets_capitalized():
    assert snake_to_camel("foo_BAR") == "fooBar"
