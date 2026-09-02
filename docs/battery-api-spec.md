# Spec: thuisbatterij-telemetrie in de SlimHuys API

**Repo:** `SlimHuys/slimhuys.nl` (Laravel) — deze spec beschrijft alleen de API-kant.
**Consument:** de Home Assistant-integratie (`SlimHuys/slimhuys-homeassistant`), die de
data pusht en later weer uitleest. Aanleiding: gebruiker heeft nu een GoodWe (via SEMS)
en krijgt binnenkort een Deye erbij.

**Wat er nu is:** niets aan telemetrie. `BatteryRoiController` en
`App\Domain\Battery\BatteryPotentialEstimator` zijn puur de *hypothetische*
ROI-rekenmodule ("wat zou een batterij opleveren"). Er is geen batterij-tabel,
geen ingest-endpoint, en geen batterij in `/v1/me/usage/current` of de SSE-stream.

Alles hieronder volgt bewust het bestaande P1-pad (`ReadingsController` +
`p1_meters`/`p1_readings_raw`/`p1_readings_quarter` + `P1AggregateCommand`), zodat
er geen tweede manier-van-doen bijkomt.

---

## 0. Conventies — graag éérst vastleggen

Deze twee keuzes bepalen de rest; ze zijn achteraf duur om te wijzigen.

**Vermogen is één signed getal:** `power_w`, **positief = laden** (energie de
batterij ín), **negatief = ontladen**. Reden: zowel GoodWe/SEMS als Deye leveren
van zichzelf één signed waarde, dus splitsen zou betekenen dat de client 'm eerst
uit elkaar haalt en de server 'm weer samenvoegt. De gesplitste gross-variant
(`charge_power_w`/`discharge_power_w`, beide ≥ 0) mag optioneel mee als een
inverter dat toevallig zo levert, maar `power_w` is de bron van waarheid.

> Let op de les uit `PhasePowerNormalizer`: dat ding bestaat alleen omdat er
> bridges in het wild zitten die een andere conventie hanteren dan het contract.
> Kies hier één conventie, documenteer 'm in de OpenAPI-spec, en valideer 'm.
> Bij `power_w` én beide gross-velden aanwezig: `power_w` wint, en log een
> `Log::debug` bij inconsistentie in plaats van een 422 — een client afserveren
> snijdt dat huis van álle batterijdata af.

**SoC is `soc_pct`, 0-100, één decimaal.** Niet 0-1, niet kWh. De absolute
energie-inhoud (`energy_kwh`) leidt de server zelf af uit `soc_pct × capacity_kwh`
als de client 'm niet meestuurt.

---

## 1. Migratie

Eén migratie, `database/migrations/YYYY_MM_DD_000001_create_battery_tables.php`.
ULID-primary-keys (`char(id, 26)`), FK op `households` met `cascadeOnDelete`,
net als `water_meters`.

### `batteries`

| kolom | type | opmerking |
|---|---|---|
| `id` | `char(26)` PK | ULID |
| `household_id` | `char(26)` | FK → `households`, cascade delete, index |
| `name` | `string(64)` nullable | vrij in te vullen, bijv. "Thuisbatterij zolder" |
| `brand` | `string(32)` nullable | `goodwe`, `deye`, … vrije string, geen enum |
| `model` | `string(64)` nullable | |
| `source` | `enum('ha_push','cloud','imported')` default `ha_push` | ruimte voor latere cloud-koppeling |
| `external_id` | `string(64)` nullable | serienummer/inverter-SN; uniek per household |
| `capacity_kwh` | `decimal(6,2)` nullable | bruikbare capaciteit |
| `max_charge_w` / `max_discharge_w` | `integer` nullable | |
| `connected_at` | `dateTime` useCurrent | |
| `last_reading_at` | `dateTime` nullable | |
| `status` | `enum('active','stale','disconnected')` default `disconnected` | zelfde semantiek als `p1_meters`: <5 min = active, <30 min = stale |
| `timestamps` | | |

