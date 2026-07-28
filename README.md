# SlimHuys — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Home Assistant-integratie voor [SlimHuys.nl](https://slimhuys.nl) — dynamische
stroomtarieven (EPEX day-ahead, NL) + push-bridge voor je P1/DSMR-meter.

## Wat krijg je?

**Vijftien prijssensoren per leverancier:**

| Sensor | Eenheid | Voorbeeld |
|---|---|---|
| `sensor.huidige_prijs` | EUR/kWh | `0.158` |
| `sensor.epex_kale_prijs` | EUR/kWh | `0.082` |
| `sensor.daggemiddelde` | EUR/kWh | `0.217` |
| `sensor.laagste_vandaag` | EUR/kWh | `0.089` |
| `sensor.hoogste_vandaag` | EUR/kWh | `0.464` |
| `sensor.goedkoopste_blok_start` | string | `"2026-04-30 02:00"` |
| `sensor.goedkoopste_blok_gemiddelde` | EUR/kWh | `0.094` |
| `sensor.volgende_negatieve_prijs` | string | `"2026-04-30 13:00"` |
| `sensor.tariefniveau_nu` | enum | `very_low / low / medium / high / peak` |
| `sensor.prijzen_vandaag` | EUR/kWh + `prices[24]` attr | `0.158` |
| `sensor.prijzen_morgen` | EUR/kWh + `prices[24]` attr | `0.187` of `unknown` |
| `sensor.prijzen_vandaag_kwartier` | EUR/kWh + `prices[96]` attr | `0.155` |
| `sensor.teruglevering_nu` | EUR/kWh | `0.071` |
| `sensor.teruglevering_vandaag` | EUR/kWh + `raw_today` attr | `0.071` |
| `sensor.teruglevering_morgen` | EUR/kWh + `raw_tomorrow` attr | `0.084` of `unknown` |

> **Entity-id's bij jou**: de integratie gebruikt `has_entity_name`, dus HA
> plakt de device-naam ervoor — `sensor.slimhuys_frank_energie_huidige_prijs`
> in plaats van `sensor.huidige_prijs`. In deze README staat overal de korte
> vorm; kijk je eigen id's na via **Developer tools → States** (filter op
> `slimhuys`) of hernoem de entiteiten naar de korte vorm.

### Prijsarrays voor dashboards

`prijzen_vandaag` en `prijzen_morgen` stellen de hele dag als attributen beschikbaar — compatibel met ApexCharts-Card, Energy Tariff Card en andere community-cards die de Nordpool/ENTSO-e-conventie volgen:

```yaml
attributes:
  prices: [0.18, 0.17, …]        # 24 all-in EUR/kWh
  raw_today:                       # voor ApexCharts e.d.
    - start: "2026-05-16T00:00:00+02:00"
      end:   "2026-05-16T01:00:00+02:00"
      value: 0.18
  raw_today_epex: [...]            # kale EPEX (zonder marge/btw/EB)
  average: 0.21
  min: 0.09
  max: 0.46
```

`prijzen_morgen` is `unknown` tot EPEX day-ahead publiceert (~14:00 CET) — attribuut `valid: true` zodra alle 24 uur beschikbaar zijn. `prijzen_vandaag_kwartier` levert de native granulariteit van de API (15-min op de meeste leveranciers, 60-min als fallback) — `granularity_minutes` attribuut geeft aan welke.

Plus **één service** voor terug-push naar SlimHuys:

```yaml
service: slimhuys.push_reading
data:
  consumption_kwh_total: "{{ states('sensor.dsmr_reading_electricity_consumption_total') | float }}"
  delivered_kwh_total:    "{{ states('sensor.dsmr_reading_electricity_delivery_total')    | float }}"
  active_power_w:         "{{ (states('sensor.dsmr_reading_current_electricity_usage')    | float * 1000) | int }}"
```

## Installeren

### Via HACS (aanbevolen)

