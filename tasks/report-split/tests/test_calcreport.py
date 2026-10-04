from calcreport import render_report


def test_report_lines():
    lines = render_report([10.0, 20.0, 30.0]).splitlines()
    assert len(lines) == 5
    assert "total" in lines[0] and "$60.00" in lines[0]
    assert "average" in lines[1] and "$20.00" in lines[1]
    assert "top" in lines[2] and "$30.00" in lines[2]
    assert "top share" in lines[3] and "50.0%" in lines[3]
    assert "count" in lines[4] and "3" in lines[4]


def test_empty_report():
    out = render_report([])
    assert "$0.00" in out
    assert "0.0%" in out


def test_single_sale():
    lines = render_report([42.5]).splitlines()
    assert "$42.50" in lines[0]
    assert "100.0%" in lines[3]