Unique op `(household_id, external_id)`. Een huis mag meerdere batterijen hebben —
dat is het verschil met `p1_meters`, waar de MVP-aanname "max 1 per huis" geldt.
De Deye komt náást de GoodWe te staan, dus multi-batterij moet er vanaf dag 1 in.

### `battery_readings_raw`

30-90 dagen retentie, net als `p1_readings_raw`.

| kolom | type |
|---|---|
| `id` | `bigIncrements` |
| `battery_id` | `char(26)` FK → `batteries` cascade |
| `timestamp` | `dateTime` (NL-wallclock, voor display) |
| `timestamp_utc` | `dateTime` (eenduidig moment) |
| `soc_pct` | `decimal(5,2)` |
| `power_w` | `integer` (signed, + = laden) |
| `charged_kwh_total` | `decimal(12,3)` nullable (cumulatief) |
| `discharged_kwh_total` | `decimal(12,3)` nullable (cumulatief) |
| `energy_kwh` | `decimal(8,3)` nullable |
| `mode` | `string(24)` nullable (`auto`, `manual`, `idle`, `force_charge`, …) |
| `temp_c` | `decimal(4,1)` nullable |
| `pv_power_w` | `integer` nullable (zon-input op de hybride omvormer) |
| `grid_power_w` | `integer` nullable |

**UNIQUE op `(battery_id, timestamp_utc)`** — verplicht, want de HA-integratie
doet upsert-retries bij netwerk-timeouts, en de DST-fall-back-zondag heeft 02:00
NL twee keer. Index op `(battery_id, timestamp_utc)` (die komt gratis uit de unique).

### `battery_readings_quarter`

5 jaar retentie, één rij per `(household_id, battery_id, quarter_start)`.

| kolom | type |
|---|---|
| `id`, `household_id`, `battery_id` | |
| `quarter_start` + `quarter_start_utc` | `dateTime` — **beide**, en filter altijd op de UTC-kolom |
| `charged_kwh` / `discharged_kwh` | `decimal(8,4)` |
| `avg_power_w` | `integer` |
| `soc_start_pct` / `soc_end_pct` / `soc_min_pct` / `soc_max_pct` | `decimal(5,2)` |
| `charge_cost_eur` / `discharge_revenue_eur` | `decimal(8,4)` nullable |
| `source` | `enum('ha_push','cloud','imported','estimated')` |

Unique op `(household_id, battery_id, quarter_start_utc)`, index op
`(household_id, quarter_start_utc)`.

> De range-filters in `UsageController` draaien allemaal op `quarter_start_utc`
> omdat een DESC-sort op de niet-geïndexeerde wallclock-kolom de hele historie
> filesort'te (zie de comment bij `p1_readings_raw` in `current()`). Neem die
> kolom hier meteen mee in plaats van 'm later toe te voegen.

---

## 2. Ingest: `POST /v1/me/battery/readings`

Registreren in `routes/api.php` naast `/me/readings`, met exact dezelfde
middleware-stack en throttle-bucket:

```php
Route::middleware(['auth.apikey', 'auth.web', 'throttle:readings'])
    ->withoutMiddleware($statelessBridgeStrip)
    ->post('/me/battery/readings', [BatteryReadingsController::class, 'store']);
```

Nieuwe `App\Http\Controllers\Api\V1\BatteryReadingsController`, gemodelleerd naar
`ReadingsController::store`.

### Request

```json
{
  "battery": {
    "external_id": "5010KETU229W1234",
    "name": "Thuisbatterij",
    "brand": "goodwe",
    "model": "Lynx Home F",
    "capacity_kwh": 9.6,
    "max_charge_w": 5000,
    "max_discharge_w": 5000
  },
  "readings": [
    {
      "timestamp": "2026-09-02T14:31:00+02:00",
      "soc_pct": 78.5,
      "power_w": -1240,
      "charged_kwh_total": 412.338,
      "discharged_kwh_total": 388.104,
      "mode": "auto",
      "temp_c": 24.5,
      "pv_power_w": 2100
    }
  ]
}
```

### Validatie

Zelfde vorm als `ReadingsController`:

