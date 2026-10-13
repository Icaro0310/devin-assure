def test_import():
    import devin_metrics

    assert devin_metrics.__version__


def test_version_matches_package_distribution() -> None:
    from importlib.metadata import version

    import devin_metrics

    assert devin_metrics.__version__ == version("devin-metrics")
