from datetime import date

from custom_components.red_energy.api import _normalise_daily_payload


def test_normalise_daily_payload_half_hours():
    payload = [
        {
            "usageDate": "2024-05-01",
            "halfHours": [
                {"consumptionKwh": 1.0, "generationKwh": 0.5},
                {"consumptionKwh": 0.5, "generationKwh": 0.2},
            ],
        },
        {
            "usageDate": "2024-05-02",
            "halfHours": [],
        },
    ]

    entries = _normalise_daily_payload(payload)
    assert len(entries) == 2
    assert entries[0].day == date(2024, 5, 1)
    assert entries[0].consumption_kwh == 1.5
    assert entries[0].generation_kwh == 0.7


def test_normalise_daily_payload_fallback_keys():
    payload = [
        {"date": "2024-05-03", "consumptionKwh": 4, "generation": 1.5},
    ]
    entries = _normalise_daily_payload(payload)
    assert entries[0].day == date(2024, 5, 3)
    assert entries[0].consumption_kwh == 4
    assert entries[0].generation_kwh == 1.5
