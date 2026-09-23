"""Derived metrics computed from a plain list of temperature values, ordered oldest -> newest.

Kept independent of the database and HTTP layer on purpose: these are the easiest
place to introduce a subtle bug (percent-change sign, off-by-one windows, div-by-zero
near 0C), so they're unit tested directly on plain floats.
"""


def change_since_last_pull(values):
    """Absolute and percent change between the last two pulls, or None if <2 pulls exist."""
    if len(values) < 2:
        return None

    previous, latest = values[-2], values[-1]
    absolute = round(latest - previous, 2)

    # Percent change is undefined (and not meaningful) when the baseline is 0 or
    # crosses 0 -- common for Celsius temperatures -- so we return None rather than
    # a misleadingly huge or infinite percentage.
    percent = None
    if previous != 0:
        percent = round((latest - previous) / abs(previous) * 100, 2)

    return {"absolute": absolute, "percent": percent}


def rolling_average(values, window):
    """Mean of the last `window` values. Uses all available values if fewer than window."""
    if not values:
        return None
    subset = values[-window:] if window else values
    return round(sum(subset) / len(subset), 2)


def min_max(values, window=None):
    """Min/max over the last `window` values, or the full history if window is None."""
    if not values:
        return None
    subset = values[-window:] if window else values
    return {"min": round(min(subset), 2), "max": round(max(subset), 2)}


def alert_triggered(change, threshold_c):
    """Flag a large swing since the last pull, using absolute degrees rather than
    percent -- percent change is unstable/meaningless near 0C for temperature."""
    if not change:
        return False
    return abs(change["absolute"]) >= threshold_c
