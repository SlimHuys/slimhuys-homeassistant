"""SlimHuys binary sensors: negatieve-prijs-triggers voor automatiseringen.

Twee varianten, met bewust verschillende doelen:

* **Negatieve prijs nu** — kale EPEX (`epex_eur_per_kwh`). Duikt regelmatig
  onder nul; dát is het moment waarop terugleveren geld kost en je
  zonnepanelen wilt dimmen/uitzetten.
* **Negatieve all-in prijs nu** — totaalprijs (`total_eur_per_kwh`), dus
  inclusief energiebelasting, opslag en btw. Gaat in NL zelden aan, maar als
  het gebeurt krijg je betaald om te verbruiken — laadpaal, boiler en
  warmtepomp mogen dan vol open.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    NEGATIVE_ALL_IN_PRICE_THRESHOLD,
    NEGATIVE_PRICE_THRESHOLD,
)
from .coordinator import SlimHuysCoordinator, slot_index_now


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    state = hass.data[DOMAIN][entry.entry_id]
    coordinator: SlimHuysCoordinator = state["coordinator"]
    supplier = state["supplier"]
    async_add_entities(
        [
            NegativePriceBinarySensor(coordinator, entry, supplier),
            NegativeAllInPriceBinarySensor(coordinator, entry, supplier),
        ]
    )


def _negative_run_end(
    slots: list[dict[str, Any]], key: str, threshold: float
) -> str | None:
    """Start-ts van het eerste slot (vanaf nu) waarop de prijs weer >= drempel is.

    Dus: tot wanneer de huidige negatieve reeks duurt. `None` als de data niet
    ver genoeg reikt om het einde te zien. Loopt op de resolutie van de
    leverancier, dus bij een kwartier-leverancier op kwartierprecisie.
    """
    idx = slot_index_now(slots)
    if idx is None:
        return None
    for s in slots[idx:]:
        price = s.get(key)
        if price is None or price >= threshold:
            return s.get("start_ts")
    return None


def _next_negative_start(
    slots: list[dict[str, Any]], key: str, threshold: float
) -> str | None:
    """Start-ts van de eerstvolgende negatieve periode (vanaf nu)."""
    idx = slot_index_now(slots)
    for s in slots[idx if idx is not None else 0 :]:
        price = s.get(key)
        if price is not None and price < threshold:
            return s.get("start_ts")
    return None


class _BaseNegativePriceBinarySensor(
    CoordinatorEntity[SlimHuysCoordinator], BinarySensorEntity
):
    """`on` zolang de gekozen prijs nú onder de drempel ligt.

    Subklassen zetten `_breakdown_key` (veld in `current.now.breakdown`) en
    `_slot_key` (veld in de coordinator-slots) — die twee moeten dezelfde
    prijs beschrijven, anders wijzen state en `negative_until` naar iets anders.
    """

    _attr_has_entity_name = True
    _breakdown_key: str
    _slot_key: str
    _threshold: float

    def __init__(self, coordinator, entry, supplier, name: str, unique_suffix: str):
        super().__init__(coordinator)
        self._supplier = supplier
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SlimHuys ({supplier})",
            "manufacturer": "SlimHuys.nl",
            "model": "Energy prices",
            "configuration_url": "https://slimhuys.nl/app/tarieven",
        }

    def _breakdown(self) -> dict[str, Any] | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        return cur["now"]["breakdown"]

    def _price_now(self) -> float | None:
        breakdown = self._breakdown()
        return breakdown.get(self._breakdown_key) if breakdown else None

    @property
    def available(self) -> bool:
        return super().available and self._price_now() is not None

    @property
    def is_on(self) -> bool | None:
        price = self._price_now()
        if price is None:
            return None
        return price < self._threshold

    @property
    def icon(self) -> str:
        return "mdi:flash-alert" if self.is_on else "mdi:flash"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        breakdown = self._breakdown() or {}
        slots = data.get("slots", [])
        attrs: dict[str, Any] = {
            "epex_now": breakdown.get("epex_eur_per_kwh"),
            "total_now": breakdown.get("total_eur_per_kwh"),
            "threshold": self._threshold,
            "supplier": self._supplier,
        }
        if self.is_on:
            # Tot wanneer duurt de huidige negatieve reeks?
            attrs["negative_until"] = _negative_run_end(
                slots, self._slot_key, self._threshold
            )
        else:
            # Wanneer begint de volgende negatieve periode (planning vooruit)?
            attrs["next_negative_start"] = _next_negative_start(
                slots, self._slot_key, self._threshold
            )
        return attrs


class NegativePriceBinarySensor(_BaseNegativePriceBinarySensor):
    """Kale EPEX onder de drempel — teruglevering kost geld."""

    _breakdown_key = "epex_eur_per_kwh"
    _slot_key = "epex"
    _threshold = NEGATIVE_PRICE_THRESHOLD

    def __init__(self, coordinator, entry, supplier):
        super().__init__(
            coordinator, entry, supplier, "Negatieve prijs nu", "negative_price_now"
        )


class NegativeAllInPriceBinarySensor(_BaseNegativePriceBinarySensor):
    """All-in prijs (incl. EB, opslag en btw) onder de drempel.

    Zeldzaam in NL — de belastingcomponent is een flinke bodem — maar als dit
    aan gaat word je betaald om te verbruiken.
    """

    _breakdown_key = "total_eur_per_kwh"
    _slot_key = "price"
    _threshold = NEGATIVE_ALL_IN_PRICE_THRESHOLD

    def __init__(self, coordinator, entry, supplier):
        super().__init__(
            coordinator,
            entry,
            supplier,
            "Negatieve all-in prijs nu",
            "negative_all_in_price_now",
        )
