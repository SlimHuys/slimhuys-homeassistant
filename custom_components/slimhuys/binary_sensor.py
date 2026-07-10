"""SlimHuys binary sensors: negatieve-prijs-trigger voor automatiseringen.

Bewust op basis van de KALE EPEX (`epex_eur_per_kwh`), niet de totaalprijs.
De totaalprijs bevat energiebelasting + opslag + btw en is in NL vrijwel nooit
negatief, terwijl de kale EPEX regelmatig onder nul duikt — dát is het moment
waarop terugleveren geld kost en je zonnepanelen wilt dimmen/uitzetten.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NEGATIVE_PRICE_THRESHOLD
from .coordinator import SlimHuysCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    state = hass.data[DOMAIN][entry.entry_id]
    coordinator: SlimHuysCoordinator = state["coordinator"]
    supplier = state["supplier"]
    async_add_entities([NegativePriceBinarySensor(coordinator, entry, supplier)])


def _now_index(hourly: list[dict[str, Any]]) -> int | None:
    """Index van het huidige uur in de hourly-lijst (dag + uur match)."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    for i, h in enumerate(hourly):
        if h["day"] == today and h["hour"] == now.hour:
            return i
    return None


def _negative_run_end(hourly: list[dict[str, Any]]) -> str | None:
    """Start-ts van het eerste uur (vanaf nu) waarop de EPEX weer >= drempel is.

    Dus: tot wanneer de huidige negatieve reeks duurt. `None` als de data niet
    ver genoeg reikt om het einde te zien.
    """
    idx = _now_index(hourly)
    if idx is None:
        return None
    for h in hourly[idx:]:
        epex = h.get("epex")
        if epex is None or epex >= NEGATIVE_PRICE_THRESHOLD:
            return h.get("start_ts")
    return None


def _next_negative_start(hourly: list[dict[str, Any]]) -> str | None:
    """Start-ts van de eerstvolgende negatieve EPEX-periode (vanaf nu)."""
    idx = _now_index(hourly)
    start = idx if idx is not None else 0
    for h in hourly[start:]:
        epex = h.get("epex")
        if epex is not None and epex < NEGATIVE_PRICE_THRESHOLD:
            return h.get("start_ts")
    return None


class NegativePriceBinarySensor(
    CoordinatorEntity[SlimHuysCoordinator], BinarySensorEntity
):
    """`on` zolang de kale EPEX nú onder de drempel (default 0) ligt."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator)
        self._supplier = supplier
        self._attr_name = "Negatieve prijs nu"
        self._attr_unique_id = f"{entry.entry_id}_negative_price_now"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SlimHuys ({supplier})",
            "manufacturer": "SlimHuys.nl",
            "model": "Energy prices",
            "configuration_url": "https://slimhuys.nl/app/tarieven",
        }

    def _epex_now(self) -> float | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        return cur["now"]["breakdown"].get("epex_eur_per_kwh")

    @property
    def available(self) -> bool:
        return super().available and self._epex_now() is not None

    @property
    def is_on(self) -> bool | None:
        epex = self._epex_now()
        if epex is None:
            return None
        return epex < NEGATIVE_PRICE_THRESHOLD

    @property
    def icon(self) -> str:
        return "mdi:flash-alert" if self.is_on else "mdi:flash"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        cur = data.get("current")
        hourly = data.get("hourly", [])
        attrs: dict[str, Any] = {
            "epex_now": self._epex_now(),
            "total_now": (
                cur["now"]["breakdown"]["total_eur_per_kwh"] if cur else None
            ),
            "threshold": NEGATIVE_PRICE_THRESHOLD,
            "supplier": self._supplier,
        }
        if self.is_on:
            # Tot wanneer moeten de panelen uit blijven?
            attrs["negative_until"] = _negative_run_end(hourly)
        else:
            # Wanneer begint de volgende negatieve periode (planning vooruit)?
            attrs["next_negative_start"] = _next_negative_start(hourly)
        return attrs
