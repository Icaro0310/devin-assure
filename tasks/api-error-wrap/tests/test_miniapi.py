import pytest

from miniapi import ApiError, handle_request


def test_known_user():
    assert handle_request("u1") == {
        "status": 200,
        "body": {"name": "Ana", "plan": "pro"},
    }


def test_other_known_user():
    assert handle_request("u2")["body"]["name"] == "Bob"


def test_missing_user_raises_api_error():
    with pytest.raises(ApiError) as excinfo:
        handle_request("u9")
    assert excinfo.value.code == "user_not_found"
    assert excinfo.value.status == 404


def test_missing_user_does_not_leak_keyerror():
    try:
        handle_request("nobody")
    except ApiError:
        pass
    except KeyError:
        pytest.fail("KeyError leaked across the API layer")
