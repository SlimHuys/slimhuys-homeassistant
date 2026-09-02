"""SlimHuys sensors: huidige prijs, dagstats, goedkoopste blok, en live P1-data."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BATTERY_SUFFIX_ENERGY,
    BATTERY_SUFFIX_POWER,
    BATTERY_SUFFIX_SOC,
    BATTERY_SUFFIX_STATE,
    BATTERY_SUFFIX_TEMP,
    DOMAIN,
    LIVE_SUFFIX_ACTIVE_POWER,
    LIVE_SUFFIX_ACTIVE_POWER_RETURNED,
    LIVE_SUFFIX_CONSUMPTION_TOTAL,
    LIVE_SUFFIX_CURRENT_L1,
    LIVE_SUFFIX_CURRENT_L2,
    LIVE_SUFFIX_CURRENT_L3,
    LIVE_SUFFIX_DELIVERY_TOTAL,
    LIVE_SUFFIX_GAS_TOTAL,
    LIVE_SUFFIX_HUMIDITY,
    LIVE_SUFFIX_LONG_POWER_FAILURES,
    LIVE_SUFFIX_NET_POWER,
    LIVE_SUFFIX_POWER_FAILURES,
    LIVE_SUFFIX_WATER_FLOW,
    LIVE_SUFFIX_POWER_L1,
    LIVE_SUFFIX_POWER_L2,
    LIVE_SUFFIX_POWER_L3,
    LIVE_SUFFIX_TEMPERATURE,
    LIVE_SUFFIX_VOLTAGE_L1,
    LIVE_SUFFIX_VOLTAGE_L2,
    LIVE_SUFFIX_VOLTAGE_L3,
    LIVE_SUFFIX_VOLTAGE_SAGS_L1,
    LIVE_SUFFIX_VOLTAGE_SAGS_L2,
    LIVE_SUFFIX_VOLTAGE_SAGS_L3,
    LIVE_SUFFIX_VOLTAGE_SWELLS_L1,
    LIVE_SUFFIX_VOLTAGE_SWELLS_L2,
    LIVE_SUFFIX_VOLTAGE_SWELLS_L3,
    LIVE_SUFFIX_WATER_TOTAL,
    P1_MODE_PULL,
    USAGE_SUFFIX_CONSUMED_TODAY,
    USAGE_SUFFIX_COST_TODAY,
    USAGE_SUFFIX_DELIVERED_TODAY,
    USAGE_SUFFIX_GAS_COST_TODAY,
    USAGE_SUFFIX_GAS_TODAY,
    USAGE_SUFFIX_NET_COST_TODAY,
    USAGE_SUFFIX_OWN_CONSUMPTION_TODAY,
    USAGE_SUFFIX_PRODUCED_TODAY,
    USAGE_SUFFIX_REVENUE_TODAY,
)
from .coordinator import (
    SlimHuysCoordinator,
    day_is_complete,
    nl_now,
    slots_for_day,
)
from .live_coordinator import SlimHuysLiveCoordinator
from .usage_coordinator import SlimHuysUsageCoordinator

_LOGGER = logging.getLogger(__name__)

UNIT_EUR_PER_KWH = f"{CURRENCY_EURO}/kWh"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    state = hass.data[DOMAIN][entry.entry_id]
    coordinator: SlimHuysCoordinator = state["coordinator"]
    supplier = state["supplier"]
    mode = state["mode"]
    live_coordinator: SlimHuysLiveCoordinator | None = state.get("live_coordinator")
    usage_coordinator: SlimHuysUsageCoordinator = state["usage_coordinator"]

    entities: list[SensorEntity] = [
        CurrentPriceSensor(coordinator, entry, supplier),
        EpexBareSensor(coordinator, entry, supplier),
        TodayAverageSensor(coordinator, entry, supplier),
        TodayLowestSensor(coordinator, entry, supplier),
        TodayHighestSensor(coordinator, entry, supplier),
        CheapestBlockStartSensor(coordinator, entry, supplier),
        CheapestBlockAverageSensor(coordinator, entry, supplier),
        NextNegativeSensor(coordinator, entry, supplier),
        CurrentLevelSensor(coordinator, entry, supplier),
        PricesTodaySensor(coordinator, entry, supplier),
        PricesTomorrowSensor(coordinator, entry, supplier),
        PricesTodayQuarterSensor(coordinator, entry, supplier),
        FeedinCurrentSensor(coordinator, entry, supplier),
        FeedinTodaySensor(coordinator, entry, supplier),
        FeedinTomorrowSensor(coordinator, entry, supplier),
        UsageCostTodaySensor(usage_coordinator, entry, supplier),
        UsageNetCostTodaySensor(usage_coordinator, entry, supplier),
        UsageRevenueTodaySensor(usage_coordinator, entry, supplier),
        UsageGasCostTodaySensor(usage_coordinator, entry, supplier),
        UsageConsumedTodaySensor(usage_coordinator, entry, supplier),
        UsageDeliveredTodaySensor(usage_coordinator, entry, supplier),
        UsageProducedTodaySensor(usage_coordinator, entry, supplier),
        UsageOwnConsumptionTodaySensor(usage_coordinator, entry, supplier),
        UsageGasTodaySensor(usage_coordinator, entry, supplier),
    ]

    if mode == P1_MODE_PULL and live_coordinator is not None:
        entities.extend(_build_live_entities(live_coordinator, entry, supplier))

    async_add_entities(entities)


def _build_live_entities(
    coordinator: SlimHuysLiveCoordinator,
    entry: ConfigEntry,
    supplier: str,
) -> list[SensorEntity]:
    """Hoofd-set + dynamisch 3-fase op basis van probe-discovery.

    1-fase huizen krijgen geen permanent-unavailable L2/L3-entities. Als
    probe niet uitgevoerd is (`probe_at_setup=False`), dan worden 3-fase-
    entities altijd aangemaakt — `discovered_fields` is dan leeg en
    `_should_add_phase` valt terug op `True` voor alle fasen.
    """
    discovered = coordinator.discovered_fields

    def _has(field: str) -> bool:
        # Geen probe gelopen → discovered is leeg → maak alles aan
        return not discovered or field in discovered

    out: list[SensorEntity] = [
        LiveActivePowerSensor(coordinator, entry, supplier),
        LiveActivePowerReturnedSensor(coordinator, entry, supplier),
        LiveNetPowerSensor(coordinator, entry, supplier),
        LiveConsumptionTotalSensor(coordinator, entry, supplier),
        LiveDeliveryTotalSensor(coordinator, entry, supplier),
    ]
    if _has("voltage_l1"):
        out.append(LiveVoltageSensor(coordinator, entry, supplier, "l1"))
    if _has("voltage_l2"):
        out.append(LiveVoltageSensor(coordinator, entry, supplier, "l2"))
    if _has("voltage_l3"):
        out.append(LiveVoltageSensor(coordinator, entry, supplier, "l3"))
    if _has("current_l1_a"):
        out.append(LiveCurrentSensor(coordinator, entry, supplier, "l1"))
    if _has("current_l2_a"):
        out.append(LiveCurrentSensor(coordinator, entry, supplier, "l2"))
    if _has("current_l3_a"):
        out.append(LiveCurrentSensor(coordinator, entry, supplier, "l3"))
    if _has("active_power_l1_w"):
        out.append(LivePowerPhaseSensor(coordinator, entry, supplier, "l1"))
    if _has("active_power_l2_w"):
        out.append(LivePowerPhaseSensor(coordinator, entry, supplier, "l2"))
    if _has("active_power_l3_w"):
        out.append(LivePowerPhaseSensor(coordinator, entry, supplier, "l3"))
    out.append(LiveGasTotalSensor(coordinator, entry, supplier))
    out.append(LiveWaterTotalSensor(coordinator, entry, supplier))
    out.append(LiveWaterFlowSensor(coordinator, entry, supplier))
    if _has("power_failures"):
        out.append(LivePowerFailuresSensor(coordinator, entry, supplier))
    if _has("long_power_failures"):
        out.append(LiveLongPowerFailuresSensor(coordinator, entry, supplier))
    if _has("voltage_sags_l1"):
        out.append(LiveVoltageSagsSwellsSensor(coordinator, entry, supplier, "sags", "l1"))
    if _has("voltage_sags_l2"):
        out.append(LiveVoltageSagsSwellsSensor(coordinator, entry, supplier, "sags", "l2"))
    if _has("voltage_sags_l3"):
        out.append(LiveVoltageSagsSwellsSensor(coordinator, entry, supplier, "sags", "l3"))
    if _has("voltage_swells_l1"):
        out.append(LiveVoltageSagsSwellsSensor(coordinator, entry, supplier, "swells", "l1"))
    if _has("voltage_swells_l2"):
        out.append(LiveVoltageSagsSwellsSensor(coordinator, entry, supplier, "swells", "l2"))
    if _has("voltage_swells_l3"):
        out.append(LiveVoltageSagsSwellsSensor(coordinator, entry, supplier, "swells", "l3"))
    if _has("temp_c"):
        out.append(LiveTemperatureSensor(coordinator, entry, supplier))
    if _has("humid_pct"):
        out.append(LiveHumiditySensor(coordinator, entry, supplier))

    # Eén set per batterij die de probe zag. `temp_c` alleen als de omvormer
    # 'm publiceert — lang niet elke batterij doet dat.
    for battery in coordinator.batteries:
        out.append(BatterySocSensor(coordinator, entry, battery))
        out.append(BatteryPowerSensor(coordinator, entry, battery))
        out.append(BatteryStateSensor(coordinator, entry, battery))
        out.append(BatteryEnergySensor(coordinator, entry, battery))
        if battery.get("temp_c") is not None:
            out.append(BatteryTemperatureSensor(coordinator, entry, battery))
    return out


class _BaseSensor(CoordinatorEntity[SlimHuysCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SlimHuysCoordinator,
        entry: ConfigEntry,
        supplier: str,
        unique_suffix: str,
        name: str,
    ) -> None:
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


class CurrentPriceSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "current_price", "Huidige prijs")

    @property
    def native_value(self) -> float | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        return cur["now"]["breakdown"]["total_eur_per_kwh"]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        b = cur["now"]["breakdown"]
        return {
            "epex_eur_per_kwh": b["epex_eur_per_kwh"],
            "supplier_markup_eur": b["supplier_markup_eur"],
            "energy_tax_eur": b["energy_tax_eur"],
            "vat_eur": b["vat_eur"],
            "valid_from": cur["now"]["timestamp"],
            "valid_until": cur["now"]["valid_until"],
            "level": cur["now"]["level"],
            "supplier": self._supplier,
        }


class EpexBareSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "epex_bare", "EPEX kale prijs")

    @property
    def native_value(self) -> float | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        return cur["now"]["breakdown"]["epex_eur_per_kwh"]


class TodayAverageSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "today_avg", "Daggemiddelde")

    @property
    def native_value(self) -> float | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        return cur["comparison"].get("day_avg_eur")


class TodayLowestSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:arrow-down-bold"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "today_low", "Laagste vandaag")

    @property
    def native_value(self) -> float | None:
        slots = (self.coordinator.data or {}).get("slots", [])
        prices = [s["price"] for s in slots_for_day(slots, _today_str())]
        return min(prices) if prices else None


class TodayHighestSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:arrow-up-bold"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "today_high", "Hoogste vandaag")

    @property
    def native_value(self) -> float | None:
        slots = (self.coordinator.data or {}).get("slots", [])
        prices = [s["price"] for s in slots_for_day(slots, _today_str())]
        return max(prices) if prices else None


class CheapestBlockStartSensor(_BaseSensor):
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "cheapest_block_start", "Goedkoopste blok start")

    @property
    def native_value(self) -> str | None:
        b = (self.coordinator.data or {}).get("cheapest_block")
        if not b:
            return None
        return f"{b['start_day']} {b['start_hour']:02d}:{b['start_minute']:02d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        b = (self.coordinator.data or {}).get("cheapest_block")
        if not b:
            return None
        return {
            "start_day": b["start_day"],
            "start_hour": b["start_hour"],
            "start_minute": b["start_minute"],
            "start_ts": b["start_ts"],
            "end_hour": b["end_hour"],
            "end_minute": b["end_minute"],
            "end_ts": b["end_ts"],
            "duration_hours": b["duration_hours"],
        }


class CheapestBlockAverageSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:cash-marker"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "cheapest_block_avg", "Goedkoopste blok gemiddelde")

    @property
    def native_value(self) -> float | None:
        b = (self.coordinator.data or {}).get("cheapest_block")
        return b["avg"] if b else None


class NextNegativeSensor(_BaseSensor):
    _attr_icon = "mdi:flash-alert"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "next_negative", "Volgende negatieve prijs")

    @property
    def native_value(self) -> str | None:
        n = (self.coordinator.data or {}).get("next_negative")
        if not n:
            return "geen"
        return f"{n['day']} {n['hour']:02d}:{n['minute']:02d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        n = (self.coordinator.data or {}).get("next_negative")
        if not n:
            return None
        return {
            "day": n["day"],
            "hour": n["hour"],
            "minute": n["minute"],
            "start_ts": n["start_ts"],
            "price_eur_per_kwh": n["price"],
        }


class CurrentLevelSensor(_BaseSensor):
    _attr_icon = "mdi:gauge"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "level", "Tariefniveau nu")

    @property
    def native_value(self) -> str | None:
        cur = (self.coordinator.data or {}).get("current")
        return cur["now"]["level"] if cur else None


# ---------- Today/tomorrow price arrays (dashboard-friendly) ----------


def _today_str() -> str:
    return nl_now().strftime("%Y-%m-%d")


def _tomorrow_str() -> str:
    return (nl_now() + timedelta(days=1)).strftime("%Y-%m-%d")


def _build_raw(
    slots: list[dict[str, Any]], field: str = "price"
) -> list[dict[str, Any]]:
    """ApexCharts-vorm: `{start, end, value}` per slot, op eigen resolutie."""
    return [
        {"start": s["start_ts"], "end": s["end_ts"], "value": s[field]}
        for s in slots
        if s.get(field) is not None
    ]


def _day_attrs(
    slots: list[dict[str, Any]],
    day: str,
    resolution: int,
    raw_key: str,
    supplier: str,
) -> dict[str, Any] | None:
    """Gedeelde attributen voor de dag-arrays (consume én feedin).

    `prices` volgt de resolutie van de leverancier: 96 waarden bij kwartier,
    24 bij uur. `granularity_minutes` zegt welke, zodat een dashboard niet
    hoeft te raden aan de lengte van de array.
    """
    day_slots = slots_for_day(slots, day)
    prices = [s["price"] for s in day_slots]
    if not prices:
        return None
    return {
        "prices": prices,
        raw_key: _build_raw(day_slots),
        f"{raw_key}_epex": _build_raw(day_slots, field="epex"),
        "granularity_minutes": resolution,
        "average": sum(prices) / len(prices),
        "min": min(prices),
        "max": max(prices),
        "supplier": supplier,
    }


class PricesTodaySensor(_BaseSensor):
    """Prijzen vandaag — state = huidige prijs, attrs = array + raw_today.

    Op de resolutie van de leverancier: kwartier-leveranciers krijgen 96
    waarden, uur-leveranciers 24.
    """

    # De dag-arrays gaan niet naar de recorder-DB: 96 kwartieren × prices +
    # raw_* + raw_*_epex is ~20 kB per state en dat is boven de 16 kB-limiet.
    # Recorder gooide dan *alle* attributen weg — inclusief
    # unit_of_measurement, waardoor long-term statistics stukliepen op
    # "unit (None) cannot be converted to €/kWh". Frontend/templates zien de
    # attributen gewoon; alleen de historie slaat ze niet op.
    _unrecorded_attributes = frozenset({"prices", "raw_today", "raw_today_epex"})
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:chart-bar"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "prices_today", "Prijzen vandaag")

    @property
    def native_value(self) -> float | None:
        cur = (self.coordinator.data or {}).get("current")
        if not cur:
            return None
        return cur["now"]["breakdown"]["total_eur_per_kwh"]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        return _day_attrs(
            data.get("slots", []),
            _today_str(),
            data.get("resolution_minutes", 60),
            "raw_today",
            self._supplier,
        )


class PricesTomorrowSensor(_BaseSensor):
    """Prijzen morgen — state = daggemiddelde, None vóór EPEX-publicatie (~14:00)."""

    _unrecorded_attributes = frozenset({"prices", "raw_tomorrow", "raw_tomorrow_epex"})
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:chart-bar"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "prices_tomorrow", "Prijzen morgen")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        prices = [s["price"] for s in slots_for_day(data.get("slots", []), _tomorrow_str())]
        if not prices:
            return None
        return sum(prices) / len(prices)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        tomorrow = _tomorrow_str()
        day_slots = slots_for_day(data.get("slots", []), tomorrow)
        attrs = _day_attrs(
            data.get("slots", []),
            tomorrow,
            data.get("resolution_minutes", 60),
            "raw_tomorrow",
            self._supplier,
        ) or {
            "prices": [],
            "raw_tomorrow": [],
            "raw_tomorrow_epex": [],
            "granularity_minutes": data.get("resolution_minutes", 60),
            "average": None,
            "min": None,
            "max": None,
            "supplier": self._supplier,
        }
        # `valid: false` vóór 14:00 — geen `unavailable`, dat geeft
        # automation-warnings. Op dekking gemeten, niet op aantal waarden:
        # 96 kwartieren en 24 uren zijn allebei een volle dag.
        attrs["valid"] = day_is_complete(day_slots)
        return attrs


class PricesTodayQuarterSensor(PricesTodaySensor):
    """Alias van `prijzen_vandaag`, bewaard voor bestaande dashboards.

    Sinds prijzen niet meer naar uren geaggregeerd worden levert
    `prijzen_vandaag` zelf al de native resolutie; deze entity is identiek en
    blijft alleen bestaan zodat kaarten die 'm noemen niet breken.
    """

    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, coordinator, entry, supplier):
        _BaseSensor.__init__(
            self,
            coordinator,
            entry,
            supplier,
            "prices_today_quarter",
            "Prijzen vandaag (kwartier)",
        )


# ---------- Teruglevering (feedin) ----------


def _feedin_model_attrs(model: dict[str, Any] | None) -> dict[str, Any]:
    """Teruglever-model als losse attributen — voor tegels/uitleg op dashboard.

    De jaar-cap/saldering zit bewust NIET in de rate (household-jaar-state);
    we echoën 'm hier zodat je 'm als informatie kunt tonen.
    """
    if not model:
        return {}
    return {
        "feedin_strategy": model.get("strategy"),
        "feedin_description": model.get("description"),
        "feedin_markup_eur_per_kwh": model.get("feedin_markup_eur_per_kwh"),
        "feedin_bonus_pct": model.get("feedin_bonus_pct"),
        "feedin_bonus_daytime_only": model.get("feedin_bonus_daytime_only"),
        "feedin_bonus_annual_cap_kwh": model.get("feedin_bonus_annual_cap_kwh"),
    }


class FeedinCurrentSensor(_BaseSensor):
    """Huidige terugleververgoeding (incl. btw)."""

    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "feedin_current", "Teruglevering nu")

    @property
    def native_value(self) -> float | None:
        fc = (self.coordinator.data or {}).get("feedin_current")
        if not fc:
            return None
        return ((fc.get("now") or {}).get("feedin") or {}).get("feedin_eur_per_kwh")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        fc = data.get("feedin_current")
        if not fc:
            return None
        now = fc.get("now") or {}
        fd = now.get("feedin") or {}
        comp = fc.get("comparison") or {}
        attrs = {
            "epex_eur_per_kwh": fd.get("epex_eur_per_kwh"),
            "level": now.get("level"),
            "valid_from": now.get("timestamp"),
            "valid_until": now.get("valid_until"),
            "day_avg_eur": comp.get("day_avg_eur"),
            "vs_day_avg_pct": comp.get("vs_day_avg_pct"),
            "supplier": self._supplier,
        }
        attrs.update(_feedin_model_attrs(data.get("feedin_model")))
        return attrs


class FeedinTodaySensor(_BaseSensor):
    """Teruglevering vandaag — state = huidige rate, attrs = array + raw_today."""

    _unrecorded_attributes = frozenset({"prices", "raw_today", "raw_today_epex"})
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "feedin_today", "Teruglevering vandaag")

    @property
    def native_value(self) -> float | None:
        fc = (self.coordinator.data or {}).get("feedin_current")
        if not fc:
            return None
        return ((fc.get("now") or {}).get("feedin") or {}).get("feedin_eur_per_kwh")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        attrs = _day_attrs(
            data.get("feedin_slots", []),
            _today_str(),
            data.get("feedin_resolution_minutes", 60),
            "raw_today",
            self._supplier,
        )
        if attrs is None:
            return None
        attrs.update(_feedin_model_attrs(data.get("feedin_model")))
        return attrs


class FeedinTomorrowSensor(_BaseSensor):
    """Teruglevering morgen — state = daggemiddelde, None vóór EPEX-publicatie."""

    _unrecorded_attributes = frozenset({"prices", "raw_tomorrow", "raw_tomorrow_epex"})
    _attr_native_unit_of_measurement = UNIT_EUR_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, "feedin_tomorrow", "Teruglevering morgen")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        prices = [
            s["price"]
            for s in slots_for_day(data.get("feedin_slots", []), _tomorrow_str())
        ]
        if not prices:
            return None
        return sum(prices) / len(prices)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        tomorrow = _tomorrow_str()
        resolution = data.get("feedin_resolution_minutes", 60)
        day_slots = slots_for_day(data.get("feedin_slots", []), tomorrow)
        attrs = _day_attrs(
            data.get("feedin_slots", []),
            tomorrow,
            resolution,
            "raw_tomorrow",
            self._supplier,
        ) or {
            "prices": [],
            "raw_tomorrow": [],
            "raw_tomorrow_epex": [],
            "granularity_minutes": resolution,
            "average": None,
            "min": None,
            "max": None,
            "supplier": self._supplier,
        }
        # `valid: false` vóór ENTSO-E-publicatie — geen `unavailable`
        attrs["valid"] = day_is_complete(day_slots)
        attrs.update(_feedin_model_attrs(data.get("feedin_model")))
        return attrs


# ---------- Live (pull-mode) entities ----------


class _LiveBaseSensor(CoordinatorEntity[SlimHuysLiveCoordinator], SensorEntity):
    """Base voor pull-mode entities — read'en uit live_coordinator.data[stream][field]."""

    _attr_has_entity_name = True
    _stream: str = "p1"
    _field: str = ""

    def __init__(
        self,
        coordinator: SlimHuysLiveCoordinator,
        entry: ConfigEntry,
        supplier: str,
        unique_suffix: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        # Zelfde device-identifier als prijssensoren — één SlimHuys-device met
        # twee capabilities (prijs + live), niet twee aparte devices.
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SlimHuys ({supplier})",
            "manufacturer": "SlimHuys.nl",
            "model": "Energy prices + P1",
            "configuration_url": "https://slimhuys.nl/app/tarieven",
        }

    def _read(self, key: str | None = None) -> Any:
        block = (self.coordinator.data or {}).get(self._stream) or {}
        return block.get(key or self._field)


class LiveActivePowerSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:flash"
    _field = "active_power_w"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_ACTIVE_POWER, "Actief vermogen")

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


class LiveActivePowerReturnedSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:transmission-tower-export"
    _field = "active_power_returned_w"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_ACTIVE_POWER_RETURNED, "Teruglevering vermogen")

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


class LiveNetPowerSensor(_LiveBaseSensor):
    """Netto vermogen — signed, zelfde conventie als de fase-sensoren.

    Afname en teruglevering zijn twee losse, altijd-positieve meter-registers
    (OBIS 1.7.0 / 2.7.0), geen splitsing van één netto-getal: op 3-fase kunnen
    ze tegelijk ≠ 0 zijn (import op de ene fase, export op de andere).
    `sensor.actief_vermogen` wordt daardoor nooit negatief, ook niet als je
    netto flink terugleeft — verwarrend naast `sensor.vermogen_l*`, die wél
    signed zijn. Deze sensor trekt ze van elkaar af: positief = van het net,
    negatief = naar het net.

    Bewust lossy: uit dit getal alleen zijn de twee bruto-waarden niet terug
    te rekenen. Voor kosten (afname- en teruglevertarief verschillen) moet je
    de losse sensoren gebruiken, niet deze.
    """
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_NET_POWER, "Netto vermogen")

    @property
    def native_value(self) -> int | None:
        drawn = self._read("active_power_w")
        returned = self._read("active_power_returned_w")
        if drawn is None and returned is None:
            return None
        return int(drawn or 0) - int(returned or 0)


class LiveConsumptionTotalSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:counter"
    _field = "consumption_total_kwh"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_CONSUMPTION_TOTAL, "Verbruik totaal")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveDeliveryTotalSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:counter"
    _field = "delivered_total_kwh"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_DELIVERY_TOTAL, "Teruglevering totaal")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveVoltageSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_icon = "mdi:sine-wave"
    # L2/L3 zijn diagnostic — voorkomt dat 3-fase-details het hoofd-dashboard vervuilen
    _PHASE_NAMES = {"l1": "Spanning L1", "l2": "Spanning L2", "l3": "Spanning L3"}
    _PHASE_SUFFIX = {
        "l1": LIVE_SUFFIX_VOLTAGE_L1,
        "l2": LIVE_SUFFIX_VOLTAGE_L2,
        "l3": LIVE_SUFFIX_VOLTAGE_L3,
    }

    def __init__(self, coordinator, entry, supplier, phase: str):
        super().__init__(
            coordinator, entry, supplier,
            self._PHASE_SUFFIX[phase], self._PHASE_NAMES[phase],
        )
        self._field = f"voltage_{phase}"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveCurrentSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_icon = "mdi:current-ac"
    _PHASE_NAMES = {"l1": "Stroom L1", "l2": "Stroom L2", "l3": "Stroom L3"}
    _PHASE_SUFFIX = {
        "l1": LIVE_SUFFIX_CURRENT_L1,
        "l2": LIVE_SUFFIX_CURRENT_L2,
        "l3": LIVE_SUFFIX_CURRENT_L3,
    }

    def __init__(self, coordinator, entry, supplier, phase: str):
        super().__init__(
            coordinator, entry, supplier,
            self._PHASE_SUFFIX[phase], self._PHASE_NAMES[phase],
        )
        self._field = f"current_{phase}_a"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LivePowerPhaseSensor(_LiveBaseSensor):
    """Per-fase actief vermogen — signed (negatief = export op die fase)."""
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:flash"
    _PHASE_NAMES = {"l1": "Vermogen L1", "l2": "Vermogen L2", "l3": "Vermogen L3"}
    _PHASE_SUFFIX = {
        "l1": LIVE_SUFFIX_POWER_L1,
        "l2": LIVE_SUFFIX_POWER_L2,
        "l3": LIVE_SUFFIX_POWER_L3,
    }

    def __init__(self, coordinator, entry, supplier, phase: str):
        super().__init__(
            coordinator, entry, supplier,
            self._PHASE_SUFFIX[phase], self._PHASE_NAMES[phase],
        )
        self._field = f"active_power_{phase}_w"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


class LiveGasTotalSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.GAS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:gas-burner"
    _field = "gas_total_m3"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_GAS_TOTAL, "Gas totaal")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveWaterTotalSensor(_LiveBaseSensor):
    """Water-meter cumulatief — native L (puls-eenheid), display m³ voor NL."""
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.WATER
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:water"
    _stream = "water"
    _field = "total_liter"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_WATER_TOTAL, "Water totaal")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveWaterFlowSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.LITERS_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:gauge"
    _stream = "water"
    _field = "flow_lpm"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_WATER_FLOW, "Waterdebiet")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveTemperatureSensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer"
    _field = "temp_c"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_TEMPERATURE, "Temperatuur")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LiveHumiditySensor(_LiveBaseSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:water-percent"
    _field = "humid_pct"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_HUMIDITY, "Luchtvochtigheid")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class LivePowerFailuresSensor(_LiveBaseSensor):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:power-plug-off"
    _field = "power_failures"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_POWER_FAILURES, "Stroomuitvallen")

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


class LiveLongPowerFailuresSensor(_LiveBaseSensor):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:power-plug-off-outline"
    _field = "long_power_failures"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, LIVE_SUFFIX_LONG_POWER_FAILURES, "Lange stroomuitvallen")

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


class LiveVoltageSagsSwellsSensor(_LiveBaseSensor):
    """Spanningsdips (sags) of -pieken (swells) per fase — cumulatieve tellers."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    _SUFFIX = {
        ("sags", "l1"): LIVE_SUFFIX_VOLTAGE_SAGS_L1,
        ("sags", "l2"): LIVE_SUFFIX_VOLTAGE_SAGS_L2,
        ("sags", "l3"): LIVE_SUFFIX_VOLTAGE_SAGS_L3,
        ("swells", "l1"): LIVE_SUFFIX_VOLTAGE_SWELLS_L1,
        ("swells", "l2"): LIVE_SUFFIX_VOLTAGE_SWELLS_L2,
        ("swells", "l3"): LIVE_SUFFIX_VOLTAGE_SWELLS_L3,
    }
    _NL = {"sags": "Spanningsdips", "swells": "Spanningspieken"}
    _ICON = {"sags": "mdi:sine-wave", "swells": "mdi:sine-wave"}

    def __init__(self, coordinator, entry, supplier, kind: str, phase: str):
        phase_up = phase.upper()
        super().__init__(
            coordinator, entry, supplier,
            self._SUFFIX[(kind, phase)],
            f"{self._NL[kind]} {phase_up}",
        )
        self._field = f"voltage_{kind}_{phase}"
        self._attr_icon = self._ICON[kind]

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


# ---------- Dagtotalen + dagkosten (`/me/usage/current` → `today`) ----------


class _UsageBaseSensor(CoordinatorEntity[SlimHuysUsageCoordinator], SensorEntity):
    """Base voor de dag-sensoren — lezen uit `usage_coordinator.data["today"]`.

    Alle dag-sensoren zijn `state_class = TOTAL` met een `last_reset` op
    middernacht NL. Niet TOTAL_INCREASING: deze waarden vallen om 00:00 terug
    naar 0 en de recorder zou dat zonder `last_reset` als een meter-rollover
    lezen en de dagsprong bij het jaartotaal optellen.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL
    _field: str = ""

    def __init__(
        self,
        coordinator: SlimHuysUsageCoordinator,
        entry: ConfigEntry,
        supplier: str,
        unique_suffix: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SlimHuys ({supplier})",
            "manufacturer": "SlimHuys.nl",
            "model": "Energy prices",
            "configuration_url": "https://slimhuys.nl/app/verbruik",
        }

    @property
    def _today(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("today")

    @property
    def available(self) -> bool:
        # `today` is None bij een huis zonder meter én zonder supplier-cloud;
        # losse velden zijn None als de bron ontbreekt (gaskosten zonder
        # gastarief bijvoorbeeld). In beide gevallen is `unavailable` eerlijker
        # dan een 0 die als "vandaag niets verbruikt" leest.
        return super().available and self._read() is not None

    @property
    def last_reset(self):
        return nl_now().replace(hour=0, minute=0, second=0, microsecond=0)

    def _read(self, key: str | None = None) -> Any:
        return (self._today or {}).get(key or self._field)

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class _UsageCostSensor(_UsageBaseSensor):
    """€-sensor. `monetary` mág hier wél — dit is een bedrag, geen tarief.

    (Zie v1.7.2: van de €/kWh-prijssensoren is `monetary` juist verwijderd,
    omdat een prijs-per-eenheid geen som is die de recorder mag optellen.)
    """

    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2


class _UsageEnergySensor(_UsageBaseSensor):
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_suggested_display_precision = 2


class UsageCostTodaySensor(_UsageCostSensor):
    _attr_icon = "mdi:cash-minus"
    _field = "cost_eur"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_COST_TODAY, "Kosten vandaag")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        today = self._today
        if not today:
            return None
        return {
            "consumed_kwh": today.get("consumed_kwh"),
            "quarters_count": today.get("quarters_count"),
            # `supplier-cloud` = afgeleid uit Tibber/Frank-data i.p.v. de P1-
            # meter; dan loopt het totaal een kwartier of langer achter.
            "source": today.get("source", "p1"),
        }


