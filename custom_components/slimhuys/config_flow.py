"""Config flow voor SlimHuys."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SlimHuysApiError, SlimHuysAuthError, SlimHuysClient
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_BATTERY_BRAND,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_CHARGED_TOTAL,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_DISCHARGED_TOTAL,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_EXTERNAL_ID,
    CONF_BATTERY_INTERVAL,
    CONF_BATTERY_INVERT_POWER,
    CONF_BATTERY_MODE,
    CONF_BATTERY_MODEL,
    CONF_BATTERY_NAME,
    CONF_BATTERY_POWER,
    CONF_BATTERY_PV_POWER,
    CONF_BATTERY_SOC,
    CONF_BATTERY_TEMP,
    CONF_P1_CONSUMPTION,
    CONF_P1_CURRENT_L1,
    CONF_P1_CURRENT_L2,
    CONF_P1_CURRENT_L3,
    CONF_P1_DELIVERY,
    CONF_P1_ENABLED,
    CONF_P1_GAS,
    CONF_P1_INTERVAL,
    CONF_P1_MODE,
    CONF_P1_POWER,
    CONF_P1_POWER_L1,
    CONF_P1_POWER_L2,
    CONF_P1_POWER_L3,
    CONF_P1_POWER_RETURNED,
    CONF_P1_POWER_RETURNED_L1,
    CONF_P1_POWER_RETURNED_L2,
    CONF_P1_POWER_RETURNED_L3,
    CONF_P1_VOLTAGE_L1,
    CONF_P1_VOLTAGE_L2,
    CONF_P1_VOLTAGE_L3,
    CONF_PULL_POLL_FALLBACK,
    CONF_PULL_PROBE_AT_SETUP,
    CONF_SUPPLIER,
    DEFAULT_BASE_URL,
    DEFAULT_BATTERY_INTERVAL,
    DEFAULT_P1_INTERVAL,
    DEFAULT_PULL_POLL_FALLBACK,
    DEFAULT_PULL_PROBE_AT_SETUP,
    DEFAULT_SUPPLIER,
    DOMAIN,
    MAX_BATTERY_INTERVAL,
    MAX_P1_INTERVAL,
    MIN_BATTERY_INTERVAL,
    MIN_P1_INTERVAL,
    P1_MODE_NONE,
    P1_MODE_PULL,
    P1_MODE_PUSH,
)

_LOGGER = logging.getLogger(__name__)


def _detect_dsmr_sensors(hass) -> dict[str, list[str]]:
    """Naam-patroon-suggesties voor de drie defaults in de dropdowns."""
    consumption: list[str] = []
    delivery: list[str] = []
    power: list[str] = []

    for state in hass.states.async_all("sensor"):
        eid = state.entity_id.lower()
        if any(p in eid for p in [
            "consumption_total", "energy_import", "_import_total", "imported_energy",
            "stroom_verbruik_totaal", "verbruik_totaal", "_consumption", "_import",
        ]):
            consumption.append(state.entity_id)
        elif any(p in eid for p in [
            "delivery_total", "energy_export", "_export_total", "exported_energy",
            "_teruglevering_totaal", "teruglevering_totaal", "_delivery", "_export",
        ]):
            delivery.append(state.entity_id)
        elif any(p in eid for p in [
            "current_electricity_usage", "active_power", "current_power",
            "vermogen_nu", "current_consumption_w",
        ]):
            power.append(state.entity_id)

    return {"consumption": consumption, "delivery": delivery, "power": power}


def _energy_sensors(hass) -> list[str]:
    """Sensors met unit kWh — voor cumulatief verbruik / teruglevering."""
    out = []
    for state in hass.states.async_all("sensor"):
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        if unit == "kwh":
            out.append(state.entity_id)
    return sorted(out)


def _power_sensors(hass) -> list[str]:
    """Sensors met unit W / kW — voor huidig vermogen."""
    out = []
    for state in hass.states.async_all("sensor"):
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        device_class = (state.attributes.get("device_class") or "").lower()
        if unit in ("w", "kw") or device_class == "power":
            out.append(state.entity_id)
    return sorted(out)


def _voltage_sensors(hass) -> list[str]:
    """Sensors met unit V — voor 3-fase voltage-meting."""
    out = []
    for state in hass.states.async_all("sensor"):
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        device_class = (state.attributes.get("device_class") or "").lower()
        if unit == "v" or device_class == "voltage":
            out.append(state.entity_id)
    return sorted(out)


def _current_sensors(hass) -> list[str]:
    """Sensors met unit A — voor 3-fase stroom-meting."""
    out = []
    for state in hass.states.async_all("sensor"):
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        device_class = (state.attributes.get("device_class") or "").lower()
        if unit == "a" or device_class == "current":
            out.append(state.entity_id)
    return sorted(out)


def _gas_sensors(hass) -> list[str]:
    """Sensors met unit m³ of device_class=gas — DSMR-gas-aansluiting."""
    out = []
    for state in hass.states.async_all("sensor"):
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        device_class = (state.attributes.get("device_class") or "").lower()
        if unit in ("m³", "m3") or device_class == "gas":
            out.append(state.entity_id)
    return sorted(out)


def _suggest_phase(candidates: list[str], phase: str) -> str | None:
    """Pak eerste sensor met '_l1' / '_l2' / '_l3' in de naam."""
    needle = f"_l{phase}"
    return next((s for s in candidates if needle in s.lower()), None)


def _suggest_gas(candidates: list[str]) -> str | None:
    return next((s for s in candidates if "gas" in s.lower()), None) or (
        candidates[0] if candidates else None
    )


def _suggest_power_returned(candidates: list[str]) -> str | None:
    """Pak de totaal-teruglevering, niet een fase-variant.

    DSMR noemt dit 'delivery' (wat je aan het net levert); HomeWizard en
    Tibber gebruiken 'export' of 'teruglevering'.
    """
    needles = ("delivery", "teruglevering", "returned", "export")
    return next(
        (
            s for s in candidates
            if any(n in s.lower() for n in needles)
            and not any(f"_l{p}" in s.lower() for p in "123")
        ),
        None,
    )


def _add_optional_phase_fields(
    schema_dict: dict,
    voltage_choices: dict, current_choices: dict, power_choices: dict, gas_choices: dict,
    voltage_sensors: list, current_sensors: list, power_sensors: list, gas_sensors: list,
    *, defaults: dict | None = None,
) -> None:
    """Voegt de optionele 3-fase-, teruglevering- en gas-dropdowns toe.

    Gedeeld tussen Config- en OptionsFlow. `defaults` staat toe om bestaande
    waarden uit een bewaarde entry voor te selecteren — als er geen `defaults`
    zijn (= eerste setup), suggereert de helper sensors waarvan de naam '_l1' /
    '_l2' / '_l3' / 'gas' bevat.
    """
    defaults = defaults or {}

    def _safe_default(value, choices):
        return value if value in choices else vol.UNDEFINED

    def _value_for(key, candidates_by_phase, fallback_suggester=None):
        if key in defaults:
            return _safe_default(defaults[key], _choices_for(key))
        # Suggest from sensor names op basis van phase-indicator.
        suggested = fallback_suggester() if fallback_suggester else None
        return suggested or vol.UNDEFINED

    def _choices_for(key):
        if key in (CONF_P1_VOLTAGE_L1, CONF_P1_VOLTAGE_L2, CONF_P1_VOLTAGE_L3):
            return voltage_choices
        if key in (CONF_P1_CURRENT_L1, CONF_P1_CURRENT_L2, CONF_P1_CURRENT_L3):
            return current_choices
        if key in (CONF_P1_POWER_L1, CONF_P1_POWER_L2, CONF_P1_POWER_L3,
                   CONF_P1_POWER_RETURNED_L1, CONF_P1_POWER_RETURNED_L2, CONF_P1_POWER_RETURNED_L3,
                   CONF_P1_POWER_RETURNED):
            return power_choices
        if key == CONF_P1_GAS:
            return gas_choices
        return {}

    field_specs = [
        (CONF_P1_VOLTAGE_L1, voltage_sensors, "1"),
        (CONF_P1_VOLTAGE_L2, voltage_sensors, "2"),
        (CONF_P1_VOLTAGE_L3, voltage_sensors, "3"),
        (CONF_P1_CURRENT_L1, current_sensors, "1"),
        (CONF_P1_CURRENT_L2, current_sensors, "2"),
        (CONF_P1_CURRENT_L3, current_sensors, "3"),
        (CONF_P1_POWER_L1, power_sensors, "1"),
        (CONF_P1_POWER_L2, power_sensors, "2"),
        (CONF_P1_POWER_L3, power_sensors, "3"),
        (CONF_P1_POWER_RETURNED_L1, power_sensors, "1"),
        (CONF_P1_POWER_RETURNED_L2, power_sensors, "2"),
        (CONF_P1_POWER_RETURNED_L3, power_sensors, "3"),
    ]
    for key, candidates, phase in field_specs:
        choices = _choices_for(key)
        if not choices:
            continue
        default = _value_for(key, candidates, lambda c=candidates, p=phase: _suggest_phase(c, p))
        schema_dict[vol.Optional(key, default=default)] = vol.In({**choices, "": "—"})

    if power_choices:
        default = _value_for(
            CONF_P1_POWER_RETURNED, power_sensors,
            lambda c=power_sensors: _suggest_power_returned(c),
        )
        schema_dict[vol.Optional(CONF_P1_POWER_RETURNED, default=default)] = vol.In(
            {**power_choices, "": "—"}
        )

    if gas_choices:
        default = _value_for(CONF_P1_GAS, gas_sensors, lambda c=gas_sensors: _suggest_gas(c))
        schema_dict[vol.Optional(CONF_P1_GAS, default=default)] = vol.In({**gas_choices, "": "—"})



# ---------- Thuisbatterij ----------

# Alle batterij-velden waarvan de waarde een entity-id is. Gedeeld door
# Config- en OptionsFlow zodat de twee niet uit elkaar kunnen lopen.
BATTERY_ENTITY_FIELDS = (
    CONF_BATTERY_SOC,
    CONF_BATTERY_POWER,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_CHARGED_TOTAL,
    CONF_BATTERY_DISCHARGED_TOTAL,
    CONF_BATTERY_MODE,
    CONF_BATTERY_TEMP,
    CONF_BATTERY_PV_POWER,
)

BATTERY_TEXT_FIELDS = (
    CONF_BATTERY_NAME,
    CONF_BATTERY_BRAND,
    CONF_BATTERY_MODEL,
    CONF_BATTERY_EXTERNAL_ID,
)


# Naam-hints voor een SoC-sensor die géén device_class draagt. Bewust ruim:
# een vals-positief kost een extra (optioneel) scherm, een vals-negatief maakt
# de stap onbereikbaar voor wie 'm juist nodig heeft.
_SOC_NAME_HINTS = ("battery", "batterij", "accu", "soc", "state_of_charge", "charge")


def _has_battery_candidates(hass) -> bool:
    """Is er iets dat op een thuisbatterij lijkt? Zo nee, stap overslaan.

    Een `%`-sensor met `device_class: battery`, óf een `%`-sensor waarvan de
    naam naar een batterij wijst. Die tweede tak is er omdat lang niet elke
    batterij-integratie een device_class zet: een Marstek via een RS485-bridge
    publiceert `sensor.…_battery_state_of_charge` met alléén
    `unit_of_measurement: %`, en die viel op de strengere check buiten de boot.
    """
    for state in hass.states.async_all("sensor"):
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        if unit != "%":
            continue
        device_class = (state.attributes.get("device_class") or "").lower()
        if device_class == "battery":
            return True
        haystack = f"{state.entity_id} {state.attributes.get('friendly_name') or ''}".lower()
        if any(hint in haystack for hint in _SOC_NAME_HINTS):
            return True
    return False


def _battery_schema(defaults: dict | None = None) -> dict:
    """Schema-dict voor de batterij-stap.

    Bewust `EntitySelector` in plaats van de `vol.In`-dropdowns die de
    P1-stap gebruikt: die bouwen hun keuzelijst uit `hass.states`, en een
    entity die tijdens setup `unavailable` is staat er dan niet in. Precies
    het geval bij een omvormer die 's nachts slaapt of een cloud-integratie
    die nog niet gepold heeft.
    """
    defaults = defaults or {}

    def _prefill(key):
        # `description.suggested_value` i.p.v. `default`: laat het veld leeg
        # als er niets bewaard is, en laat de gebruiker een bestaande keuze
        # wissen door 'm leeg te maken (een `default` zou 'm terugzetten).
        value = defaults.get(key)
        return {"suggested_value": value} if value else {}

    def _entity(key, **cfg):
        return (
            vol.Optional(key, description=_prefill(key)),
            EntitySelector(EntitySelectorConfig(**cfg)),
        )

    schema: dict = {
        vol.Required(
            CONF_BATTERY_ENABLED,
            default=bool(defaults.get(CONF_BATTERY_ENABLED, False)),
        ): bool,
    }
    # Alleen op domein filteren, bewust niet op device_class. Omvormer- en
    # bridge-integraties zetten die lang niet altijd: de Marstek-SoC via een
    # LilyGO-RS485-bridge heeft alleen `unit_of_measurement: %`, en met een
    # `device_class: battery`-filter is 'ie domweg niet te kiezen. Een langere
    # (doorzoekbare) lijst is beter dan een sensor die je niet kúnt aanwijzen;
    # de push leest de waarde toch zelf uit en valideert 'm daar.
    for key, selector in [
        _entity(CONF_BATTERY_SOC, domain="sensor"),
        _entity(CONF_BATTERY_POWER, domain="sensor"),
        _entity(CONF_BATTERY_CHARGE_POWER, domain="sensor"),
        _entity(CONF_BATTERY_DISCHARGE_POWER, domain="sensor"),
        _entity(CONF_BATTERY_CHARGED_TOTAL, domain="sensor"),
        _entity(CONF_BATTERY_DISCHARGED_TOTAL, domain="sensor"),
        _entity(CONF_BATTERY_TEMP, domain="sensor"),
        _entity(CONF_BATTERY_PV_POWER, domain="sensor"),
        _entity(CONF_BATTERY_MODE, domain=["sensor", "select"]),
    ]:
        schema[key] = selector

    schema[vol.Required(
        CONF_BATTERY_INVERT_POWER,
        default=bool(defaults.get(CONF_BATTERY_INVERT_POWER, False)),
    )] = bool
    schema[vol.Optional(
        CONF_BATTERY_INTERVAL,
        default=max(MIN_BATTERY_INTERVAL, min(MAX_BATTERY_INTERVAL, int(
            defaults.get(CONF_BATTERY_INTERVAL, DEFAULT_BATTERY_INTERVAL)
        ))),
    )] = vol.All(vol.Coerce(int), vol.Range(min=MIN_BATTERY_INTERVAL, max=MAX_BATTERY_INTERVAL))

    for key in BATTERY_TEXT_FIELDS:
        value = defaults.get(key)
        schema[vol.Optional(
            key, description={"suggested_value": value} if value else {}
        )] = str
    capacity = defaults.get(CONF_BATTERY_CAPACITY)
    schema[vol.Optional(
        CONF_BATTERY_CAPACITY,
        description={"suggested_value": capacity} if capacity else {},
    )] = vol.All(vol.Coerce(float), vol.Range(min=0, max=1000))
    return schema


def _validate_battery(user_input: dict) -> tuple[dict, dict]:
    """→ (opgeschoonde config, errors). Leeg dict aan config = uitgeschakeld."""
    if not user_input.get(CONF_BATTERY_ENABLED):
        # Uitgeschakeld: bewaar de vlag, gooi de rest niet weg — wie 'm later
        # weer aanzet vindt zijn entity-keuzes terug.
        cleaned = {k: v for k, v in user_input.items() if v not in ("", None)}
        cleaned[CONF_BATTERY_ENABLED] = False
        return cleaned, {}

    errors: dict[str, str] = {}
    if not user_input.get(CONF_BATTERY_SOC):
        errors[CONF_BATTERY_SOC] = "battery_soc_required"
    has_signed = bool(user_input.get(CONF_BATTERY_POWER))
    has_split = bool(
        user_input.get(CONF_BATTERY_CHARGE_POWER)
        or user_input.get(CONF_BATTERY_DISCHARGE_POWER)
    )
    if not (has_signed or has_split):
        errors[CONF_BATTERY_POWER] = "battery_power_required"
    if errors:
        return {}, errors

    cleaned = {k: v for k, v in user_input.items() if v not in ("", None)}
    cleaned[CONF_BATTERY_ENABLED] = True
    return cleaned, {}


class SlimHuysConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step setup wizard."""

    _p1_data: dict[str, Any] = {}

    VERSION = 2

    def __init__(self) -> None:
        self._suppliers: list[dict[str, Any]] = []
        self._base_url: str = DEFAULT_BASE_URL
        self._api_key: str | None = None
        self._user_email: str | None = None
        self._supplier: str = DEFAULT_SUPPLIER
        self._has_p1_meter: bool = False
        self._mode: str = P1_MODE_NONE

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Stap 1: API-key + base URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).rstrip("/")
            self._api_key = user_input[CONF_API_KEY].strip()

            session = async_get_clientsession(self.hass)
            client = SlimHuysClient(session, self._base_url, self._api_key)
            try:
                profile = await client.whoami()
                self._user_email = profile.get("email")
                # `has_p1_meter` bepaalt de default-keuze op de mode-step.
                self._has_p1_meter = bool(profile.get("has_p1_meter"))
                self._suppliers = await client.suppliers()
            except SlimHuysAuthError:
                errors["base"] = "invalid_auth"
            except SlimHuysApiError as err:
                _LOGGER.warning("SlimHuys API onbereikbaar: %s", err)
                errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(self._user_email)
                self._abort_if_unique_id_configured()
                return await self.async_step_supplier()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    # API-key is een secret: masker 'm in de UI i.p.v. plain text.
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                }
            ),
            errors=errors,
        )

    async def async_step_supplier(self, user_input: dict[str, Any] | None = None):
        """Stap 2: leverancier kiezen."""
        if user_input is not None:
            self._supplier = user_input[CONF_SUPPLIER]
            return await self.async_step_p1_mode()

        choices = {s["id"]: s["name"] for s in self._suppliers if s.get("active", True)}
        if not choices:
            choices = {DEFAULT_SUPPLIER: "Frank Energie"}

        return self.async_show_form(
            step_id="supplier",
            data_schema=vol.Schema(
                {vol.Required(CONF_SUPPLIER, default=DEFAULT_SUPPLIER): vol.In(choices)}
            ),
        )

    async def async_step_p1_mode(self, user_input: dict[str, Any] | None = None):
        """Stap 3: kies P1-bron (none/push/pull). Default volgt `has_p1_meter`."""
        if user_input is not None:
            self._mode = user_input[CONF_P1_MODE]
            if self._mode == P1_MODE_PULL:
                return await self.async_step_p1_pull()
            if self._mode == P1_MODE_PUSH:
                return await self.async_step_p1_push()
            return await self._continue_to_battery({CONF_P1_MODE: P1_MODE_NONE})

        default_mode = P1_MODE_PULL if self._has_p1_meter else P1_MODE_PUSH
        return self.async_show_form(
            step_id="p1_mode",
            data_schema=vol.Schema({
                vol.Required(CONF_P1_MODE, default=default_mode): vol.In({
                    P1_MODE_NONE: "none",
                    P1_MODE_PUSH: "push",
                    P1_MODE_PULL: "pull",
                }),
            }),
            description_placeholders={
                "has_p1_meter": "ja" if self._has_p1_meter else "nee",
            },
        )

    async def async_step_p1_pull(self, user_input: dict[str, Any] | None = None):
        """Stap 4a (pull-mode): polling-fallback + probe-toggles. Geen DSMR-dropdowns."""
        if user_input is not None:
            return await self._continue_to_battery({
                CONF_P1_MODE: P1_MODE_PULL,
                CONF_PULL_POLL_FALLBACK: bool(user_input.get(
                    CONF_PULL_POLL_FALLBACK, DEFAULT_PULL_POLL_FALLBACK
                )),
                CONF_PULL_PROBE_AT_SETUP: bool(user_input.get(
                    CONF_PULL_PROBE_AT_SETUP, DEFAULT_PULL_PROBE_AT_SETUP
                )),
            })

        return self.async_show_form(
            step_id="p1_pull",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_PULL_POLL_FALLBACK, default=DEFAULT_PULL_POLL_FALLBACK,
                ): bool,
                vol.Required(
                    CONF_PULL_PROBE_AT_SETUP, default=DEFAULT_PULL_PROBE_AT_SETUP,
                ): bool,
            }),
        )

    async def async_step_p1_push(self, user_input: dict[str, Any] | None = None):
        """Stap 4b (push-mode): DSMR-sensors selecteren — bestaande logica."""
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_P1_MODE: P1_MODE_PUSH,
                CONF_P1_ENABLED: True,
                CONF_P1_CONSUMPTION: user_input.get(CONF_P1_CONSUMPTION),
                CONF_P1_DELIVERY: user_input.get(CONF_P1_DELIVERY),
                CONF_P1_POWER: user_input.get(CONF_P1_POWER),
                CONF_P1_INTERVAL: int(user_input.get(CONF_P1_INTERVAL, DEFAULT_P1_INTERVAL)),
            }
            for opt_key in (
                CONF_P1_VOLTAGE_L1, CONF_P1_VOLTAGE_L2, CONF_P1_VOLTAGE_L3,
                CONF_P1_CURRENT_L1, CONF_P1_CURRENT_L2, CONF_P1_CURRENT_L3,
                CONF_P1_POWER_L1, CONF_P1_POWER_L2, CONF_P1_POWER_L3,
                CONF_P1_POWER_RETURNED_L1, CONF_P1_POWER_RETURNED_L2, CONF_P1_POWER_RETURNED_L3,
                CONF_P1_POWER_RETURNED, CONF_P1_GAS,
            ):
                if user_input.get(opt_key):
                    data[opt_key] = user_input[opt_key]
            return await self._continue_to_battery(data)

        suggestions = _detect_dsmr_sensors(self.hass)
        energy_sensors = _energy_sensors(self.hass)
        power_sensors = _power_sensors(self.hass)
        voltage_sensors = _voltage_sensors(self.hass)
        current_sensors = _current_sensors(self.hass)
        gas_sensors = _gas_sensors(self.hass)

        energy_choices = {s: s for s in energy_sensors}
        power_choices = {s: s for s in power_sensors}
        voltage_choices = {s: s for s in voltage_sensors}
        current_choices = {s: s for s in current_sensors}
        gas_choices = {s: s for s in gas_sensors}

        default_consumption = next(
            (s for s in suggestions["consumption"] if s in energy_sensors), None
        ) or (energy_sensors[0] if energy_sensors else None)
        default_delivery = next(
            (s for s in suggestions["delivery"] if s in energy_sensors), None
        ) or (energy_sensors[1] if len(energy_sensors) > 1 else None)
        default_power = next(
            (s for s in suggestions["power"] if s in power_sensors), None
        ) or (power_sensors[0] if power_sensors else None)

        schema_dict: dict[Any, Any] = {}
        if energy_choices and power_choices:
            schema_dict.update({
                vol.Required(CONF_P1_CONSUMPTION, default=default_consumption or vol.UNDEFINED): vol.In(energy_choices),
                vol.Required(CONF_P1_DELIVERY, default=default_delivery or vol.UNDEFINED): vol.In(energy_choices),
                vol.Required(CONF_P1_POWER, default=default_power or vol.UNDEFINED): vol.In(power_choices),
                vol.Optional(CONF_P1_INTERVAL, default=DEFAULT_P1_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_P1_INTERVAL, max=MAX_P1_INTERVAL)
                ),
            })
            _add_optional_phase_fields(
                schema_dict,
                voltage_choices, current_choices, power_choices, gas_choices,
                voltage_sensors, current_sensors, power_sensors, gas_sensors,
            )

        return self.async_show_form(
            step_id="p1_push",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "energy_count": str(len(energy_sensors)),
                "power_count": str(len(power_sensors)),
            },
        )

    async def _continue_to_battery(self, p1_data: dict[str, Any]):
        """Laatste stap, ongeacht P1-mode — een batterij staat daar los van.

        Geen batterij-achtige sensor in dit huis? Dan de stap overslaan; een
        leeg formulier is alleen maar een extra klik in de wizard.
        """
        self._p1_data = p1_data
        if not _has_battery_candidates(self.hass):
            return self._finish_entry(p1_data)
        return await self.async_step_battery()

    async def async_step_battery(self, user_input: dict[str, Any] | None = None):
        """Stap 5: thuisbatterij → SlimHuys (optioneel)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            battery_data, errors = _validate_battery(user_input)
            if not errors:
                return self._finish_entry({**self._p1_data, **battery_data})

        return self.async_show_form(
            step_id="battery",
            data_schema=vol.Schema(_battery_schema(user_input or {})),
            errors=errors,
        )

    def _finish_entry(self, p1_data: dict[str, Any]):
        return self.async_create_entry(
            title=f"SlimHuys ({self._user_email})",
            data={
                CONF_API_KEY: self._api_key,
                CONF_BASE_URL: self._base_url,
                CONF_SUPPLIER: self._supplier,
                **p1_data,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SlimHuysOptionsFlow()


class SlimHuysOptionsFlow(config_entries.OptionsFlow):
    """Wijzig leverancier + P1-mode achteraf.

    HA 2024.11+ stelt self.config_entry automatisch in — geen __init__
    nodig (en de oude pattern conflicteert met de nieuwe property).
    Vandaar de minimum-versie in hacs.json.
    """

    def __init__(self) -> None:
        self._supplier_choice: str | None = None
        self._mode_choice: str | None = None
        self._p1_data: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Stap 1: leverancier + mode in één form."""
        entry = self.config_entry

        try:
            session = async_get_clientsession(self.hass)
            client = SlimHuysClient(
                session, entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            )
            suppliers = await client.suppliers()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("OptionsFlow kon suppliers niet laden: %s", err)
            suppliers = []

        choices = {s["id"]: s["name"] for s in suppliers if s.get("active", True)} or {
            DEFAULT_SUPPLIER: "Frank Energie"
        }

        def get(key, default=None):
            return entry.options.get(key, entry.data.get(key, default))

        if user_input is not None:
            self._supplier_choice = user_input[CONF_SUPPLIER]
            self._mode_choice = user_input[CONF_P1_MODE]
            if self._mode_choice == P1_MODE_PULL:
                return await self.async_step_p1_pull()
            if self._mode_choice == P1_MODE_PUSH:
                return await self.async_step_p1_push()
            return await self._continue_to_battery({})

        current_supplier = get(CONF_SUPPLIER, DEFAULT_SUPPLIER)
        current_mode = get(CONF_P1_MODE, P1_MODE_PUSH if get(CONF_P1_ENABLED) else P1_MODE_NONE)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SUPPLIER, default=current_supplier): vol.In(choices),
                vol.Required(CONF_P1_MODE, default=current_mode): vol.In({
                    P1_MODE_NONE: "none",
                    P1_MODE_PUSH: "push",
                    P1_MODE_PULL: "pull",
                }),
            }),
        )

    async def async_step_p1_pull(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return await self._continue_to_battery({
                CONF_PULL_POLL_FALLBACK: bool(user_input.get(
                    CONF_PULL_POLL_FALLBACK, DEFAULT_PULL_POLL_FALLBACK
                )),
                CONF_PULL_PROBE_AT_SETUP: bool(user_input.get(
                    CONF_PULL_PROBE_AT_SETUP, DEFAULT_PULL_PROBE_AT_SETUP
                )),
            })

        entry = self.config_entry

        def get(key, default=None):
            return entry.options.get(key, entry.data.get(key, default))

        return self.async_show_form(
            step_id="p1_pull",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_PULL_POLL_FALLBACK,
                    default=bool(get(CONF_PULL_POLL_FALLBACK, DEFAULT_PULL_POLL_FALLBACK)),
                ): bool,
                vol.Required(
                    CONF_PULL_PROBE_AT_SETUP,
                    default=bool(get(CONF_PULL_PROBE_AT_SETUP, DEFAULT_PULL_PROBE_AT_SETUP)),
                ): bool,
            }),
        )

    async def async_step_p1_push(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry

        def get(key, default=None):
            return entry.options.get(key, entry.data.get(key, default))

        if user_input is not None:
            cleaned = {k: v for k, v in user_input.items() if v != ""}
            cleaned[CONF_P1_ENABLED] = True
            return await self._continue_to_battery(cleaned)

        energy_sensors = _energy_sensors(self.hass)
        power_sensors = _power_sensors(self.hass)
        voltage_sensors = _voltage_sensors(self.hass)
        current_sensors = _current_sensors(self.hass)
        gas_sensors = _gas_sensors(self.hass)
        energy_choices = {s: s for s in energy_sensors}
        power_choices = {s: s for s in power_sensors}
        voltage_choices = {s: s for s in voltage_sensors}
        current_choices = {s: s for s in current_sensors}
        gas_choices = {s: s for s in gas_sensors}

        def safe_default(value, choices):
            return value if value in choices else vol.UNDEFINED

        current_p1_consumption = get(CONF_P1_CONSUMPTION)
        current_p1_delivery = get(CONF_P1_DELIVERY)
        current_p1_power = get(CONF_P1_POWER)
        # Clamp de prefill op het schema-bereik, anders ketst het formulier
        # af op zijn eigen ingevulde waarde bij opslaan.
        current_p1_interval = max(
            MIN_P1_INTERVAL,
            min(MAX_P1_INTERVAL, int(get(CONF_P1_INTERVAL, DEFAULT_P1_INTERVAL))),
        )

        schema_dict: dict[Any, Any] = {}
        if energy_choices and power_choices:
            schema_dict.update({
                vol.Required(CONF_P1_CONSUMPTION, default=safe_default(current_p1_consumption, energy_choices)): vol.In(energy_choices),
                vol.Required(CONF_P1_DELIVERY, default=safe_default(current_p1_delivery, energy_choices)): vol.In(energy_choices),
                vol.Required(CONF_P1_POWER, default=safe_default(current_p1_power, power_choices)): vol.In(power_choices),
                vol.Optional(CONF_P1_INTERVAL, default=current_p1_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_P1_INTERVAL, max=MAX_P1_INTERVAL)
                ),
            })
            optional_defaults = {
                k: v for k in (
                    CONF_P1_VOLTAGE_L1, CONF_P1_VOLTAGE_L2, CONF_P1_VOLTAGE_L3,
                    CONF_P1_CURRENT_L1, CONF_P1_CURRENT_L2, CONF_P1_CURRENT_L3,
                    CONF_P1_POWER_L1, CONF_P1_POWER_L2, CONF_P1_POWER_L3,
                    CONF_P1_POWER_RETURNED_L1, CONF_P1_POWER_RETURNED_L2, CONF_P1_POWER_RETURNED_L3,
                    CONF_P1_POWER_RETURNED, CONF_P1_GAS,
                ) if (v := get(k))
            }
            _add_optional_phase_fields(
                schema_dict,
                voltage_choices, current_choices, power_choices, gas_choices,
                voltage_sensors, current_sensors, power_sensors, gas_sensors,
                defaults=optional_defaults,
            )

        return self.async_show_form(
            step_id="p1_push",
            data_schema=vol.Schema(schema_dict),
        )

    async def _continue_to_battery(self, p1_data: dict[str, Any]):
        self._p1_data = p1_data
        # Anders dan bij de eerste setup slaan we de stap niet over als er
        # geen kandidaat-sensor is: wie de batterij ooit ingesteld heeft moet
        # 'm ook kunnen uitzetten wanneer de omvormer offline staat.
        entry = self.config_entry
        configured = entry.options.get(
            CONF_BATTERY_ENABLED, entry.data.get(CONF_BATTERY_ENABLED, False)
        )
        if not configured and not _has_battery_candidates(self.hass):
            return self._save_options(p1_data)
        return await self.async_step_battery()

    async def async_step_battery(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            battery_data, errors = _validate_battery(user_input)
            if not errors:
                return self._save_options({**self._p1_data, **battery_data})

        entry = self.config_entry

        def get(key, default=None):
            return entry.options.get(key, entry.data.get(key, default))

        defaults = user_input or {
            k: v for k in (
                CONF_BATTERY_ENABLED, CONF_BATTERY_INVERT_POWER, CONF_BATTERY_INTERVAL,
                CONF_BATTERY_CAPACITY, *BATTERY_ENTITY_FIELDS, *BATTERY_TEXT_FIELDS,
            ) if (v := get(k)) is not None
        }
        return self.async_show_form(
            step_id="battery",
            data_schema=vol.Schema(_battery_schema(defaults)),
            errors=errors,
        )

    def _save_options(self, p1_data: dict[str, Any]):
        return self.async_create_entry(
            title="",
            data={
                CONF_SUPPLIER: self._supplier_choice,
                CONF_P1_MODE: self._mode_choice,
                **p1_data,
            },
        )
