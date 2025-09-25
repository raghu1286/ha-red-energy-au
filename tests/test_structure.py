from pathlib import Path

BASE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "red_energy"


def test_expected_files_exist():
    expected = {
        "__init__.py",
        "api.py",
        "config_flow.py",
        "const.py",
        "coordinator.py",
        "diagnostics.py",
        "manifest.json",
        "models.py",
        "sensor.py",
    }
    assert expected.issubset({path.name for path in BASE_PATH.iterdir()})