class UsageRevenueTodaySensor(_UsageCostSensor):
    _attr_icon = "mdi:cash-plus"
    _field = "revenue_eur"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_REVENUE_TODAY, "Opbrengst vandaag")


class UsageGasCostTodaySensor(_UsageCostSensor):
    _attr_icon = "mdi:fire"
    _field = "gas_cost_eur"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_GAS_COST_TODAY, "Gaskosten vandaag")


class UsageNetCostTodaySensor(_UsageCostSensor):
    """Stroom + gas − teruglevering: wat de dag je netto kost.

    Gas telt mee zodra de API een gastarief kent; ontbreekt dat, dan is dit
    puur elektra. Blijft beschikbaar zolang `cost_eur` er is — anders zou de
    sensor wegvallen op een huis zonder gasaansluiting.
    """

    _attr_icon = "mdi:cash-multiple"
    _field = "cost_eur"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_NET_COST_TODAY, "Netto kosten vandaag")

    @property
    def native_value(self) -> float | None:
        cost = self._read("cost_eur")
        if cost is None:
            return None
        gas = self._read("gas_cost_eur") or 0.0
        revenue = self._read("revenue_eur") or 0.0
        return round(float(cost) + float(gas) - float(revenue), 2)


class UsageConsumedTodaySensor(_UsageEnergySensor):
    _attr_icon = "mdi:transmission-tower-import"
    _field = "consumed_kwh"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_CONSUMED_TODAY, "Verbruik vandaag")


