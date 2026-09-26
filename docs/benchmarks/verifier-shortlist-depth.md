# Verifier shortlist depth

| Depth | Micro gold recall | Mean per-item gold recall | Missing gold relations |
| ---: | ---: | ---: | ---: |
| 6 | 0.041322 | 0.032593 | 348 |
| 10 | 0.060606 | 0.048148 | 341 |
| 20 | 0.063361 | 0.050370 | 340 |
| 30 | 0.063361 | 0.050370 | 340 |
| 50 | 0.077135 | 0.068148 | 335 |
| 75 | 0.168044 | 0.146667 | 302 |
| 100 | 0.192837 | 0.165926 | 293 |
| 150 | 0.206612 | 0.179259 | 288 |
| 200 | 0.228650 | 0.199259 | 280 |

Chosen N: 100 (uncapped: 200).
Smallest measured depth within 1 percentage point of depth-200 micro recall, capped at 100.

Unreachable gold relations at depth 200: 280.

Baseline replay admits p >= 0.5 and never abstains.
The deterministic baseline prompt hash identifies the canonical answer rule.