- `readings` — `required|array|min:1|max:1000`
- `readings.*.timestamp` — `required|string|regex:/[Zz]$|[+-]\d{2}:?\d{2}$/`
  (TZ-suffix verplicht; voorkomt dat een verkeerd geconfigureerde client
  TZ-loze strings in server-tz laat parsen)
- `readings.*.soc_pct` — `required|numeric|min:0|max:100`
- `readings.*.power_w` — `required|numeric` (**geen** `min:0`, signed)
- `readings.*.charged_kwh_total` / `discharged_kwh_total` — `nullable|numeric|min:0`
- `readings.*.energy_kwh` — `nullable|numeric|min:0`
- `readings.*.mode` — `nullable|string|max:24`
- `readings.*.temp_c` — `nullable|numeric|min:-50|max:100`
- `readings.*.pv_power_w` / `grid_power_w` — `nullable|numeric`
- `battery.*` — allemaal `nullable`, `external_id` `max:64`

### Gedrag

1. **Household-resolutie exact als `ReadingsController`:** bij Bearer-auth
   *verplicht* `api_key_household_id` (→ `ProblemDetails::unpinnedApiKey`), géén
   fallback naar `current_household_id`. Bij sessie-auth wél die fallback, plus
   `Household::hasWriteRole()` zodat een viewer geen batterij kan aanmaken.
2. **Auto-provisioning** zoals `resolveMeterId()`: bestaat er een batterij met dit
   `(household_id, external_id)` → gebruiken; anders aanmaken met de velden uit
   `battery`, en `AuditLogger::log('BATTERY_REGISTERED', …)`. Zonder `external_id`:
   pak de enige bestaande batterij van het huis, of maak er één aan. (Dat laatste
   is de reden dat `external_id` in de praktijk gewoon meegestuurd moet worden
   zodra er twee batterijen zijn — de HA-integratie doet dat.)
3. **Metadata-patch:** `capacity_kwh`, `max_charge_w`, `max_discharge_w`, `brand`,
   `model` bijwerken als ze meekomen — zoals de serienummers nu in `p1_meters`
   gepatcht worden.
4. **`DB::table('battery_readings_raw')->upsert($rows, ['battery_id','timestamp_utc'], [...])`**
   — upsert, niet insert, om retries idempotent te houden.
5. `batteries.last_reading_at` + `status = 'active'` bijwerken.
6. **Broadcast:** nieuw `App\Events\BatteryReadingReceived` naar het household-
   channel, met alléén de nieuwste reading uit de batch (niet de hele batch — een
   catch-up-batch na netwerkuitval zou anders 60+ events in seconden flooden;
   zie de comment bij `P1ReadingReceived`). Payload-keys identiek aan het
   `battery`-blok in `usage/current` hieronder, zodat REST en WebSocket dezelfde
   shape leveren.
7. **Response `202`:** `{"accepted": <int>, "battery_id": "<ulid>"}`

---

## 3. Uitlezen

### 3a. `battery`-blok in `GET /v1/me/usage/current`

Naast `live`, `today`, `solar`, `water`, `leak` komt er `batteries` (array, want
multi-batterij) bij. `null`/leeg array als het huis er geen heeft.

```json
"batteries": [
  {
    "id": "01J…",
    "name": "Thuisbatterij",
    "brand": "goodwe",
    "capacity_kwh": 9.6,
    "status": "active",
    "timestamp": "2026-09-02T14:31:00+02:00",
    "soc_pct": 78.5,
    "energy_kwh": 7.54,
    "power_w": -1240,
    "state": "discharging",
    "mode": "auto",
    "temp_c": 24.5,
    "today": {
      "charged_kwh": 6.2,
      "discharged_kwh": 5.1,
      "cycles": 0.65,
      "roundtrip_efficiency_pct": 82.3,
      "charge_cost_eur": 0.74,
      "discharge_revenue_eur": 1.63,
      "saved_eur": 0.89
    }
  }
]
```

- `state` is afgeleid: `charging` bij `power_w > 25`, `discharging` bij
  `power_w < -25`, anders `idle`. De dode zone van ±25 W voorkomt dat de UI
  staat te knipperen op standby-verbruik van de omvormer.
