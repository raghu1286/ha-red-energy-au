"""Data models used by the Red Energy integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(slots=True)
class DailyUsageEntry:
    """Represent a single day of usage returned by the API."""

    day: date
    consumption_kwh: float
    generation_kwh: float
    consumption_cost: float
    generation_value: float


@dataclass(slots=True)
class EnergyPeriod:
    """Usage totals for a period."""

    start: date
    end: date
    consumption_kwh: float
    generation_kwh: float
    consumption_cost: float
    generation_value: float


@dataclass(slots=True)
class EnergyBreakdown:
    """All period buckets for a property."""

    daily: EnergyPeriod
    weekly: EnergyPeriod
    monthly: EnergyPeriod


@dataclass(slots=True)
class HourlyUsage:
    """Latest hourly usage summary."""

    start: datetime
    end: datetime
    consumption_kwh: float
    generation_kwh: float
    consumption_cost: float
    generation_value: float


@dataclass(slots=True)
class PropertyEnergyData:
    """Energy overview for a property."""

    property_id: str
    name: str
    breakdown: EnergyBreakdown
    last_updated: datetime
    consumer_number: str
    latest_hour: HourlyUsage | None


@dataclass(slots=True)
class SelectedProperty:
    """Store the user-selected property configuration."""

    property_id: str
    consumer_number: str
    name: str
