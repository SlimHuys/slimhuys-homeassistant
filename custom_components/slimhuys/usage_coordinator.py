"""Coordinator voor het `today`-blok uit `/v1/me/usage/current`.

Los van `SlimHuysLiveCoordinator`, en dat is bewust:

* de live-coordinator bestaat alleen in pull-mode en is push-driven (SSE);
  het `today`-blok komt daar niet doorheen — de SSE-stream emit alleen
  `reading`/`water-reading`/`water-leak`, geen dagtotalen.
* dagkosten wil je óók in push- en none-mode zien. Wie zijn P1 vanuit HA
  naar SlimHuys pusht heeft geen live-coordinator, maar wél een dagrekening.

Vandaar een eigen poll op hetzelfde endpoint. Eén request per 5 minuten is
verwaarloosbaar naast de 600/min die de key mag.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SlimHuysApiError, SlimHuysAuthError, SlimHuysClient
from .const import DOMAIN, USAGE_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SlimHuysUsageCoordinator(DataUpdateCoordinator):
    """Pollt `/v1/me/usage/current` en bewaart `today` + `solar` + `meter`.

    State-shape van `self.data`:

        { "today": {...} | None,
          "solar": {...} | None,
          "has_meter": bool }

    `today` is `None` zolang het huis geen meter én geen supplier-cloud-data
    heeft; de sensoren zijn dan `unavailable` in plaats van 0 — een lege
    dagrekening is geen dagrekening van nul.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: SlimHuysClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_usage",
            update_interval=USAGE_SCAN_INTERVAL,
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            snapshot = await self._client.current_usage()
        except SlimHuysAuthError as err:
            raise ConfigEntryAuthFailed("API-key revoked") from err
        except SlimHuysApiError as err:
            raise UpdateFailed(str(err)) from err

        snapshot = snapshot or {}
        return {
            "today": snapshot.get("today"),
            "solar": snapshot.get("solar"),
            "has_meter": bool(snapshot.get("has_meter")),
        }
