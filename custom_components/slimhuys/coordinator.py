"""DataUpdateCoordinator that fetches prices for all SlimHuys sensors."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SlimHuysApiError, SlimHuysClient
from .const import (
    CHEAPEST_BLOCK_HOURS,
    DEFAULT_RESOLUTION_MINUTES,
    DOMAIN,
    SCAN_INTERVAL,
    TIMEZONE,
)

_LOGGER = logging.getLogger(__name__)

NL_TZ = ZoneInfo(TIMEZONE)


def nl_now() -> datetime:
    """Huidige tijd in Europe/Amsterdam — DST-correct."""
    return datetime.now(NL_TZ)


def parse_iso(ts: str | None) -> datetime | None:
    """ISO-8601 → aware datetime. Naïeve input geldt als NL-tijd.

    De API levert offsets (`+02:00`); alleen als die ontbreekt vullen we NL
    aan. De offset zelf blijft staan — normaliseren naar één tijdzone doen we
    pas bewust in `_build_slots`.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=NL_TZ) if dt.tzinfo is None else dt


def slot_index_now(slots: list[dict[str, Any]]) -> int | None:
    """Index van het slot waar 'nu' in valt, of `None` als er geen match is."""
    now = datetime.now(timezone.utc)
    for i, s in enumerate(slots):
        if s["start"] <= now < s["end"]:
            return i
    return None


def slots_for_day(
    slots: list[dict[str, Any]], day: str
) -> list[dict[str, Any]]:
    return [s for s in slots if s["day"] == day]


def day_is_complete(slots: list[dict[str, Any]]) -> bool:
    """Dekken deze slots een hele dag? Ondergrens 23u i.v.m. DST-krimpdag."""
    covered = sum(s["resolution_minutes"] for s in slots)
    return covered >= 23 * 60


