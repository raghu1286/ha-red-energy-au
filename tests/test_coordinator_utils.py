from datetime import date

from custom_components.red_energy.coordinator import _build_breakdown
from custom_components.red_energy.models import DailyUsageEntry


def test_build_breakdown_handles_empty():
    breakdown = _build_breakdown([])
    assert breakdown.daily.consumption_kwh == 0.0
    assert breakdown.weekly.generation_kwh == 0.0


def test_build_breakdown_aggregates_periods():
    entries = [
        DailyUsageEntry(day=date(2024, 5, 1), consumption_kwh=1.0, generation_kwh=0.5),
        DailyUsageEntry(day=date(2024, 5, 2), consumption_kwh=2.0, generation_kwh=0.2),
        DailyUsageEntry(day=date(2024, 5, 3), consumption_kwh=3.0, generation_kwh=0.3),
    ]

    breakdown = _build_breakdown(entries)
    assert breakdown.daily.consumption_kwh == 3.0
    assert breakdown.weekly.consumption_kwh == 6.0
    assert breakdown.monthly.generation_kwh == 1.0
