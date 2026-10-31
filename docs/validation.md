# Propagation validation (Phase 2)

Generated 2026-10-31 16:22 UTC by `scripts/validate_propagation.py` from catalog snapshot `20261031T144946Z`.

## 1. ISS against NASA's flight-dynamics ephemeris

Reference: [NASA JSC FOD/TOPO ISS OEM](https://nasa-public-data.s3.amazonaws.com/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.txt), EME2000 state vectors that do not come from the public TLE/GP data. Frame conversions on the reference side are done by astropy.

ISS element set epoch: 2026-10-31 10:25:39 UTC, compared at 540 reference points (4-minute spacing) from 12 h before to 24 h after epoch.

| | Value |
|---|---|
| Position error nearest epoch (-0.03 h) | **0.91 km** |
| of which radial / in-track / cross-track | +0.02 / -0.91 / +0.03 km |
| End to end in Earth-fixed frame (SGP4 + Perigee frames) | 0.94 km |
| Largest orbital-radius difference over the window | 0.29 km |

| Window | Max error | Median error |
|---|---|---|
| within 1 h of epoch | 0.96 km | 0.83 km |
| 12 h before to epoch | 1.33 km | 0.47 km |
| epoch to +12 h | 1.77 km | 0.85 km |
| +12 h to +24 h | 1.89 km | 0.85 km |

Public element sets are fits to tracking data with roughly kilometre-level accuracy at epoch, and the error is mostly in-track and grows with time from epoch. This is the uncertainty every later stage (screening, ML) has to live with. The NASA file is itself a prediction, made when it was published, so part of the difference far from epoch belongs to it.

## 2. Frames code alone

Perigee's TEME to Earth-fixed rotation (IAU-82 GMST, UTC for UT1, no polar motion) against astropy's TEME to ITRS on the same SGP4 positions:
median **25 m**, max 30 m. The simplifications cost tens of metres, about 36 times smaller than the TLE error.

## 3. Python sgp4 against satellite.js (the browser library)

500 randomly sampled catalog objects (20 deep-space, which use the SDP4 branch), propagated to snapshot time +0 h, +6 h, +24 h, +72 h: 2000 position pairs.

| | Max | 99th percentile | Median |
|---|---|---|---|
| TEME position | 7.57 m | 7.27 m | 2.53 m |
| TEME position, epochs truncated to ms | 0.227 m | | 0.062 m |
| Ground point (lat/lon) | 7.1 m | | 2.3 m |
| Height | 2.3 m | | 0.0 m |

Objects flagged as failed by one library but not the other: 0.

The few-metre position difference comes from satellite.js reading `EPOCH` through a JavaScript `Date`, which keeps only milliseconds (1 ms is about 7.5 m of LEO motion). With Python's epochs truncated the same way the two agree to centimetres, so the SGP4 maths is identical. Small ground and height differences also come from the geodetic conversion (satellite.js uses its own WGS-84 constants and iteration).
