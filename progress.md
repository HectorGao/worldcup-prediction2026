# Progress Log

## Session: 2026-06-24

### Phase 1: Requirements & Current State Discovery
- **Status:** in_progress
- **Started:** 2026-06-24 Asia/Shanghai
- Actions taken:
  - Read the `planning-with-files` skill instructions.
  - Confirmed there were no existing `task_plan.md`, `findings.md`, or `progress.md` files.
  - Created persistent planning files for the current goal.
  - Inspected `training.py`, `rosters.py`, `roster_strength.py`, and references across backend/tests.
  - Found that historical matches are decayed but not hard-excluded after 16 years.
  - Found that roster coverage currently depends on API-Football player season stats with no secondary enrichment fallback.
  - Checked current SQLite roster coverage: England 22/26 complete, Portugal 0/26, Uzbekistan 0/26.
  - Researched API-Football lineups page and TheSportsDB documentation.
  - Connected Chrome control successfully, but the API-Football lineups page rendered a Cloudflare "Just a moment..." page in Chrome automation.
  - Added failing test for excluding matches older than 16 years from decision profiles.
  - Implemented `max_age_years` in `build_team_profiles()` and passed `max_age_years=16` from `WorldCupService.save_historical_matches()`.
  - Ran `tests/test_public_data_sources.py`: 4 passed.
  - Added TheSportsDB public enrichment provider and API-Football lineups parser improvements.
  - Added `POST /api/squads/enrich-public?limit=10`.
  - Updated data health UI with a separate “公开源补全” button.
  - Ran public enrichment against the live SQLite DB:
    - Batch 1: 17 enriched, 3 failed due to one no-match and two 429 rate-limit errors.
    - Batch 2: 10 enriched, 0 failed.
    - Batch 3: 9 enriched, 1 failed.
  - Current live coverage: England 25/26, Portugal 24/26, Uzbekistan 9/26.
  - Confirmed Portugal vs Uzbekistan prediction output includes `roster_adjustment.available=true`.
- Files created/modified:
  - `task_plan.md` (created)
  - `findings.md` (created)
  - `progress.md` (created)

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Historical 16-year cutoff | `.venv/bin/pytest tests/test_public_data_sources.py -q` | Old matches excluded, existing parser tests pass | 4 passed | ✓ |
| Full backend test suite | `.venv/bin/pytest -q` | All tests pass | 33 passed | ✓ |
| Frontend syntax | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Public roster enrichment smoke | `POST /api/squads/enrich-public?limit=10` | Enrich queued players without API key leakage | 10 enriched, 0 failed in second batch | ✓ |
| Prediction roster adjustment smoke | `POST /api/predict/web-400021503?roster_weight=0.25` | Roster fields present and adjustment available once both sides have coverage | `available=true`, Portugal 0.9231, Uzbekistan 0.3462 | ✓ |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-06-24 | Old skill path for TDD/browser skills no longer existed | 1 | Located current skill paths with `find` and read the current files. |
| 2026-06-24 | Chrome Playwright `waitForLoadState(networkidle)` unsupported | 1 | Retried with supported `load` state. |
| 2026-06-24 | Chrome control saw Cloudflare "Just a moment..." for API-Football page | 2 | Use `web.open`/HTTP-accessible page text instead of repeating the blocked browser path. |
| 2026-06-24 | TheSportsDB returned HTTP 429 in a batch of 20 | 1 | Reduced public enrichment batch size to 10 and left failed rows retryable. |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 6: Verification and Handoff |
| Where am I going? | Finish verification/handoff after historical cutoff and public roster enrichment implementation. |
| What's the goal? | Improve the prediction system so old matches are excluded and roster/player coverage is improved via verified free/public sources. |
| What have I learned? | TheSportsDB is useful for partial club/league/position enrichment but must be rate-limited. |
| What have I done? | Implemented 16-year cutoff, public enrichment provider, API/UI controls, tests, and live DB enrichment. |

## Session: 2026-06-25

