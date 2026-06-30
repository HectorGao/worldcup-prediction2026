# Task Plan: World Cup Roster Coverage and Historical Window

## Goal
Improve the World Cup prediction system so decisions ignore matches older than 16 years and current-match roster/player strength coverage is materially improved through verified free/public data sources, with persistent evidence and progress tracked on disk.

## Current Phase
Phase 8 complete

## Phases

### Phase 1: Requirements & Current State Discovery
- [x] Capture the user goal in persistent planning files.
- [x] Inspect current historical-data weighting and prediction code.
- [x] Inspect current roster/provider/database coverage gaps.
- [x] Document findings in `findings.md`.
- **Status:** complete

### Phase 2: External Data Source Research
- [x] Use official API documentation before live API usage.
- [x] Find free/public sources for player club, league, position, and roster data.
- [x] Use browser/web tooling and capture every useful finding in `findings.md`.
- [x] Identify sources that need manual user registration or login.
- **Status:** complete

### Phase 3: Historical Match Decision Window
- [x] Add tests proving matches older than 16 years are excluded from decision profiles.
- [x] Implement cutoff in historical profile construction and prediction service.
- [x] Preserve recent-match weighting within the allowed 16-year window.
- **Status:** complete

### Phase 4: Roster Coverage Enrichment
- [x] Add tests for enrichment fallback and source matching.
- [x] Implement source adapters or scrapers for usable free/public roster/player data.
- [x] Match enriched data to existing squad players without leaking API keys.
- [x] Store coverage/source/confidence evidence in SQLite.
- **Status:** complete

### Phase 5: Frontend and API Visibility
- [x] Expose improved roster coverage and sources in API responses.
- [x] Show source status/manual-action needs in the data health UI.
- [x] Keep prediction usable when only partial roster data is available.
- **Status:** complete

### Phase 6: Verification and Handoff
- [x] Run unit/integration tests.
- [x] Smoke test local API and the dashboard path.
- [x] Update `progress.md` and `findings.md`.
- [x] Report completed work and remaining manual actions, if any.
- **Status:** complete

### Phase 7: Match Archive, Team Detail, and 48-Team Data Coverage
- [x] Inspect existing match/result/prediction/team UI and API gaps.
- [x] Research `https://worldcup.lyihub.com/` with browser/web tools and record usable data structures.
- [x] Add backend storage and APIs for dated match cards with actual score, predicted score, and accuracy.
- [x] Add team detail API with this-tournament matches, scores, squad, club, and player ability fields.
- [x] Add round/all-match views for group stage rounds and knockout rounds.
- [x] Implement data coverage validation for all 48 teams.
- [x] Update the frontend to show historical dates, round cards, team detail, squads, clubs, and prediction accuracy.
- [x] Run tests and browser smoke checks.
- **Status:** complete

### Phase 8: Odds, XGBoost, Adaptive Learning, and Betting Product Layer
- [x] Add Market bundle output for 1X2, handicap placeholder, and totals availability.
- [x] Expose `market_probability_no_vig`, overround, and explicit unavailable-market shape for odds output.
- [x] Add XGBoost-compatible model layer with deterministic local fallback when `xgboost` is not installed.
- [x] Add rolling World Cup learning adjustment with future-data leakage guard.
- [x] Add configurable Monte Carlo simulation count in API and UI.
- [x] Add betting recommendations, Kelly fractions, confidence, and dynamic risk warnings.
- [x] Add compact UI cards/chips/bars for odds, model-vs-market edge, XGBoost, and Kelly risk.
- [x] Show `value bet` and `market efficient` labels in daily Market vs Model edge chips.
- [x] Add tests for XGBoost stability, market fallback, Monte Carlo simulation count, and no future leakage.
- [x] Run full backend tests and Chrome smoke test after service restart.
- [x] Add China Sporttery odds parser/provider as an opt-in public-web fallback when The Odds API has no World Cup market.
- [x] Add optional Betfair Exchange provider with health validation and parser coverage for 1X2, Asian handicap, and Over/Under 2.5.
- [x] Replace the deterministic-only XGBoost adapter with a trainable model layer: real `xgboost` when optional dependency is installed, trainable softmax fallback otherwise.
- [x] Add daily persisted model-weight recalibration from completed-match scoring metrics.
- [x] Switch Sporttery investigation to the mobile football calculator endpoint and document WAF fallback limits.
- [x] Add lottery market output for SPF/RQSPF, handicap no-vig probabilities, Edge, Kelly, and recommendations.
- [x] Add handicap regions to the score heatmap and detail page model-vs-market comparison.
- [x] Clamp Monte Carlo simulation count to `1,000..100,000` and add custom UI input.
- [x] Use `ui-ux-pro-max` review rules to tighten the Today match layout, odds panels, date badges, and sales-window status copy.
- [x] Change the Beijing default match day from completed `2026-06-28` rows to the next actionable match day, `2026-06-29`.
- [x] Add the current China Sporttery six-match World Cup sales-window snapshot from `2026-06-28/29/30` sales dates.
- [x] Show the six Sporttery-window matches in the Today page with Beijing match-date chips and right-side SPF/RQSPF odds cards.
- [x] Recompute predictions using the updated lottery markets and expose `display_mode=sporttery_lottery_window`.
- [x] Verify API, Chrome UI, frontend syntax, focused backend tests, and full backend tests after service restart.
- **Status:** complete

