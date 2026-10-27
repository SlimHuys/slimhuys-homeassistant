"""Tests voor de P1-sensorherkenning en tariefparen.

`p1_sensors.py` importeert niets van Home Assistant; we laden 'm via het pad
zodat het package-`__init__` (dat wél HA nodig heeft) niet meedraait.
Draaien: `python3 -m pytest tests/`.
"""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).parent.parent / "custom_components" / "slimhuys" / "p1_sensors.py"
_spec = importlib.util.spec_from_file_location("p1_sensors", _PATH)
p1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p1)

# Zoals een SlimmeLezer/ESPHome-DSMR ze in HA zet: geen totaalstand.
ESPHOME = [
    "sensor.p1_meter_energy_consumed_tariff_1",
    "sensor.p1_meter_energy_consumed_tariff_2",
    "sensor.p1_meter_energy_produced_tariff_1",
    "sensor.p1_meter_energy_produced_tariff_2",
    "sensor.p1_meter_power_consumed",
    "sensor.p1_meter_power_produced",
]

# HomeWizard: totaalstand náást de tariefregisters.
HOMEWIZARD = [
    "sensor.p1_meter_energy_import_tariff_1",
    "sensor.p1_meter_energy_import",
    "sensor.p1_meter_energy_import_tariff_2",
    "sensor.p1_meter_energy_export_tariff_1",
    "sensor.p1_meter_energy_export",
    "sensor.p1_meter_power",
]


def test_tariff_of_recognises_common_spellings():
    assert p1.tariff_of("sensor.x_energy_consumed_tariff_1") == 1
    assert p1.tariff_of("sensor.x_energy_delivered_tariff2") == 2
    assert p1.tariff_of("sensor.electricity_meter_energy_consumption_tarif_1") == 1
    assert p1.tariff_of("sensor.verbruik_tarief_2") == 2
    assert p1.tariff_of("sensor.p1_meter_energy_import") is None
    # Geen tarief 12 of 10 als 1 lezen.
    assert p1.tariff_of("sensor.x_tariff_12") is None


def test_esphome_meter_gets_tariff_pair_for_consumption_and_delivery():
    s = p1.detect_dsmr_sensors(ESPHOME)
    assert p1.pick_register(s["consumption"], ESPHOME) == (
        "sensor.p1_meter_energy_consumed_tariff_1",
        "sensor.p1_meter_energy_consumed_tariff_2",
    )
    assert p1.pick_register(s["delivery"], ESPHOME) == (
        "sensor.p1_meter_energy_produced_tariff_1",
        "sensor.p1_meter_energy_produced_tariff_2",
    )
    assert s["power"][0] == "sensor.p1_meter_power_consumed"


def test_esphome_delivered_means_consumption():
    # In de DSMR-spec is 'delivered' geleverd áán de klant: afname.
    ids = ["sensor.dsmr_energy_delivered_tariff1", "sensor.dsmr_energy_returned_tariff1"]
    s = p1.detect_dsmr_sensors(ids)
    assert s["consumption"] == ["sensor.dsmr_energy_delivered_tariff1"]
    assert s["delivery"] == ["sensor.dsmr_energy_returned_tariff1"]


def test_total_wins_over_tariff_registers_regardless_of_order():
    s = p1.detect_dsmr_sensors(HOMEWIZARD)
    assert p1.pick_register(s["consumption"], HOMEWIZARD) == ("sensor.p1_meter_energy_import", None)
    assert p1.pick_register(s["delivery"], HOMEWIZARD) == ("sensor.p1_meter_energy_export", None)


def test_inverter_energy_produced_is_not_taken_as_meter_delivery():
    s = p1.detect_dsmr_sensors(["sensor.goodwe_energy_produced", "sensor.goodwe_total_energy"])
    assert s["delivery"] == []


def test_lone_tariff_2_is_never_the_main_sensor():
    ids = ["sensor.x_energy_consumed_tariff_2"]
    assert p1.pick_register(ids, ids) == (None, None)


def test_tariff_1_without_partner_has_no_second_sensor():
    ids = ["sensor.x_energy_consumed_tariff_1"]
    assert p1.pick_register(ids, ids) == ("sensor.x_energy_consumed_tariff_1", None)


def test_sum_registers():
    assert p1.sum_registers([1234.5, 987.25]) == 2221.75
    assert p1.sum_registers([1234.5]) == 1234.5
    # Eén register even unavailable: geen halve stand pushen.
    assert p1.sum_registers([1234.5, None]) is None
