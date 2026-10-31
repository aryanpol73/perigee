# Screening validation against CelesTrak SOCRATES (Phase 3)

Generated 2026-10-31 19:09 UTC by `scripts/validate_screening.py`.

All results are estimated close approaches based on public orbit data, not collision predictions.

## Runs compared

|  | Perigee | SOCRATES |
|---|---|---|
| Catalog snapshot | `20261031T144946Z` | `sort-minRange_20261031T164658Z.csv` |
| Objects | 20,087 (CelesTrak GP groups) | full public catalog |
| Scope | active satellites vs all objects | active satellites vs all objects |
| Threshold / window | 5 km / 7 days | 5 km / 7 days |
| Newest element set used | 2026-10-31 11:57 UTC | 2026-10-30 22:00 UTC |
| Propagator | Python `sgp4` 2.27 | SGP4 in STK 12.2 |
| Events (full window) | 869,584 | 192,495 |

Comparison window (overlap of both runs, 5 min trimmed at each end): 2026-10-31 14:54 to 2026-11-07 01:54 UTC. Events match when they involve the same pair and their times of closest approach (TCA) are within 60 s.

## Headline: how much of SOCRATES does Perigee find?

| SOCRATES events | Count | Found by Perigee |
|---|---|---|
| All SOCRATES events | 179,271 | 19.4% |
| Both objects in Perigee's catalog | 142,836 | 24.4% |
| Identical inputs (both element sets the same) | 4,149 | **100.0%** |
| High-risk: SOCRATES min range < 1 km | 9,538 | 33.0% |
| High-risk, both objects in catalog | 8,030 | 39.1% |
| High-risk, identical inputs | 693 | 100.0% |

**Identical inputs** is the test of the screening algorithm: SOCRATES publishes each object's days-since-epoch, so its exact element set is known, and wherever both objects' sets equal ours the two programs had the same inputs. The other rows also measure catalog coverage and data age.

## Why SOCRATES events were not found

| Reason | Events | Share of SOCRATES events |
|---|---|---|
| different element sets | 108,008 | 60.2% |
| found | 34,827 | 19.4% |
| object not in our catalog (DEB) | 18,848 | 10.5% |
| object not in our catalog (R/B) | 7,678 | 4.3% |
| object not in our catalog (PAY) | 7,288 | 4.1% |
| object not in our catalog (UNK) | 2,621 | 1.5% |
| same inputs, not found | 1 | 0.0% |

- **Object not in our catalog.** CelesTrak's GP groups (active, analyst and three debris clouds) are not the full catalog; most missing objects are debris and rocket bodies. The full catalog needs Space-Track.
- **Different element sets.** SOCRATES's newest data was 2026-10-30 22:00 UTC; ours is about 14 h newer. Fresher data moves predicted positions by kilometres days ahead, so many near-threshold events appear or vanish. This is the data-age effect Phase 4's ML stage is about.
- **Identical inputs, not found.** Each one:

| Object A | Object B | SOCRATES TCA | SOCRATES range (km) | Rel. speed (km/s) |
|---|---|---|---|---|
| 57249 | 64095 | 2026-11-02 19:00:09 | 4.998 | 12.236 |

  A SOCRATES range at the 5 km line can fall just above it in Perigee's computation (the two SGP4 implementations and TCA searches differ by metres), so it drops out of the list.

## Agreement where both found the event from identical inputs

|  | Median | 99th percentile | Max |
|---|---|---|---|
| TCA difference | 0.3 ms | 1.4 ms | 336 ms |
| Miss distance difference | 0.4 m | 7.8 m | 1424 m |

3,456 events. Differences above 50 m: 3. These are unexplained so far: the objects' epochs match SOCRATES's to within its 86-second DSE rounding, so they look like the same element sets. Possible causes are an element set re-issued with the same epoch, or an SGP4 difference between STK and Python `sgp4` for these objects. Open item.

| Object A | Object B | TCA | SOCRATES (km) | Perigee (km) |
|---|---|---|---|---|
| 30982 | 69614 | 2026-11-04 09:20 | 2.162 | 3.372 |
| 36000 | 45462 | 2026-11-05 11:52 | 4.839 | 4.754 |
| 36371 | 69614 | 2026-11-03 07:54 | 4.927 | 3.503 |

## Perigee events that SOCRATES does not list