### Phase 9: Today Card Grid and Handicap Heatmap UI
- [x] Read `ui-ux-pro-max` and apply dashboard layout, chart labeling, touch target, and accessibility rules.
- [x] Convert the desktop Today match list from long horizontal rows into a responsive card grid that uses available width.
- [x] Keep each match card self-contained with teams, date badge, right-side odds content, status, and detail action.
- [x] Use the final market-calibrated score matrix for `score_matrix`, `top_scorelines`, `score_heatmap`, and handicap region calculations.
- [x] Add handicap model and market probabilities to `score_heatmap`.
- [x] Render a full 0-7 score-probability heatmap in match detail.
- [x] Label every visible heatmap cell with its score, probability, and handicap outcome region.
- [x] Show aggregate handicap win/draw/loss probabilities above the heatmap.
- [x] Verify frontend syntax, focused backend tests, full backend tests, and browser DOM behavior.
- **Status:** complete

### Phase 10: Live Refresh Button and Current Sporttery Window
- [x] Re-check `https://m.sporttery.cn/mjc/jsq/zqspf/` in the browser and extract the current purchasable World Cup odds window.
- [x] Update the Sporttery fallback snapshot from the older 6-match window to the current 9-match window.
- [x] Add the newly purchasable matches: Mexico vs Ecuador, England vs DR Congo, Belgium vs Senegal, United States vs Bosnia and Herzegovina.
- [x] Remove the delisted South Africa vs Canada market from the current Today window.
- [x] Add `POST /api/refresh/current?date=YYYY-MM-DD` to sync completed lyihub scores and refresh Sporttery odds in one call.
- [x] Wire the top refresh button to the new refresh endpoint and clear stale frontend prediction cache after refresh.
- [x] Ensure Today match odds come from current fixture market fields even if stored prediction payloads are stale.
- [x] Restart FastAPI on `127.0.0.1:8000`.
- [x] Verify the API and browser refresh button show 9 current purchasable matches and updated SPF/RQSPF odds.
- **Status:** complete

## Key Questions
1. Where is the current historical-data date window enforced, if at all?
2. Which roster/player fields are missing because API-Football free season data is unavailable?
3. Which free/public sources can fill club/league/position/player-strength coverage without violating docs or requiring paid access?
4. Which data requires manual user registration or browser login?
5. How should low-confidence public data affect prediction strength without overstating precision?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use project-root planning files | User explicitly requested `planning-with-files`; root files survive context loss and are visible in the repo. |
| Keep API keys out of output and persisted provider payloads | Existing project handles secrets through `.env`; health and reports must not leak keys. |
| Treat roster enrichment as confidence-weighted | Public/free data may be incomplete; predictions should expose coverage and source confidence instead of silently treating missing values as weak players. |
| Use TheSportsDB as partial public enrichment | It provides current club, position, nationality, team league, and free key `123`; it lacks robust current-season minutes/ratings, so records are marked `enriched` rather than full `complete`. |
| Limit public enrichment batches to 10 players | TheSportsDB free tier is 30 requests/minute and each player can require search + team lookup. |
| Prefer lyihub fixtures over older fallback fixtures on dates where lyihub data exists | Prevent stale Wikipedia fallback rows from polluting dated match cards, actual scores, and accuracy. |
| Treat lyihub squad coverage and ability coverage separately | 48 teams have full 26-player squads; some source records lack ability fields, so the health report must show strict ability gaps instead of inventing values. |
| Use a trainable XGBoost-compatible layer with optional real `xgboost` | The current local venv does not include `xgboost`; the system now trains a local softmax fallback on the same feature rows and will use `xgboost.XGBClassifier` automatically when the optional dependency is installed. |
| Keep Market as a formal ensemble source and expose unavailable handicap explicitly | The Odds API may not return World Cup handicap markets; UI should show `盘口不可用` instead of hiding the market. |
| Filter completed-match learning samples strictly before fixture date | Prevents future World Cup results from leaking into earlier predictions. |
| Keep Betfair optional and secret-driven | Betfair requires an app key and session token; live access is enabled only when `BETFAIR_APP_KEY` and `BETFAIR_SESSION_TOKEN` exist in `.env`. |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| None yet | 1 | Pending implementation. |
| TheSportsDB 429 Too Many Requests | 1 | Reduced public enrichment batch size to 10 and left failed items retryable. |
| `data/worldcup.db` did not contain roster tables | 1 | Confirmed FastAPI uses `data/worldcup.sqlite3`; final verification uses the service database. |
| Two Uzbekistan players could not be matched by TheSportsDB | 1 | Verified API-Football 2024 player statistics for Abdulla Abdullayev and Behruzjon Karimov, reset the exhausted queue item once, and processed both through the API-Football queue. |
| `xgboost` package missing in local venv | 1 | Added a trainable fallback layer and optional `ml` dependency for real `xgboost`. |
| Ensemble weights rounded to 0.999999 in tests | 1 | Preserve raw normalized weights instead of rounding them in the API payload. |
| Betfair official docs entry returned a regional `Restricted` page | 1 | Added the provider from the standard Exchange JSON-RPC shape, marked it optional, and documented that live validation needs valid Betfair credentials. |

## Notes
- User requirement: matches older than 16 years must not participate in decisions.
- User requirement: roster/player-stat coverage is currently too low and should be improved via web/browser research and usable free/public data.
- User requirement: if a needed source requires manual entry/registration, ask the user to perform that step.
- Final verification: no remaining manual entry is needed for the currently synced England, Portugal, and Uzbekistan squads; roster health is 78/78 players with stats and 0 failed queue items.
- New user goal on 2026-06-25: when viewing yesterday/earlier dates, show all matches for that date with actual score, predicted score, and prediction accuracy; clicking a team should show its World Cup matches and squad with player club; use `worldcup.lyihub.com` as data/UI reference; show group-stage and knockout round cards; validate coverage across all 48 teams.