- `today.cycles` = `discharged_kwh / capacity_kwh`.
- `today.saved_eur` = `discharge_revenue_eur − charge_cost_eur`, gewaardeerd
  tegen het all-in afnametarief per kwartier via de bestaande
  `TariffTimelineBuilder`/`HouseholdContractResolver`-stack — dus dezelfde
  prijslogica als `computeCostAndRevenue()` in `UsageController`. **Niet**
  tegen kale EPEX; de besparing zit juist in belasting + btw.

### 3b. SSE-event `battery-reading`

In `UsageController::liveEvents()`, naast `reading` en `water-reading`. Zelfde
patroon: initiële snapshot direct emitten, daarna in de 1 Hz-loop alleen bij een
gewijzigde `timestamp`. Event-naam **`battery-reading`** — die mapping zit al
voorbereid in `live_coordinator.py` (`_handle_event`, de `stream_key`-dict).
Payload = één object uit de `batteries`-array hierboven (zonder het `today`-blok,
dat is te duur voor 1 Hz).

Voor de multi-batterij-situatie: één event per batterij, en `id` in de payload
is leidend voor de client.

### 3c. `GET /v1/me/battery/range?from=…&to=…&resolution=quarter|hour|day`

Historie uit `battery_readings_quarter`, zelfde query-parameters en response-vorm
als `/me/usage/range`, zodat de frontend-grafiekcomponent hergebruikt kan worden.
Per bucket: `charged_kwh`, `discharged_kwh`, `soc_end_pct`, `saved_eur`.

Dit mag in een tweede PR — de HA-integratie heeft 'm niet nodig, de webapp wel.

---

## 4. Aggregatie + retentie

- **`BatteryAggregateCommand`** (`battery:aggregate`), naast `P1AggregateCommand`.
  Rolt raw → kwartier: `charged_kwh`/`discharged_kwh` uit de delta van de
  cumulatieve totalen (met fallback op de integraal van `power_w` over de tijd
  als de client geen totalen levert), plus SoC min/max/start/eind en de
  kosten/opbrengst tegen de kwartier-tarieven.
  Inplannen in `routes/console.php` op hetzelfde ritme als `p1:aggregate`, met
  `withoutOverlapping()`.
- **`BatteryPruneRawCommand`** (`battery:prune-raw`), `dailyAt('03:25')` — vlak na
  `p1:prune-raw` om 03:20, zodat de twee prunes elkaar niet in de I/O zitten.

---

## 5. Tests

Volg de bestaande feature-test-opzet van de readings-endpoints:

- push met Bearer-key zonder household-pinning → `ProblemDetails::unpinnedApiKey`
- viewer-rol via sessie → 403
- eerste push auto-provisiont de batterij + audit-log-regel
- dezelfde batch 2× pushen → `accepted` telt door, maar rij-aantal blijft gelijk (upsert)
- DST-fall-back: twee readings op `02:30+02:00` en `02:30+01:00` → **twee** rijen
- `power_w` negatief wordt geaccepteerd (regressie op de signed-conventie)
- `soc_pct` 101 of −1 → 422
- `usage/current` geeft `state: charging|discharging|idle` correct rond de ±25 W-grens

---

## 6. Bewust buiten scope (voor nu)

**Aansturing** (laden/ontladen forceren vanuit SlimHuys). De infrastructuur
bestaat al — `DeviceActionClient`, `IntegrationDispatcher` en
`POST /me/devices/{provider}/{deviceId}/actions/{actionId}` — dus dit is later
een uitbreiding van het devices-concept, geen nieuw mechanisme. Eerst read-only
telemetrie stabiel krijgen.

**Waar het daarna interessant wordt:** met echte telemetrie kan
`BatteryRoiController` van hypothetisch naar gemeten ROI ("je batterij heeft je
deze maand €X bespaard"), door de contrafeitelijke kosten-zonder-batterij te
berekenen over dezelfde kwartieren. Dat is met afstand de sterkste feature die
uit deze data volgt, en een goede reden om `charge_cost_eur`/
`discharge_revenue_eur` meteen in het kwartier-aggregaat op te slaan in plaats
van ze achteraf te herberekenen.