| Reason | Events | Share of Perigee events |
|---|---|---|
| different element sets | 738,889 | 93.9% |
| in SOCRATES | 32,461 | 4.1% |
| same inputs: same-constellation pair omitted by SOCRATES | 12,192 | 1.6% |
| object absent from this SOCRATES run | 3,006 | 0.4% |
| same inputs, not in SOCRATES | 6 | 0.0% |

With identical inputs, how many of Perigee's events SOCRATES also lists:

| Pair kind | Perigee events | Also in SOCRATES |
|---|---|---|
| Starlink-Starlink | 14,492 | 16.0% |
| Starlink-other | 746 | 99.9% |
| other | 431 | 95.8% |

| Satellites in the pair | Perigee events | Also in SOCRATES |
|---|---|---|
| different | 1,146 | 99.5% |
| same constellation or family | 14,523 | 16.1% |

**Pairs within one constellation.** For pairs from different operators the two lists agree almost completely. For pairs within one constellation (Starlink-Starlink above all, but also OneWeb-OneWeb and smaller families), SOCRATES lists only a minority of the approaches Perigee finds, even with identical inputs, and the ones it does list agree with Perigee to metres. So this is not a computation difference: SOCRATES publishes only some intra-constellation approaches. Its published methodology does not say why; a plausible reason is that operators screen their own constellations, but that is a guess. Ruled out by the data: different element sets (the kept events match to metres), a relative-speed or distance cut (the shortfall is the same in every speed and distance bin), and a per-object cap (no spike in events per object). ("Family" is the first word of both names, which also groups unrelated COSMOS satellites; that only affects this classification.)

The 6 remaining identical-input events SOCRATES does not list (open items; most are near the 5 km threshold or are formation-flying pairs, which SOCRATES lists inconsistently):

| Object A | Object B | TCA | Perigee miss (km) | Rel. speed (km/s) |
|---|---|---|---|---|
| TERRASAR-X | TANDEM-X | 2026-10-31 16:23 | 0.250 | 0.001 |
| UNISAT-6 | KUIPER-00546 | 2026-11-05 12:47 | 0.313 | 5.876 |
| 82529 | PRAETORIAN SDA_519 | 2026-11-06 01:01 | 2.293 | 14.832 |
| EXPRESS-MD2 | STARLINK-37433 | 2026-11-05 16:25 | 3.546 | 11.998 |
| POPACS 2 | YAOGAN-43 01E | 2026-11-05 10:37 | 4.600 | 6.057 |
| FENGYUN 1C DEB | POPACS 2 | 2026-11-06 19:29 | 4.911 | 14.919 |

Starlink-Starlink approaches also grow through the week in both lists, while every other kind is flat:

|  | Perigee Starlink-Starlink | Perigee other | SOCRATES Starlink-Starlink | SOCRATES other |
|---|---|---|---|---|
| Day 1 | 40,809 | 13,637 | 4,512 | 20,299 |
| Day 2 | 79,819 | 13,989 | 6,136 | 19,670 |
| Day 3 | 114,932 | 13,797 | 7,650 | 19,368 |
| Day 4 | 127,098 | 14,124 | 8,605 | 18,812 |
| Day 5 | 132,660 | 14,186 | 9,431 | 19,098 |
| Day 6 | 137,670 | 13,805 | 10,452 | 18,878 |
| Day 7 | 138,871 | 14,187 | 10,694 | 18,890 |

Starlink satellites manoeuvre constantly to hold their slots; public GP data knows nothing of future manoeuvres, so propagated days ahead, neighbours in a shell drift into each other. Days 4-7 of any Starlink-Starlink list built from public data are mostly artefacts. A good input for the ML stage: time to TCA and object type should matter a lot.

## Performance

|  |  |
|---|---|
| Objects screened | 20,087 |
| Window | 7 days at 20 s coarse steps |
| Wall time | **156 s** on 6 worker processes |
| Candidates refined | 964,595 |
| Budget (build guide) | under 30 minutes on a laptop |

## Limitations

- Perigee's catalog lacks most debris and rocket bodies (CelesTrak groups only); SOCRATES uses the full catalog.
- Both use public GP data with roughly kilometre-level error and no covariance, so a list of approaches under 5 km is a list of *possible* close approaches. SOCRATES's maximum-probability figure is not reproduced.
- Co-located objects (docked or formation flying, relative speed under 10 m/s) are flagged and kept once per pair, at their closest approach; SOCRATES lists them once per orbit. They are compared by pair over both full runs.
