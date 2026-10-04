Move `_money`, `_pct` and `_row` verbatim into a new `_fmt.py` and add
`from _fmt import _money, _pct, _row` at the top of calcreport.py. The
behavior of `render_report` is unchanged; only the layout changes.