### Phase 6: Verification and Handoff
- **Status:** complete
- Actions taken:
  - Re-read `planning-with-files` instructions and restored `task_plan.md`, `findings.md`, and `progress.md`.
  - Confirmed active FastAPI database is `data/worldcup.sqlite3`, not the older `data/worldcup.db`.
  - Added a regression test proving `service.get_team_squad()` counts public `enriched` player records as usable coverage.
  - Fixed `WorldCupService.get_team_squad()` to use the same usable statuses as `roster_strength.aggregate_team_strength()`.
  - Verified API-Football 2024 player statistics for the two remaining TheSportsDB failures:
    - Abdulla Abdullayev (`73418`): Pakhtakor / Super League, 13 starts, 1104 minutes.
    - Behruzjon Karimov (`416964`): Olympic / Cup, 1 appearance, 13 minutes.
  - Processed both through `/api/squads/process-queue?limit=5`; roster queue is now 78 done, 0 failed, 0 pending.
  - Restarted FastAPI on `http://127.0.0.1:8000/`.
  - Smoke-tested Uzbekistan squad, roster health, and Portugal vs Uzbekistan prediction with `roster_weight=0.25`.

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Full backend test suite | `.venv/bin/pytest -q` | All tests pass | 34 passed, 1 warning | ✓ |
| Frontend syntax | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Roster health API | `GET /api/health/roster-data` | No pending/failed, all synced players covered | 78/78 players with stats, 0 failed | ✓ |
| Uzbekistan squad API | `GET /api/teams/Uzbekistan/squad` | Top-level coverage includes enriched records | `coverage: 1.0` | ✓ |
| Prediction roster coverage | `POST /api/predict/web-400021503?roster_weight=0.25` | Roster fields present and complete for both teams | home 1.0, away 1.0 | ✓ |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-06-25 | Queried `data/worldcup.db` and saw missing roster tables | 1 | Located actual service DB at `data/worldcup.sqlite3`. |
| 2026-06-25 | Squad API coverage counted only `complete` and ignored `enriched` | 1 | Added regression test and changed service coverage to use `USABLE_STATUSES`. |
| 2026-06-25 | Behruzjon Karimov was skipped after 3 public-source failures | 1 | Verified API-Football 2024 data, reset only that queue row to allow one API-Football retry, then processed successfully. |

### Phase 7: Match Archive, Round Cards, Team Detail, and 48-Team Coverage
- **Status:** complete
- Actions taken:
  - Researched `https://worldcup.lyihub.com/` through Chrome and confirmed its static JSON data model.
  - Added a lyihub read-only adapter for index and match-detail JSON.
  - Added SQLite tables for lyihub match details and players.
  - Added endpoints for lyihub sync, rounds, matches, coverage, and team World Cup detail.
  - Changed `/api/matches?date=...` to prefer lyihub rows when a date has lyihub data, preventing stale fallback rows from mixing into completed match days.
  - Added actual score, predicted score, and accuracy fields to dated match responses.
  - Added frontend round/all-match cards, team-click detail, player ability display, and known club display.
  - Added ability fallback derivation from numeric `score10` sub-scores when the source lacks a direct `评分`.
  - Added coverage fields that distinguish full 26-player roster coverage from strict ability-value coverage.
  - Added display-only estimated ability values for players whose source records lack direct or derivable ability scores; estimates are marked and raw source gaps remain visible.
  - Restarted FastAPI on `http://127.0.0.1:8000/`.
  - Synced lyihub: 104 matches, 58 detail files, 48/48 teams, 48/48 full 26-player squads.
  - Browser smoke-tested local dashboard controls: round tab click, team click, and match detail click.

## Test Results: Phase 7
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Lyihub focused tests | `.venv/bin/pytest tests/test_lyihub_data.py -q` | Normalization, storage, API routes pass | 3 passed | ✓ |
| Frontend syntax | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Date match API | `GET /api/matches?date=2026-06-24` | Only real lyihub matches for the date with scores and accuracy | 4 matches, all final | ✓ |
| Round API | `GET /api/lyihub/rounds` | All group and knockout round buckets | 9 buckets, 104 total matches | ✓ |
| Team detail API | `GET /api/teams/Portugal/world-cup-detail` | Matches, 26 players, clubs where known | 3 matches, 26 players, 26 clubs | ✓ |
| Coverage API | `GET /api/lyihub/coverage` | 48-team validation visible | 48 squads complete; 36 strict ability-complete; 27 missing ability values | ✓ |
| Source-gap team detail | `GET /api/teams/Haiti/world-cup-detail` | No blank displayed ability values; estimates marked | 26/26 displayed ability, 23 source, 3 estimated | ✓ |
| Browser smoke | `http://127.0.0.1:8000/` | Buttons visibly update panels | Round, team detail, and prediction detail interactions pass | ✓ |