class UsageDeliveredTodaySensor(_UsageEnergySensor):
    # Naam bewust niet "Teruglevering vandaag" — die is al bezet door de
    # prijssensor (`feedin_today`, €/kWh) en zou een entity-id-botsing geven.
    _attr_icon = "mdi:transmission-tower-export"
    _field = "delivered_kwh"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_DELIVERED_TODAY, "Teruggeleverd vandaag")


class UsageProducedTodaySensor(_UsageEnergySensor):
    _attr_icon = "mdi:solar-power"
    _field = "produced_kwh"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_PRODUCED_TODAY, "Opwek vandaag")


class UsageOwnConsumptionTodaySensor(_UsageEnergySensor):
    _attr_icon = "mdi:home-lightning-bolt"
    _field = "own_consumption_kwh"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(
            coordinator, entry, supplier,
            USAGE_SUFFIX_OWN_CONSUMPTION_TODAY, "Eigen verbruik vandaag",
        )


class UsageGasTodaySensor(_UsageBaseSensor):
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_device_class = SensorDeviceClass.GAS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:meter-gas"
    _field = "gas_m3"

    def __init__(self, coordinator, entry, supplier):
        super().__init__(coordinator, entry, supplier, USAGE_SUFFIX_GAS_TODAY, "Gasverbruik vandaag")


