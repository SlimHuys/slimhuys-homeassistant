"""DataUpdateCoordinator that fetches prices for all SlimHuys sensors."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SlimHuysApiError, SlimHuysClient
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SlimHuysCoordinator(DataUpdateCoordinator):
    """Polls /v1/prices/* and aggregates today's hourly prices."""

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

            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_end = today_start + timedelta(days=2)
            range_resp = await self._client.price_range(
                self._supplier,
                today_start.strftime("%Y-%m-%dT%H:%M:%S"),
                tomorrow_end.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        except SlimHuysApiError as err:
            raise UpdateFailed(str(err)) from err

        points = range_resp.get("points", []) if range_resp else []
        hourly = self._aggregate_hourly(points)

        # Goedkoopste 3-uurs venster vanaf nu (vandaag of morgen)
        cheapest = self._find_cheapest_block(hourly, slots=3, start_idx=datetime.now().hour)

        # Negatieve uren — eerstvolgende
        negative = self._find_next_negative(hourly)

        data = {
            "current": current,
            "hourly": hourly,
            "points": points,
            "cheapest_block": cheapest,
            "next_negative": negative,
            "supplier": self._supplier,
            "fetched_at": datetime.now().isoformat(),
        }

        # Teruglevering — mag falen zonder de consume-sensoren te breken.
        data.update(
            await self._fetch_feedin(
                today_start.strftime("%Y-%m-%dT%H:%M:%S"),
                tomorrow_end.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        )
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
            "feedin_points": [],
            "feedin_hourly": [],
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
        return {
            "feedin_current": current if "feedin" in now else None,
            "feedin_points": points,
            "feedin_hourly": self._aggregate_feedin_hourly(points),
            "feedin_model": (range_resp or {}).get("feedin_model"),
        }

    @staticmethod
    def _aggregate_hourly(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate consume-points (`breakdown`) to 24+24 hour-buckets."""
        return SlimHuysCoordinator._aggregate(
            points,
            lambda p: (p.get("breakdown") or {}).get("total_eur_per_kwh"),
            lambda p: (p.get("breakdown") or {}).get("epex_eur_per_kwh"),
        )

    @staticmethod
    def _aggregate_feedin_hourly(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate feedin-points (`feedin`) to 24+24 hour-buckets.

        Zelfde bucket-vorm als consume (`price`/`epex`/`start_ts`) zodat de
        sensor-helpers ongewijzigd werken; `price` is hier de teruglever-rate.
        """
        return SlimHuysCoordinator._aggregate(
            points,
            lambda p: (p.get("feedin") or {}).get("feedin_eur_per_kwh"),
            lambda p: (p.get("feedin") or {}).get("epex_eur_per_kwh"),
        )

    @staticmethod
    def _aggregate(
        points: list[dict[str, Any]],
        get_price: Any,
        get_epex: Any,
    ) -> list[dict[str, Any]]:
        """Aggregate 15-min or 60-min points to 24+24 hour-buckets (today + tomorrow)."""
        buckets: dict[str, dict[int, dict[str, Any]]] = {}
        for p in points:
            ts = p.get("timestamp", "")
            if len(ts) < 13:
                continue
            price = get_price(p)
            if price is None:
                continue
            day = ts[:10]
            hour = int(ts[11:13])
            bucket = buckets.setdefault(day, {}).setdefault(
                hour, {"prices": [], "epex": [], "start_ts": ts}
            )
            bucket["prices"].append(price)
            epex = get_epex(p)
            if epex is not None:
                bucket["epex"].append(epex)

        result = []
        for day in sorted(buckets.keys()):
            for hour in range(24):
                b = buckets[day].get(hour)
                if b and b["prices"]:
                    avg = sum(b["prices"]) / len(b["prices"])
                    epex_avg = (
                        sum(b["epex"]) / len(b["epex"]) if b["epex"] else None
                    )
                    start_ts = b["start_ts"]
                else:
                    avg = None
                    epex_avg = None
                    start_ts = None
                result.append(
                    {
                        "day": day,
                        "hour": hour,
                        "price": avg,
                        "epex": epex_avg,
                        "start_ts": start_ts,
                    }
                )
        return result

    @staticmethod
    def _find_cheapest_block(
        hourly: list[dict[str, Any]],
        slots: int,
        start_idx: int = 0,
    ) -> dict[str, Any] | None:
        best = None
        for i in range(start_idx, len(hourly) - slots + 1):
            window = hourly[i : i + slots]
            if any(h["price"] is None for h in window):
                continue
            avg = sum(h["price"] for h in window) / slots
            if best is None or avg < best["avg"]:
                best = {
                    "start_day": window[0]["day"],
                    "start_hour": window[0]["hour"],
                    "end_hour": (window[0]["hour"] + slots) % 24,
                    "avg": avg,
                }
        return best

    @staticmethod
    def _find_next_negative(hourly: list[dict[str, Any]]) -> dict[str, Any] | None:
        now_hour = datetime.now().hour
        for i, h in enumerate(hourly):
            if i < now_hour:
                continue
            if h["price"] is not None and h["price"] < 0:
                return {"day": h["day"], "hour": h["hour"], "price": h["price"]}
        return None