## Session: 2026-06-27

### Phase 8: Odds, XGBoost, Adaptive Learning, and Betting Product Layer
- **Status:** complete
- Actions taken:
  - Read current Market/Poisson/Monte Carlo/Ensemble service and tests.
  - Confirmed local `.venv` does not have `xgboost`; implemented a deterministic XGBoost-compatible adapter layer.
  - Added rolling World Cup learning adjustment that uses only completed matches before the fixture date.
  - Added Monte Carlo goal variance output for model features.
  - Added `simulations` query parameter to prediction API and wired it into Monte Carlo.
  - Added `odds_markets` bundle for 1X2, handicap availability, and totals availability.
  - Added XGBoost into ensemble source probabilities and model weights.
  - Added dynamic betting risk warnings and recommended options with full/half/quarter Kelly.
  - Added frontend simulation count control, XGBoost model card, odds chips, model-vs-market edge card, and Kelly risk badges.
  - Added opt-in China Sporttery public-web odds parser/provider; live gateway is WAF-blocked in this environment, so it remains disabled unless `SPORTTERY_ENABLE_LIVE=1`.
  - Added persisted daily ensemble weight calibration from completed predictions before the target date.
  - Added model-weight APIs and returned `model_weight_run` in prediction payloads.
  - Replaced deterministic-only XGBoost behavior with a trainable layer: `xgboost.XGBClassifier` when installed, otherwise a local trainable softmax fallback using the same features.
  - Optimized recent-match feature loading so Poisson and XGBoost training use a bounded 400-day historical window plus current World Cup results, avoiding repeated parsing of the full historical table.
  - Added optional Betfair Exchange provider with 1X2, Asian handicap, and Over/Under 2.5 parser coverage.
  - Added `odds_data_status` to prediction payloads so market availability, source priority, and unavailable reasons are auditable per match.
  - Added the explicit `market_probability_no_vig` output alias required by the product spec while preserving `implied_probability_no_vig` for backward compatibility.
  - Added visible edge labels for daily match chips: `value bet`, `market efficient`, or `watch`.
  - Updated `task_plan.md`, `findings.md`, and this progress log.