# ---------- Thuisbatterij (pull-mode, `battery-reading`-events) ----------


class _BatteryBaseSensor(CoordinatorEntity[SlimHuysLiveCoordinator], SensorEntity):
    """Base voor batterij-entities — leest `data["batteries"][battery_id]`.

    Elke batterij wordt een eigen HA-device (via_device onder de SlimHuys-
    entry) in plaats van nóg een reeks entities op het prijs-device: een huis
    kan meerdere batterijen hebben en die horen in de UI niet op één hoop.
    """

    _attr_has_entity_name = True
    _field: str = ""

    def __init__(
        self,
        coordinator: SlimHuysLiveCoordinator,
        entry: ConfigEntry,
        battery: dict[str, Any],
        unique_suffix: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery["id"]
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}_{self._battery_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_battery_{self._battery_id}")},
            "via_device": (DOMAIN, entry.entry_id),
            "name": battery.get("name") or "Thuisbatterij",
            "manufacturer": (battery.get("brand") or "SlimHuys.nl").title(),
            "model": battery.get("model") or "Thuisbatterij",
            "configuration_url": "https://slimhuys.nl/app/batterij",
        }

    def _read(self, key: str | None = None) -> Any:
        batteries = (self.coordinator.data or {}).get("batteries") or {}
        return (batteries.get(self._battery_id) or {}).get(key or self._field)

    @property
    def available(self) -> bool:
        return super().available and self._read() is not None

    @property
    def native_value(self) -> Any:
        return self._read()


