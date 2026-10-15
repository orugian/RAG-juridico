"""Fast regression of the load harness budget, not a product latency/SLO test.

Synthetic durations exercise arithmetic only; the affected load test separately
measures three real local Qwen/Chroma HTTP cache misses before injecting a stall.
"""
import pytest

from app.http_contracts import HttpConfig
from tests.test_p8_load import _relative_deadline


def test_deadline_reserves_one_observed_maximum_as_recovery_margin():
    samples = (2.0, 5.0, 10.0)
    deadline = _relative_deadline(samples)
    assert deadline > max(samples), "legacy 8s cannot accommodate a healthy 10s miss"
    assert deadline - max(samples) == pytest.approx(max(samples))
    assert HttpConfig(deadline_seconds=deadline).deadline_seconds == deadline


@pytest.mark.parametrize("samples", [
    (), (1.0,), (1.0, 2.0, 3.0, 4.0),
    (1.0, 0.0), (1.0, -1.0), (1.0, float("nan")),
    (1.0, float("inf")), (1.0, float("-inf")), (1.0, True),
])
def test_calibration_rejects_missing_or_unhealthy_measurements(samples):
    with pytest.raises(ValueError):
        _relative_deadline(samples)


@pytest.mark.parametrize("samples", [
    (0.000001, 0.000002), (0.25, 0.75), (5.0, 2.0, 1.0), (15.0, 14.0),
])
def test_deadline_scales_without_absolute_floor_and_preserves_order_independence(samples):
    deadline = _relative_deadline(samples)
    assert deadline == pytest.approx(2 * max(samples))
    assert _relative_deadline(tuple(reversed(samples))) == deadline
    assert _relative_deadline(tuple(seconds / 2 for seconds in samples)) == pytest.approx(deadline / 2)


@pytest.mark.parametrize("samples", [(15.000001, 1.0), (30.0, 1.0), (1e308, 1.0)])
def test_unrepresentable_margin_fails_instead_of_clamping_or_relaxing_http_limit(samples):
    with pytest.raises(ValueError):
        _relative_deadline(samples)
