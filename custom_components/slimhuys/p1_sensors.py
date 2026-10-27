"""P1-sensorherkenning en tariefparen — los van Home Assistant importeerbaar.

Staat apart van `config_flow.py` zodat de logica zonder HA-installatie te
testen is (`tests/test_p1_sensors.py`). Hier zit alleen naam-logica op
entity-id's; geen `hass`, geen I/O.

Waarom tariefparen: niet elke meter heeft een totaalstand. ESPHome-DSMR
(SlimmeLezer e.d.) publiceert alleen `energy_consumed_tariff_1` en `_2`, en
SlimHuys wil één cumulatieve stand. Zonder optellen moest de gebruiker eerst
zelf een template-sensor bouwen — en koos anders tarief 1 als "totaal", met
een stand die 's nachts stilstaat (gemeld 27-10-2026).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# `_tariff_1`, `tariff1`, `_tarif_2` (HA-DSMR), `_tarief_1`. Alleen 1 en 2:
# NL-meters hebben er nooit meer.
_TARIFF_RE = re.compile(r"(tariff|tarif|tarief)_?([12])(?![0-9])")

_CONSUMPTION_PATTERNS = (
    "consumption_total", "energy_import", "_import_total", "imported_energy",
    "stroom_verbruik_totaal", "verbruik_totaal", "_consumption", "_import",
    # ESPHome-DSMR: "consumed" is afname, en "delivered" óók — in de
    # DSMR-spec is dat geleverd áán de klant. Niet verwarren met `_delivery`.
    "energy_consumed", "energy_delivered",
)
_DELIVERY_PATTERNS = (
    "delivery_total", "energy_export", "_export_total", "exported_energy",
    "_teruglevering_totaal", "teruglevering_totaal", "_delivery", "_export",
    "energy_returned",
)
# "energy_produced"/"energy_production" noemen veel omvormers hun opwek. Alleen met
# een tarief erachter is het zeker de meter.
_DELIVERY_TARIFF_ONLY_PATTERNS = ("energy_produced", "energy_production")
_POWER_PATTERNS = (
    "current_electricity_usage", "active_power", "current_power",
    "vermogen_nu", "current_consumption_w", "power_consumed",
)


def tariff_of(entity_id: str) -> int | None:
    """1 of 2 als de entity één tariefregister is, anders None (= totaal)."""
    m = _TARIFF_RE.search(entity_id.lower())
    return int(m.group(2)) if m else None


def tariff_partner(entity_id: str, available: Iterable[str]) -> str | None:
    """De tarief-2-tegenhanger van een tarief-1-sensor, als die bestaat."""
    if tariff_of(entity_id) != 1:
        return None
    m = _TARIFF_RE.search(entity_id.lower())
    assert m is not None
    start, end = m.span(2)
    candidate = entity_id[:start] + "2" + entity_id[end:]
    return candidate if candidate in set(available) else None


def detect_dsmr_sensors(entity_ids: Iterable[str]) -> dict[str, list[str]]:
    """Naam-patroon-suggesties voor verbruik, teruglevering en vermogen.

    Binnen elke lijst staan totaalstanden vóór tariefregisters: HomeWizard
    publiceert `energy_import` naast `energy_import_tariff_1`, en de volgorde
    van `hass.states` is willekeurig.
    """
    consumption: list[str] = []
    delivery: list[str] = []
    power: list[str] = []

    for entity_id in entity_ids:
        eid = entity_id.lower()
        if any(p in eid for p in _CONSUMPTION_PATTERNS):
            consumption.append(entity_id)
        elif any(p in eid for p in _DELIVERY_PATTERNS) or (
            tariff_of(eid) is not None and any(p in eid for p in _DELIVERY_TARIFF_ONLY_PATTERNS)
        ):
            delivery.append(entity_id)
        elif any(p in eid for p in _POWER_PATTERNS):
            power.append(entity_id)

    def _totals_first(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda e: tariff_of(e) is not None)

    return {
        "consumption": _totals_first(consumption),
        "delivery": _totals_first(delivery),
        "power": power,
    }


def pick_register(candidates: list[str], available: Iterable[str]) -> tuple[str | None, str | None]:
    """Kies (hoofdsensor, tarief-2-sensor) uit de kandidaten.

    Een totaalstand wint; dan is er geen tweede sensor nodig. Is er alleen
    een tarief-1-register, dan gaat de tarief-2-tegenhanger ernaast. Een los
    tarief-2-register wordt nooit als hoofdsensor gekozen.
    """
    available = list(available)
    usable = [c for c in candidates if c in available]
    total = next((c for c in usable if tariff_of(c) is None), None)
    if total is not None:
        return total, None
    t1 = next((c for c in usable if tariff_of(c) == 1), None)
    if t1 is not None:
        return t1, tariff_partner(t1, available)
    return None, None


def sum_registers(values: list[float | None]) -> float | None:
    """Tel tariefregisters op; None als er één onleesbaar is.

    Half optellen zou de totaalstand een tarief omlaag laten springen zodra
    één sensor even `unavailable` is — de server ziet dat als teller-reset.
    """
    if any(v is None for v in values):
        return None
    return round(sum(values), 3)  # type: ignore[arg-type]
