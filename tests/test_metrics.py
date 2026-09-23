from app.metrics import alert_triggered, change_since_last_pull, min_max, rolling_average


def test_change_since_last_pull_needs_two_values():
    assert change_since_last_pull([]) is None
    assert change_since_last_pull([10.0]) is None


def test_change_since_last_pull_basic_increase():
    assert change_since_last_pull([20.0, 22.0]) == {"absolute": 2.0, "percent": 10.0}


def test_change_since_last_pull_basic_decrease():
    result = change_since_last_pull([22.0, 20.0])
    assert result["absolute"] == -2.0
    assert result["percent"] == round(-2 / 22 * 100, 2)


def test_change_since_last_pull_only_uses_last_two():
    # a big earlier swing shouldn't leak into the "since last pull" figure
    assert change_since_last_pull([0.0, 100.0, 21.0, 20.0]) == {
        "absolute": -1.0,
        "percent": round(-1.0 / 21 * 100, 2),
    }


def test_change_since_last_pull_negative_baseline_uses_abs_for_percent():
    # previous=-10, latest=-5: absolute change is +5, percent should be +50%, not -50%
    result = change_since_last_pull([-10.0, -5.0])
    assert result["absolute"] == 5.0
    assert result["percent"] == 50.0


def test_change_since_last_pull_zero_baseline_percent_is_none():
    result = change_since_last_pull([0.0, 3.0])
    assert result["absolute"] == 3.0
    assert result["percent"] is None


def test_rolling_average_uses_last_n_values():
    values = [10, 20, 30, 40, 50]
    assert rolling_average(values, 2) == 45.0
    assert rolling_average(values, 3) == 40.0


def test_rolling_average_window_larger_than_history_uses_all():
    assert rolling_average([10, 20], 5) == 15.0


def test_rolling_average_empty_is_none():
    assert rolling_average([], 5) is None


def test_min_max_over_full_history():
    assert min_max([10, 5, 20, 3, 8]) == {"min": 3, "max": 20}


def test_min_max_over_window_ignores_older_values():
    # the min (3) falls outside the last-2 window, so it should not appear
    assert min_max([10, 5, 20, 3, 8], window=2) == {"min": 3, "max": 8}
    assert min_max([10, 5, 20, 3, 8], window=1) == {"min": 8, "max": 8}


def test_min_max_empty_is_none():
    assert min_max([]) is None


def test_alert_triggered_respects_threshold():
    big_change = {"absolute": 6.0, "percent": 30.0}
    small_change = {"absolute": 2.0, "percent": 5.0}
    assert alert_triggered(big_change, threshold_c=5) is True
    assert alert_triggered(small_change, threshold_c=5) is False


def test_alert_triggered_uses_absolute_value_of_change():
    negative_swing = {"absolute": -7.0, "percent": -30.0}
    assert alert_triggered(negative_swing, threshold_c=5) is True


def test_alert_triggered_false_when_no_prior_pull():
    assert alert_triggered(None, threshold_c=5) is False