1. Open HACS → 3-puntjes menu → **Custom repositories**
2. URL: `https://github.com/SlimHuys/slimhuys-homeassistant`
3. Categorie: **Integration**
4. **Add** → zoek "SlimHuys" → **Download**
5. Herstart Home Assistant
6. **Settings → Devices & Services → + Add Integration → SlimHuys**
7. Plak je API-key — die maak je aan op [slimhuys.nl/app/account?tab=api](https://slimhuys.nl/app/account?tab=api)

### Handmatig

Kopieer `custom_components/slimhuys/` naar je HA `config/custom_components/`-folder
en restart HA.

## P1-data: push of pull

Tijdens **Add Integration** kies je in stap 3 één van drie P1-modi:

| Modus | Richting | Wanneer kiezen |
|---|---|---|
| **none** | geen P1 | Je wilt alleen de prijs-sensors |
| **push** | HA → SlimHuys | Je hebt een DSMR-meter via USB / HomeWizard / Tibber Pulse en wilt die data delen met SlimHuys |
| **pull** | SlimHuys → HA | Je hebt een SlimHuys-P1-bridge die rechtstreeks aan SlimHuys is gekoppeld |

De wizard kiest een verstandige standaard op basis van je SlimHuys-account
(`has_p1_meter`-veld uit `/v1/me`): is er al een P1-bridge gekoppeld, dan
wordt pull als standaard geselecteerd, anders push.

### Push-modus — DSMR-data naar SlimHuys

In stap 4 detecteert de integratie automatisch mogelijke DSMR-sensors
en biedt dropdowns aan:

- Cumulatief verbruik (kWh)
- Cumulatieve teruglevering (kWh)
- Huidig vermogen (W of kW — wordt automatisch geconverteerd)

Plus een push-interval (1–300 seconden, default 30s). Sinds v0.3.0 is de
push **event-driven**: zodra je DSMR-meter een nieuwe waarde publiceert
gaat 'ie meteen naar SlimHuys (met throttling op het ingestelde interval).

**Optionele velden** (3-fase + gas) verschijnen automatisch onderin de
wizard als ze in je HA-instance bestaan.

> **1-seconde push** is sinds v0.2.0 mogelijk. DSMR-meters publiceren van nature
> elke ~1s; de SlimHuys-API rate-limit is 600/min/key (= 10/s) dus 1Hz uit één
> instance is comfortabel.

Werkt out-of-the-box met DSMR Slimme meter (USB), HomeWizard P1, en Tibber Pulse.

Wil je toch zelf via een automation pushen? De service `slimhuys.push_reading`
blijft beschikbaar voor maatwerk.

### Pull-modus — SlimHuys-P1 als bron voor HA

Heb je een P1-meter rechtstreeks aan SlimHuys gekoppeld (cellular, wifi)?
Dan vult pull-modus je HA met live entiteiten — geen DSMR-USB nodig.

Connectie: **Server-Sent Events** op `/v1/me/usage/live-events` — sub-seconde
latency, native HTTP, automatische reconnect met exponential backoff. Bij
SSE-uitval valt de integratie terug op `GET /v1/me/usage/current` (5s-poll).

Aangemaakte sensors:

| Entity | Eenheid | Device-class |
|---|---|---|
| `sensor.actief_vermogen` | W (afname, altijd ≥ 0) | power |
| `sensor.teruglevering_vermogen` | W (export, altijd ≥ 0) | power |
| `sensor.netto_vermogen` | W (signed: − = teruglevering) | power |
| `sensor.verbruik_totaal` | kWh (total_increasing) | energy |
| `sensor.teruglevering_totaal` | kWh (total_increasing) | energy |
| `sensor.spanning_l1/l2/l3` | V | voltage (L2/L3 als diagnostic) |
| `sensor.stroom_l1/l2/l3` | A | current (L2/L3 als diagnostic) |
| `sensor.vermogen_l1/l2/l3` | W (signed) | power |
| `sensor.gas_totaal` | m³ (total_increasing) | gas |
| `sensor.water_totaal` | L native, m³ display (total_increasing) | water |
| `sensor.waterdebiet` | L/min (measurement) | water |
| `sensor.temperatuur` | °C | temperature |
| `sensor.luchtvochtigheid` | % | humidity |

Afname en teruglevering zijn twee losse, altijd-positieve registers van de
meter (OBIS `1-0:1.7.0` en `1-0:2.7.0`) — géén splitsing van één netto-getal.
Op een 3-fase-aansluiting kunnen ze daarom **tegelijk** een waarde hebben:
exporteert je omvormer 4.234 W via L2 + L3 terwijl je kookplaat 1.379 W van
L1 trekt, dan zie je 1.379 W afname én 4.234 W teruglevering. Op 1-fase is er
altijd precies één van de twee nul. `sensor.actief_vermogen` wordt nooit
negatief, ook niet als je netto terugleeft.

`sensor.netto_vermogen` is het verschil van die twee: positief = van het net,
negatief = naar het net (zelfde conventie als `sensor.vermogen_l*`). Handig om
in één oogopslag de richting te zien, maar het is een lossy getal — je kunt de
twee bruto-waarden er niet uit terugrekenen. Voor kostenberekeningen gebruik je
`actief_vermogen` en `teruglevering_vermogen` los van elkaar, want afname en
teruglevering hebben verschillende tarieven.

3-fase entities worden alleen aangemaakt voor fasen die de meter rapporteert
(via een eenmalige `/current`-probe bij setup). 1-fase huishoudens krijgen
geen permanent-unavailable L2/L3-entities. Waterdebiet, temperatuur en
luchtvochtigheid verschijnen alleen als de gekoppelde meter die velden stuurt.

## Kant-en-klaar dashboard

Geen zin om zelf met grafieken te puzzelen? Hieronder staat een compleet
dashboard om te kopiëren. Plak het via **Overzicht → potlood → 3-puntjes →
Raw configuration editor** (of per kaart via **+ Kaart toevoegen → Handmatig**).

### Zonder extra downloads

Werkt met alleen de standaard-kaarten van Home Assistant:

```yaml
type: vertical-stack
cards:
  - type: heading
    heading: Stroomprijs
    heading_style: title

  - type: glance
    columns: 3
    entities:
      - entity: sensor.huidige_prijs
        name: Nu
      - entity: sensor.daggemiddelde
        name: Gemiddeld
      - entity: sensor.laagste_vandaag
        name: Laagste

  - type: history-graph
    hours_to_show: 24
    entities:
      - entity: sensor.huidige_prijs
        name: Prijsverloop

  - type: markdown
    content: >-
      **Goedkoopste blok vandaag:**
      {{ states('sensor.goedkoopste_blok_start') }} —
      € {{ states('sensor.goedkoopste_blok_gemiddelde') | float(0) | round(3) }}/kWh


      **Niveau nu:** {{ states('sensor.tariefniveau_nu') }}
      {% if states('sensor.volgende_negatieve_prijs') not in ['geen', 'unknown'] %}


      ⚡ **Negatieve prijs:** {{ states('sensor.volgende_negatieve_prijs') }}
      {% endif %}
```

De `history-graph` tekent het *werkelijke* verloop van vandaag (HA's eigen
recorder), dus morgen-prijzen zie je daar niet in. Wil je vandaag én morgen
in één staafgrafiek, gebruik dan de ApexCharts-variant hieronder.

### Met ApexCharts-card (vandaag + morgen in één grafiek)

Installeer eenmalig **ApexCharts Card** via HACS → Frontend → zoek
"apexcharts-card" → Download → herstart de browser (ctrl-shift-R).

```yaml
type: custom:apexcharts-card
experimental:
  color_threshold: true
header:
  show: true
  title: Stroomprijs vandaag & morgen
  show_states: true
  colorize_states: true
graph_span: 2d
span:
  start: day
now:
  show: true
  label: nu
yaxis:
  - decimals: 2
    apex_config:
      title:
        text: € / kWh
series:
  - entity: sensor.prijzen_vandaag
    name: Vandaag
    type: column
    curve: stepline
    unit: € /kWh
    float_precision: 3
    show:
      extremas: true
    color_threshold:
      - value: -0.001
        color: "#1b5e20"
      - value: 0
        color: "#2e7d32"
      - value: 0.25
        color: "#f9a825"
      - value: 0.40
        color: "#c62828"
    data_generator: |
      return (entity.attributes.raw_today || []).map(p => {
        return [new Date(p.start).getTime(), p.value];
      });
  - entity: sensor.prijzen_morgen
    name: Morgen
    type: column
    curve: stepline
    unit: € /kWh
    float_precision: 3
    opacity: 0.55
    color_threshold:
      - value: -0.001
        color: "#1b5e20"
      - value: 0
        color: "#2e7d32"
      - value: 0.25
        color: "#f9a825"
      - value: 0.40
        color: "#c62828"
    data_generator: |
      return (entity.attributes.raw_tomorrow || []).map(p => {
        return [new Date(p.start).getTime(), p.value];
      });
```

De morgen-reeks is leeg tot EPEX day-ahead publiceert (~14:00 CET) — de
grafiek toont dan simpelweg alleen vandaag, geen foutmelding.

**Liever kwartier-precisie?** Vervang de eerste reeks door
`sensor.prijzen_vandaag_kwartier` (zelfde `raw_today`-vorm, 96 punten) en
zet `graph_span: 1d`.

**Teruglevering erbij?** Voeg een derde reeks toe met
`entity: sensor.teruglevering_vandaag` — die heeft exact dezelfde
`raw_today`-structuur.

### Kale EPEX naast de all-in prijs

Handig om te zien hoeveel er aan belasting/btw/marge bovenop zit — beide
attributen zitten op dezelfde sensor:

```yaml
series:
  - entity: sensor.prijzen_vandaag
    name: All-in
    type: column
    data_generator: |
      return (entity.attributes.raw_today || []).map(p => [new Date(p.start).getTime(), p.value]);
  - entity: sensor.prijzen_vandaag
    name: Kale EPEX
    type: line
    curve: stepline
    stroke_width: 2
    data_generator: |
      return (entity.attributes.raw_today_epex || []).map(p => [new Date(p.start).getTime(), p.value]);
```

### Live P1-tegels (alleen in pull-modus)

```yaml
type: horizontal-stack
cards:
  - type: tile
    entity: sensor.netto_vermogen
    name: Nu
  - type: tile
    entity: sensor.teruglevering_vermogen
    name: Terug
  - type: tile
    entity: sensor.gas_totaal
    name: Gas
```

### Het SlimHuys-dashboard zelf in HA?

Een **webpagina-kaart** met `https://slimhuys.nl/app` werkt niet: slimhuys.nl
stuurt bewust `X-Frame-Options: DENY` mee om clickjacking te voorkomen, dus
je browser weigert het iframe. Een snelkoppeling kan wél:

```yaml
type: markdown
content: '[Open SlimHuys →](https://slimhuys.nl/app)'
```

## Configuratie wijzigen

Settings → Devices & Services → SlimHuys → **Configure** → wissel van leverancier
(Tibber, Frank, Zonneplan, ANWB, Eneco, NextEnergy, Coolblue, easyEnergy, Powerpeers).

## Meer over dynamische stroomprijzen

Geen Home Assistant bij de hand? Dezelfde data staat live op SlimHuys.nl,
doorgerekend met energiebelasting en btw:

- [Dynamische stroomprijs vandaag](https://slimhuys.nl/stroomprijzen-vandaag) — per kwartier, NL-zone
- [Stroomprijs morgen](https://slimhuys.nl/stroomprijzen-morgen) — de EPEX day-ahead-tarieven van morgen
- [Gemiddelde stroomprijs per maand en jaar](https://slimhuys.nl/gemiddelde-stroomprijs)
- [Negatieve stroomprijzen in Nederland](https://slimhuys.nl/negatieve-stroomprijzen)
- [Dynamische energieleveranciers vergelijken](https://slimhuys.nl/vergelijken) — Tibber, Frank Energie, Zonneplan en meer
- [Wanneer kan ik mijn auto het goedkoopst laden?](https://slimhuys.nl/wanneer-laden)

De tarieven komen uit de open dataset
[dynamic-tariffs-nl](https://github.com/SlimHuys/dynamic-tariffs-nl) en de
publieke API op `https://slimhuys.nl/v1/suppliers`.

## Licentie

MIT — zie [LICENSE](LICENSE).