class BatterySocSensor(_BatteryBaseSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _field = "soc_pct"

    def __init__(self, coordinator, entry, battery):
        super().__init__(coordinator, entry, battery, BATTERY_SUFFIX_SOC, "Laadtoestand")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class BatteryPowerSensor(_BatteryBaseSensor):
    """Signed vermogen, API-conventie: positief = laden, negatief = ontladen."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging"
    _field = "power_w"

    def __init__(self, coordinator, entry, battery):
        super().__init__(coordinator, entry, battery, BATTERY_SUFFIX_POWER, "Vermogen")

    @property
    def native_value(self) -> int | None:
        v = self._read()
        return int(v) if v is not None else None


class BatteryStateSensor(_BatteryBaseSensor):
    """`charging` / `discharging` / `idle` — door de API afgeleid, niet hier.

    Bewust niet zelf uit `power_w` berekend: de API hanteert een dode zone van
    ±25 W zodat standby-verbruik van de omvormer de status niet laat knipperen,
    en die drempel hoort op één plek te staan.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["charging", "discharging", "idle"]
    _attr_icon = "mdi:battery-sync"
    _field = "state"

    def __init__(self, coordinator, entry, battery):
        super().__init__(coordinator, entry, battery, BATTERY_SUFFIX_STATE, "Status")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        today = self._read("today")
        if not today:
            return None
        return {
            "charged_today_kwh": today.get("charged_kwh"),
            "discharged_today_kwh": today.get("discharged_kwh"),
            "cycles_today": today.get("cycles"),
            "saved_eur_today": today.get("saved_eur"),
            "mode": self._read("mode"),
        }


class BatteryEnergySensor(_BatteryBaseSensor):
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:battery-high"
    _field = "energy_kwh"

    def __init__(self, coordinator, entry, battery):
        super().__init__(coordinator, entry, battery, BATTERY_SUFFIX_ENERGY, "Inhoud")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None


class BatteryTemperatureSensor(_BatteryBaseSensor):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _field = "temp_c"

    def __init__(self, coordinator, entry, battery):
        super().__init__(coordinator, entry, battery, BATTERY_SUFFIX_TEMP, "Temperatuur")

    @property
    def native_value(self) -> float | None:
        v = self._read()
        return float(v) if v is not None else None
