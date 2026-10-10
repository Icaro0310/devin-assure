def test_import():
    import devin_metrics.dashboard

    assert devin_metrics.dashboard.__version__


def test_dashboard_version_matches_package():
    import devin_metrics
    import devin_metrics.dashboard

    assert devin_metrics.dashboard.__version__ == devin_metrics.__version__
