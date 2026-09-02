# Spec: zonnepanelen-productie in de SlimHuys API

**Repo:** `SlimHuys/slimhuys.nl` (Laravel) — deze spec beschrijft de API-kant.
**Consument:** de Home Assistant-integratie (`SlimHuys/slimhuys-homeassistant`),
die de opwek pusht en 'm via `/v1/me/usage/current` weer uitleest.

Anders dan [`battery-api-spec.md`](battery-api-spec.md) is dit geen vooraf-
ontwerp maar een **as-built**-beschrijving: API en integratie zijn in één keer
gebouwd (SlimHuys HA v1.11.0).

**Aanleiding.** Opwek kwam tot dan toe uitsluitend uit een cloud-koppeling met
de omvormer: GoodWe/SEMS, SolarEdge Monitoring of Enphase Enlighten, opgehaald
door `solar:fetch`. Wie een ander merk heeft — of z'n omvormer bewust niet aan
een cloud hangt — zag `sensor.opwek_vandaag` en
`sensor.eigen_verbruik_vandaag` leeg blijven, terwijl Home Assistant de
omvormer allang uitleest. `own_consumption_kwh` is server-side
`produced − delivered`, dus zonder opwek is die per definitie leeg.

---

## 0. Conventies

**Een station is géén eigen tabel.** In deze codebase is een PV-station een
entry in `user_integrations.metadata_json.stations`, met de provider-slug als
bron. De push-bron volgt dat: de eerste push auto-provisiont een
`user_integrations`-rij met provider **`ha`**. Daarmee werken
`SolarSnapshotService`, `ForecastService`, de forecast-calibratie en de
stations-extras in de SPA meteen mee. Welke slugs een zonbron zijn staat in
`App\Domain\Solar\SolarProviders`, niet als array-literal per bestand.

**Eén bron per huis.** Een huis met een actieve cloud-koppeling krijgt **409
`errors/solar-source-conflict`** op een push. `pv_readings_quarter` is per
`(household, source, station)` gesleuteld, dus dezelfde panelen via twee
bronnen tellen netjes op tot het dubbele — en dat is achteraf niet van een
terecht hoge opbrengst te onderscheiden. De integratie stopt op deze status
met pushen tot de gebruiker kiest, zelfde patroon als `ambiguous-battery`.

**`power_w` óf `produced_kwh_total`, minstens één per reading.** Vermogen
heeft de voorkeur: daarmee kan de server integreren én het piekvermogen
bepalen. `produced_kwh_total` is een levenslange teller en levert de
nauwkeurigste kwartier-delta.

---

## 1. Endpoint

```
POST /v1/me/solar/readings
Authorization: Bearer slh_…
```

Zelfde middleware-stack als `/me/readings`, `/me/water-readings` en
`/me/battery/readings` (`auth.apikey` + `auth.web` + `throttle:readings`),
zelfde household-pinning: een Bearer-key moet gepind zijn (anders **422
`errors/unpinned-key`**), sessie-auth valt terug op `current_household_id` en
weigert viewers met **403**.

```jsonc
{
  "station": {                       // optioneel, mag bij elke push mee
    "external_id": "dak-zuid",       // default "default"
    "name": "Dak zuid",
    "capacity_kwp": 5.4              // voedt de opbrengst-voorspelling
  },
  "readings": [                      // 1-1000
    {
      "timestamp": "2026-09-02T14:31:00+02:00",  // ISO-8601, TZ-suffix verplicht
      "power_w": 3120,                            // W, signed
      "produced_kwh_total": 8421.5                // levenslange teller, kWh
    }
  ]
}
```

**202** `{"accepted": 1, "station_id": "dak-zuid"}`.

**422 `errors/empty-solar-reading`** als een reading noch `power_w` noch
`produced_kwh_total` heeft.

`power_w` mag negatief zijn: sommige omvormers rapporteren 's nachts een paar
watt eigenverbruik als negatieve productie. Dat is geen reden om het bericht te
weigeren — de aggregator klemt op 0 in plaats van af te trekken, zodat de
dagopbrengst er niet door zakt.

Idempotent: UNIQUE op `(household_id, source, external_station_id,
timestamp_utc)`, dus een retry na een netwerk-timeout upsert over de eerdere
rij heen. De UTC-kolom (niet de NL-wallclock) is de sleutel, zodat de twee
02:30-readings op de DST-fall-back-zondag naast elkaar blijven staan.

---

## 2. Opslag en aggregatie

`pv_readings_raw` → `solar:aggregate` (elke minuut, `--lookback=6`) →
`pv_readings_quarter`. De upsert loopt via dezelfde `SolarReadingsImporter`
als de cloud-fetchers, zodat er maar één plek is die de UTC-kolom en de
UNIQUE-sleutel goed moet hebben. `solar:prune-raw` houdt de raw-buffer op 14
dagen.

`App\Domain\Solar\PvQuarterEnergy` bepaalt de kwartier-opwek:

1. **Teller-delta** (primair): laatste stand vóór het einde min de laatste
   stand vóór het begin. Pakt ook de energie mee die over de kwartier-grens
   loopt.
2. **Trapezium-integratie van `power_w`** (terugval): als de client geen
   teller stuurt, als er nog geen baseline is, of als de teller terugviel —
   dat laatste gebeurt bij een HA-restart die de `utility_meter` kwijtraakt en
   zou anders een negatieve delta opleveren.

Gap-tolerantie 420s en een dekkingseis van 50% van het venster, gelijk aan de
batterij-aggregator. `peak_power_w` komt altijd uit de samples, ook op de
teller-route — die kolom voedt de live-tegel op het dashboard.

Een kwartier **zonder samples levert geen rij**: 0 betekent "de omvormer stond
stil", een ontbrekende rij "we weten het niet", en dat verschil is precies wat
een gat in de dag-grafiek hoort te zijn.

---

## 3. Wat de integratie stuurt

Eén reading per push, state-change-driven met dezelfde throttle en
exponentiële backoff als de P1- en batterij-push, plus een hartslag-tick op het
ingestelde interval (default 30s). Die tick is hier net zo hard nodig als bij
de batterij: een omvormer die 's nachts op 0 W staat levert uren geen
state-change-event, en zonder tick mist het eerste kwartier na zonsopgang z'n
teller-baseline.

Eenheden worden client-side genormaliseerd: kW/MW → W, en Wh/MWh → kWh. Dat
laatste is geen randgeval — veel omvormer-integraties publiceren
`..._energy_total` in Wh, en die ongemoeid doorsturen zou de dagopbrengst met
een factor 1000 opblazen.
