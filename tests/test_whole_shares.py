from app.utils.shares import whole_shares, whole_shares_ceil, clamp_whole_shares


def test_whole_shares_floor():
    assert whole_shares(10.9) == 10
    assert whole_shares(10.0000001) == 10
    assert whole_shares(11) == 11
    assert whole_shares(-3) == 0
    assert whole_shares(None) == 0
    assert whole_shares('12.7') == 12


def test_whole_shares_near_integer():
    assert whole_shares(9.999999999) == 10
    assert whole_shares(10.0) == 10


def test_ceil_and_clamp():
    assert whole_shares_ceil(10.1) == 11
    assert clamp_whole_shares(15.9, 12.2) == 12