class SlimHuysCoordinator(DataUpdateCoordinator):
    """Polls /v1/prices/* en bewaart de prijzen op hun eigen resolutie.

    Bewust géén aggregatie naar uren: de API levert per leverancier de
    resolutie waarop die factureert (`resolution_minutes`, 15 of 60). Een
    kwartier-leverancier hoort ook kwartierprijzen in HA te krijgen — anders
    middel je precies de prijspieken weg waarop je wilt schakelen.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: SlimHuysClient,
        supplier: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{supplier}",
            update_interval=SCAN_INTERVAL,
        )
        self._client = client
        self._supplier = supplier

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            current = await self._client.current_price(self._supplier)

            today_start = nl_now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_end = today_start + timedelta(days=2)
            from_iso = today_start.strftime("%Y-%m-%dT%H:%M:%S")
            to_iso = tomorrow_end.strftime("%Y-%m-%dT%H:%M:%S")
            range_resp = await self._client.price_range(
                self._supplier, from_iso, to_iso
            )
        except SlimHuysApiError as err:
            raise UpdateFailed(str(err)) from err

        points = range_resp.get("points", []) if range_resp else []
        resolution = self._resolution(range_resp, points)
        slots = self._build_consume_slots(points, resolution)

        data = {
            "current": current,
            "slots": slots,
            "resolution_minutes": resolution,
            "cheapest_block": self._find_cheapest_block(slots, CHEAPEST_BLOCK_HOURS),
            "next_negative": self._find_next_negative(slots),
            "supplier": self._supplier,
            "fetched_at": nl_now().isoformat(),
        }

        # Teruglevering — mag falen zonder de consume-sensoren te breken.
        data.update(await self._fetch_feedin(from_iso, to_iso))
        return data

    async def _fetch_feedin(self, from_iso: str, to_iso: str) -> dict[str, Any]:
        """Haal teruglever-rates op. Fail-safe: bij fout of oude API → leeg.

        De prijs-endpoints negeren `direction` zolang de API-deploy nog niet
        live is en geven dan de consume-`breakdown` terug. We herkennen echte
        feedin-data aan de aanwezigheid van de `feedin`-key en vallen anders
        stil terug op "geen teruglevering" — zo is deze release veilig te
        shippen vóór de backend-deploy.
        """
        empty = {
            "feedin_current": None,
            "feedin_slots": [],
            "feedin_resolution_minutes": DEFAULT_RESOLUTION_MINUTES,
            "feedin_model": None,
        }
        try:
            current = await self._client.current_price(
                self._supplier, direction="feedin"
            )
            range_resp = await self._client.price_range(
                self._supplier, from_iso, to_iso, direction="feedin"
            )
        except SlimHuysApiError as err:
            _LOGGER.debug("teruglevering ophalen mislukt: %s", err)
            return empty

        points = (range_resp or {}).get("points", []) or []
        if not (points and "feedin" in points[0]):
            # Oude API (nog geen direction-support) → geen feedin-data
            return empty

        now = (current or {}).get("now") or {}
        resolution = self._resolution(range_resp, points)
        return {
            "feedin_current": current if "feedin" in now else None,
            "feedin_slots": self._build_feedin_slots(points, resolution),
            "feedin_resolution_minutes": resolution,
            "feedin_model": (range_resp or {}).get("feedin_model"),
        }

    @staticmethod
    def _resolution(range_resp: dict[str, Any] | None, points: list[dict]) -> int:
        """Resolutie in minuten, uit de API zelf (bereik-veld, anders 1e punt).

        Nooit afleiden uit het aantal punten: 24 uurprijzen × 2 dagen ziet er
        precies zo uit als één dag kwartierprijzen.
        """
        for src in ((range_resp or {}), *points[:1]):
            res = src.get("resolution_minutes")
            if isinstance(res, (int, float)) and res > 0:
                return int(res)
        return DEFAULT_RESOLUTION_MINUTES

    @staticmethod
    def _build_consume_slots(
        points: list[dict[str, Any]], resolution: int
    ) -> list[dict[str, Any]]:
        """Consume-punten (`breakdown`) → slots op eigen resolutie."""
        return SlimHuysCoordinator._build_slots(
            points,
            lambda p: (p.get("breakdown") or {}).get("total_eur_per_kwh"),
            lambda p: (p.get("breakdown") or {}).get("epex_eur_per_kwh"),
            resolution,
        )

    @staticmethod
    def _build_feedin_slots(
        points: list[dict[str, Any]], resolution: int
    ) -> list[dict[str, Any]]:
        """Feedin-punten (`feedin`) → slots met dezelfde vorm als consume.

        `price` is hier de teruglever-rate, zodat de sensor-helpers voor beide
        richtingen ongewijzigd werken.
        """
        return SlimHuysCoordinator._build_slots(
            points,
            lambda p: (p.get("feedin") or {}).get("feedin_eur_per_kwh"),
            lambda p: (p.get("feedin") or {}).get("epex_eur_per_kwh"),
            resolution,
        )

    @staticmethod
    def _build_slots(
        points: list[dict[str, Any]],
        get_price: Any,
        get_epex: Any,
        resolution: int,
    ) -> list[dict[str, Any]]:
        """Eén slot per API-punt, chronologisch, zonder gaten op te vullen.

        `start`/`end` staan in UTC, `*_local` en `day`/`hour`/`minute` in
        NL-tijd. Die splitsing is niet cosmetisch: Python trekt twee datetimes
        met dezelfde tzinfo op wandkloktijd van elkaar af, dus in één ZoneInfo
        lijkt de DST-sprong 01:00→03:00 twee uur te duren. Alle rekenwerk
        (contiguïteit, "is dit slot al voorbij") gaat daarom over UTC.
        """
        slots: list[dict[str, Any]] = []
        for p in points:
            price = get_price(p)
            start = parse_iso(p.get("timestamp"))
            if price is None or start is None:
                continue
            res = int(p.get("resolution_minutes") or resolution)
            end = parse_iso(p.get("valid_until")) or start + timedelta(minutes=res)
            start_local = start.astimezone(NL_TZ)
            end_local = end.astimezone(NL_TZ)
            slots.append(
                {
                    "day": start_local.strftime("%Y-%m-%d"),
                    "hour": start_local.hour,
                    "minute": start_local.minute,
                    "price": price,
                    "epex": get_epex(p),
                    "start": start.astimezone(timezone.utc),
                    "end": end.astimezone(timezone.utc),
                    "end_local": end_local,
                    "start_ts": start_local.isoformat(),
                    "end_ts": end_local.isoformat(),
                    "resolution_minutes": res,
                }
            )
        slots.sort(key=lambda s: s["start"])
        return slots

    @staticmethod
    def _find_cheapest_block(
        slots: list[dict[str, Any]], hours: int = CHEAPEST_BLOCK_HOURS
    ) -> dict[str, Any] | None:
        """Goedkoopste aaneengesloten venster van `hours` uur, vanaf nu.

        Schuift per slot op, niet per uur: bij een kwartier-leverancier vind
        je dus ook blokken die om :15 beginnen. Vensters met een gat in de
        reeks (ontbrekende prijzen) worden overgeslagen.
        """
        now = datetime.now(timezone.utc)
        best = None
        for i, first in enumerate(slots):
            if first["end"] <= now:
                continue  # blok zou volledig in het verleden starten
            res = first["resolution_minutes"] or DEFAULT_RESOLUTION_MINUTES
            count = max(1, round(hours * 60 / res))
            window = slots[i : i + count]
            if len(window) < count:
                continue
            step = timedelta(minutes=res)
            if any(
                window[k]["start"] - first["start"] != k * step for k in range(count)
            ):
                continue
            avg = sum(s["price"] for s in window) / count
            if best is None or avg < best["avg"]:
                last = window[-1]
                best = {
                    "start_day": first["day"],
                    "start_hour": first["hour"],
                    "start_minute": first["minute"],
                    "start_ts": first["start_ts"],
                    "end_hour": last["end_local"].hour,
                    "end_minute": last["end_local"].minute,
                    "end_ts": last["end_ts"],
                    "avg": avg,
                    "duration_hours": hours,
                }
        return best

    @staticmethod
    def _find_next_negative(slots: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Eerste nog niet verstreken slot met een negatieve totaalprijs."""
        now = datetime.now(timezone.utc)
        for s in slots:
            if s["end"] <= now:
                continue
            if s["price"] < 0:
                return {
                    "day": s["day"],
                    "hour": s["hour"],
                    "minute": s["minute"],
                    "price": s["price"],
                    "start_ts": s["start_ts"],
                }
        return None