## Test Results: Phase 8
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Frontend syntax | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Full backend test suite | `.venv/bin/python -m pytest -q` | All tests pass | 44 passed, 1 warning | ✓ |
| Focused model/API suite | `.venv/bin/python -m pytest tests/test_prediction_core.py tests/test_service_api.py -q` | XGBoost, learning, MC, market and API tests pass | 17 passed, 1 warning | ✓ |
| Chrome smoke | `http://127.0.0.1:8000/` with Chrome stable | Detail cards, XGBoost, odds, market panel, and simulation count control work | 6 matches, 7 model cards, XGBoost visible, 1X2 visible, simulations 10,000 -> 5,000, no console/HTTP errors | ✓ |
| Focused model/API suite after Sporttery + weights | `.venv/bin/python -m pytest tests/test_prediction_core.py tests/test_service_api.py -q` | Sporttery parser, weight calibration, no future leakage pass | 20 passed, 1 warning | ✓ |
| Full backend suite after Sporttery + weights | `.venv/bin/python -m pytest -q` | All regression tests pass | 47 passed, 1 warning | ✓ |
| Frontend syntax after card-click fix | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Chrome stable smoke after card-click fix | `http://127.0.0.1:8000/` | Top tabs, clickable round cards, health, detail, XGBoost, weight audit work | 24 cards, 7 tabs, Sporttery visible, XGBoost visible, weight sample count 120 | ✓ |
| XGBoost trainable layer focused suite | `.venv/bin/python -m pytest tests/test_prediction_core.py tests/test_service_api.py -q` | Trainable fallback, Sporttery parser, model weights pass | 21 passed, 1 warning | ✓ |
| Full backend suite after trainable XGBoost | `.venv/bin/python -m pytest -q` | All regression tests pass | 48 passed, 1 warning | ✓ |
| Frontend syntax after trainable XGBoost | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Real DB prediction timing | `WorldCupService().predict_fixture('lyihub-54328044', simulations=1000)` | XGBoost training layer active and response under a few seconds | 0.87s first run, 0.63s cached, 180 train samples | ✓ |
| Chrome stable smoke after trainable XGBoost | `http://127.0.0.1:8000/` | Detail exposes trained XGBoost metadata and existing top-tab UI still works | 24 cards, 7 tabs, `trainable_softmax_fallback`, 180 XGBoost samples, 120 weight samples | ✓ |
| Focused odds/provider suite after Betfair | `.venv/bin/python -m pytest tests/test_service_api.py tests/test_prediction_core.py -q` | Betfair parser, Sporttery parser, odds status, model tests pass | 22 passed, 1 warning | ✓ |
| Full backend suite after Betfair | `.venv/bin/python -m pytest -q` | All regression tests pass | 49 passed, 1 warning | ✓ |
| Frontend syntax after Betfair | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| FastAPI TestClient smoke after Betfair | `PYTHONPATH=backend .venv/bin/python ... TestClient` | Current code exposes Betfair/Sporttery health and prediction odds status | Betfair true, Sporttery true, XGBoost 180 samples, odds status present | ✓ |
| Chrome stable smoke after approval reset | `http://127.0.0.1:8000/` with `/Applications/Google Chrome.app` | Top tabs, round cards, Today odds/value strips, health odds sources, and detail model audit work | 7 tabs, 24 round cards, 6 Today prediction cards, 6 odds strips, 6 edge strips, XGBoost `trainable_softmax_fallback`, 180 train samples | ✓ |
| Full backend suite final Phase 8 | `.venv/bin/python -m pytest -q` | All regression tests pass | 49 passed, 1 warning | ✓ |
| Frontend syntax final Phase 8 | `node --check src/main.js` | No syntax errors | Passed | ✓ |
| Focused suite after market probability alias | `.venv/bin/python -m pytest tests/test_prediction_core.py tests/test_service_api.py -q` | No-vig alias, edge labels, odds/provider/model API tests pass | 22 passed, 1 warning | ✓ |
| Full backend suite after market probability alias | `.venv/bin/python -m pytest -q` | All regression tests pass | 49 passed, 1 warning | ✓ |
| API market alias smoke | `WorldCupService(...).sync_date('2026-06-15')` then `predict_fixture(...)` | Market prediction exposes `market_probability_no_vig`, overround, Kelly full/half/quarter, normalized weights | no-vig sum 1.0, overround 0.049571, Kelly keys full/half/quarter, weights sum 1.0 | ✓ |
| Chrome stable smoke after market alias | `http://127.0.0.1:8000/` with `/Applications/Google Chrome.app` | Current UI/API expose top tabs, Today betting chips, health odds sources, detail model audit, and `market_probability_no_vig` | 7 tabs, 24 round cards, 6 Today prediction cards, 6 odds strips, 6 edge strips, XGBoost 180 samples, weights sum 1.0 | ✓ |

## Error Log: Phase 8
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-06-27 | `xgboost` package missing from local `.venv` | 1 | Implemented deterministic XGBoost adapter with `engine` metadata. |
| 2026-06-27 | Ensemble rounded weights summed to `0.999999` | 1 | Returned raw normalized weights instead of rounded values. |
| 2026-06-27 | China Sporttery gateway returned `禁止访问` / Tencent WAF block | 2 | Added parser and opt-in provider; default health reports live source disabled unless `SPORTTERY_ENABLE_LIVE=1`. |
| 2026-06-27 | Smoke test clicked stale `.match-card` selector and then a non-detail button | 2 | Updated smoke selectors to current `.round-card` UI and made round cards themselves open match detail. |
| 2026-06-27 | Initial trainable XGBoost probe took over 60s | 1 | Profiled the bottleneck to full historical date parsing in Poisson recent rates; bounded `_recent_match_inputs` to the recent window and added a lightweight XGBoost training feature path. |
| 2026-06-27 | Betfair official docs entry returned a regional `Restricted` page | 1 | Kept Betfair optional and credential-gated, added parser coverage and health reporting without claiming live validation. |
| 2026-06-27 | Could not restart local 8000 server for Chrome smoke | 1 | Resolved after approval reset: restarted FastAPI on port 8000 and passed Chrome stable smoke with top tabs, round cards, Today odds/value strips, health odds sources, and detail model audit. |
