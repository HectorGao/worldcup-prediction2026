# Findings & Decisions

## Requirements
- Use `planning-with-files` and persist the goal/plan in project files.
- Do not allow match data older than 16 years to participate in prediction decisions.
- Improve current-match roster and player-stat coverage; current API-Football-only coverage is too low.
- Use official API documentation before using any external API.
- Use browser/web tooling to find usable data sources.
- If a useful source requires manual registration, login, or user-provided fields, ask the user.
- Preserve API key secrecy.

## Research Findings
- `backend/worldcup_predictor/data/training.py` currently uses exponential half-life weighting but does not exclude matches older than 16 years.
- `WorldCupService.save_historical_matches()` currently calls `build_team_profiles(matches, as_of="2026-06-16", half_life_years=5.0)`, so both the date and decision window need correction.
- Roster coverage is currently driven by API-Football squad + `/players?id=...&season=...`; no secondary enrichment provider is implemented for missing club/league/player fields.
- `roster_strength.aggregate_team_strength()` already regresses uncompleted players to neutral 65 and exposes coverage, which is good for not overstating missing data.
- API-Football 2026 lineups page is usable as a public roster fallback; it lists each qualified team with players grouped by Goalkeepers, Defenders, Midfielders, and Forwards. It includes England, Portugal, Uzbekistan, and the full 48-team page content.
- TheSportsDB documentation confirms free v1 key `123`, v1 base URL `https://www.thesportsdb.com/api/v1/json`, and rate limit of 30 requests/minute for free users.
- TheSportsDB useful free endpoints include `lookup_all_players.php?id=...` for team players and `lookupplayerstats.php?id=...` for player statistics, but free search/list limits are tight and matching by team/player ID may require mapping.
- Live TheSportsDB probes:
  - `searchplayers.php?p=Cristiano_Ronaldo` returned current club Al-Nassr and position Centre-Forward.
  - `searchplayers.php?p=Bukayo_Saka` returned current club Arsenal and position Right Winger.
  - `searchplayers.php?p=Abdukodir_Khusanov` returned current club Manchester City and position Centre-Back.
  - `searchteams.php?t=Manchester_City` returned league English Premier League.
  - `lookupplayerstats.php` returned historical competition stats for Cristiano Ronaldo, but it is not reliable current club-season performance data.
- Final live DB after public enrichment plus API-Football queue retry:
  - Roster health: 78/78 players with usable stats, 0 pending, 0 failed.
  - England squad endpoint coverage: 1.0 after counting public `enriched` records as usable.
  - Portugal squad/strength coverage: 1.0.
  - Uzbekistan squad/strength coverage: 1.0.
  - Portugal vs Uzbekistan prediction has `roster_adjustment.available=true`, `roster_coverage.home=1.0`, and `roster_coverage.away=1.0`.
- TheSportsDB could not confidently match two Uzbekistan abbreviated players:
  - `A. Abdullaev` was resolved through API-Football 2024 as Abdulla Abdullayev, Pakhtakor, Uzbekistan Super League / AFC Champions League Elite data.
  - `B. Karimov` was resolved through API-Football 2024 as Behruzjon Karimov, Olympic, Uzbekistan Cup / Super League data.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Start with code inspection before new data fetches | The project already has API-Football roster code and SQLite schema; avoiding duplicate adapters requires understanding the existing shape first. |
| Store source confidence with enriched data | Free/public sources can be stale or partial; confidence should be explicit and used to control prediction impact. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Historical data has decay but no hard cutoff | Add a max age/window parameter and tests proving pre-cutoff matches are ignored. |
| Roster coverage depends on API-Football player season stats only | Research and add public/free enrichment fallback sources. |
| Chrome control hit API-Football Cloudflare "Just a moment..." page | Use `web.open`/HTTP-retrieved page text for auditable parsing; keep Chrome finding as evidence that browser UI may require manual verification. |
| TheSportsDB rate limit triggered during a batch of 20 | Reduced code/frontend public-enrichment limit to 10 players per click; failed queue items remain retryable. |
| `GET /api/teams/Uzbekistan/squad` returned top-level `coverage: 0.0` despite enriched player rows | Fixed service coverage to count both `complete` and `enriched`, matching the strength model. |
| Direct SQLite check accidentally targeted `data/worldcup.db` | Confirmed active FastAPI database is `data/worldcup.sqlite3`; final verification uses `worldcup.sqlite3` plus HTTP API. |

## Resources
- API-Football documentation: https://www.api-football.com/documentation-v3
- API-Football 2026 lineups page: https://www.api-football.com/news/post/fifa-world-cup-2026-lineups-all-teams-coaches-and-players
- TheSportsDB documentation: https://www.thesportsdb.com/documentation
- TheSportsDB player list endpoint pattern: `https://www.thesportsdb.com/api/v1/json/123/lookup_all_players.php?id=133604`
- TheSportsDB player stats endpoint pattern: `https://www.thesportsdb.com/api/v1/json/123/lookupplayerstats.php?id=34146304`
- New local API endpoint: `POST /api/squads/enrich-public?limit=10`
- Current code: `backend/worldcup_predictor/data/training.py`
- Current code: `backend/worldcup_predictor/data/rosters.py`
- Current code: `backend/worldcup_predictor/service.py`

## Visual/Browser Findings
- Web documentation/page inspection:
  - API-Football lineups page lines for England list 3 goalkeepers, 9 defenders, 7 midfielders, and 7 forwards.
  - API-Football lineups page lines for Portugal list 3 goalkeepers, 9 defenders, 6 midfielders, and 8 forwards.
  - API-Football lineups page lines for Uzbekistan list 3 goalkeepers, 10 defenders, 10 midfielders, and 3 forwards.
  - TheSportsDB docs state free users have 30 requests/minute and document the free key and player list/stat endpoints.
- Chrome control was connected successfully, but direct Chrome navigation to the API-Football lineups URL rendered a Cloudflare "Just a moment..." page instead of roster content.

## 2026-06-25 Reference Site Findings
- Chrome opened `https://worldcup.lyihub.com/`; title is `林亦 LYi · 嘉豪世界杯预测 2026`.
- The page starts with a risk confirmation dialog, then shows a prediction board and match cards.
- Match cards include stage/round text such as `小组赛 第3轮`, teams, model buttons, countdown/completed state, and detail links like `match.html?id=54328044`.
- The reference UI has a dense card-board layout with AI/model labels and per-match analysis entry points; our UI should use it as structural reference only, while our data remains local/audited.
- `js/data-source.js` exposes static JSON paths:
  - index: `data/index.json`
  - match detail: `data/matches/{match_id}.json`
- `data/index.json` currently contains 104 matches, stages include `小组赛 第1轮`, `小组赛 第2轮`, `小组赛 第3轮`, `1/16决赛`, `1/8决赛`, `1/4决赛`, `半决赛`, `季军赛`, and `决赛`.
- Match detail JSON contains `match`, `llm_predict`, `players`, and `team_profiles`.
- Detail player records include `player_name`, `shirt_number`, `position`, `score10`, and `fitness`. This satisfies the requested player ability-value source.
- Implemented lyihub static JSON ingestion as a read-only reference data adapter:
  - `data/index.json` syncs 104 matches.
  - `data/matches/{match_id}.json` syncs match details and player records when available.
  - Local API exposes dated matches, round cards, team detail, and coverage.
- Current lyihub coverage after sync:
  - 48/48 group-stage teams found.
  - 48/48 teams have 26-player squads.
  - 36/48 teams have ability values for every listed player.
  - 27 total player ability values are still missing from the source data after deriving missing total ratings from numeric `score10` sub-scores where possible.
- For UI completeness, players still missing source ability values receive an explicitly marked estimated ability based on same-team/same-position averages. The raw coverage endpoint still exposes the 27 source-data gaps.
- `/api/matches?date=2026-06-24` now prefers lyihub rows and returns the 4 real completed matches for that date, including actual score, predicted score, and accuracy.
- `/api/lyihub/rounds` returns 9 round buckets covering group rounds, 1/16, 1/8, 1/4, semifinals, third-place match, and final.
- `/api/teams/Portugal/world-cup-detail` returns Portugal's this-tournament matches plus 26 players; known clubs are joined from local public/API roster enrichment by shirt number.
- `/api/teams/Haiti/world-cup-detail` confirms display coverage for a source-gap team: 26/26 players have displayed ability, 23 source ability values, 3 estimated values.
- Browser smoke test on `http://127.0.0.1:8000/` confirmed:
  - Round tab click switches all-match cards to `小组赛 第1轮` and shows 24 cards.
  - Team button click opens `#team-detail` and renders the team's tournament matches and squad.
  - Match detail button click renders probabilities, xG, roster weight slider, three-line strength, and Top 6 scorelines.

## 2026-06-27 Model/Product Enhancement Findings
- Current local `.venv` does not include the `xgboost` package. `prediction/xgboost_model.py` now supports a real `xgboost.XGBClassifier` when the optional `ml` dependency is installed and otherwise trains a local softmax fallback on the same feature rows.
- The trainable XGBoost-compatible layer consumes Elo delta, Poisson lambdas, Monte Carlo probability/variance, market implied probability when available, roster attack/defense edge, and rolling World Cup form.
- Service predictions now train/cache the XGBoost layer per fixture date using completed matches strictly before that date. Real DB smoke for `lyihub-54328044` trained on 180 samples with `engine="trainable_softmax_fallback"` and completed in under 1 second after the recent-match window optimization.
- `prediction/learning.py` now builds rolling World Cup form adjustments only from completed World Cup/lyihub matches strictly before the target fixture date. This is the current data-leakage guard.
- `WorldCupService.predict_fixture()` now accepts `simulations`, clamps it to `100..50000`, and sends that count into Monte Carlo simulation.
- Ensemble now supports `xgboost` as a model source and uses normalized weights from available models. If market is missing, market is excluded and remaining model weights are renormalized.
- Market output now includes an `odds_markets` bundle:
  - `h2h`: 1X2 no-vig probabilities when odds exist.
  - `handicap`: explicit unavailable object when no handicap market is returned.
  - `totals`: Over/Under no-vig probabilities when odds exist.
- Market payloads now expose both `market_probability_no_vig` and the older `implied_probability_no_vig` key. Unavailable markets return empty probability objects, `overround=null`, and `available=false`, so the UI can render `盘口不可用` without losing the requested output shape.
- Betting value output now includes recommended options, full/half/quarter Kelly, confidence, and dynamic risk warnings.
- Daily match edge chips now visibly label each outcome as `value bet`, `market efficient`, or `watch`, matching the 5% value and 3% efficient thresholds.
- Dynamic risk warnings currently cover:
  - 盘口缺失
  - 模型分歧
  - 首轮保守系数影响
  - 近期样本不足
  - roster 不完整
  - 本届世界杯可学习样本不足
- Frontend uses compact chips/mini-cards/probability bars for odds, edge, XGBoost badge, and Kelly risk; it preserves the existing World Cup card-board style.
- Test evidence added:
  - XGBoost adapter deterministic output and probability normalization.
  - Trainable XGBoost-compatible fallback fits fixed samples and returns stable predictions.
  - Rolling learning excludes future match data.
  - Monte Carlo simulation count changes sample size and confidence interval width.
  - Prediction API returns xgboost, learning, odds markets, risk warnings, and supports `simulations=5000`.
- China Sporttery source investigation:
  - `https://www.sporttery.cn/` is reachable and advertises the public web API host `//webapi.sporttery.cn`.
  - Direct gateway probes to `https://webapi.sporttery.cn/gateway/jc/football/getFixedBonusV1.qry?clientCode=3001` returned either `禁止访问` or a Tencent WAF block page in this environment, even with Referer/User-Agent headers.
  - Implemented a parser/provider for the observed Sporttery fixed-bonus JSON shape, but live fetching is opt-in behind `SPORTTERY_ENABLE_LIVE=1`; default health reports it as configured=false rather than silently scraping a blocked endpoint.
  - When available, Sporttery odds can enrich 1X2, totals, and handicap fields and are persisted with `market_source="China Sporttery"`.
- Betfair source investigation:
  - Official developer docs entry `https://docs.developer.betfair.com/` returned a regional `Restricted` page from the current environment, so live docs/API probing could not be completed here.
  - Added optional `BetfairOddsProvider` using the standard Exchange JSON-RPC shape, enabled only when `BETFAIR_APP_KEY` and `BETFAIR_SESSION_TOKEN` are present in `.env`.
  - Betfair parser supports `MATCH_ODDS`, `ASIAN_HANDICAP`, and `OVER_UNDER_25`, and the health API reports the provider as unconfigured when secrets are absent.
  - Prediction output now includes `odds_data_status` with configured odds providers, available markets, selected source, and unavailable-market reason.
- Daily ensemble weight calibration is now persisted in SQLite:
  - New table: `model_weight_runs`.
  - New APIs: `POST /api/models/recalibrate?date=YYYY-MM-DD` and `GET /api/models/weights?date=YYYY-MM-DD`.
  - Calibration uses completed prediction samples strictly before the target date and scores model source probabilities with Brier Score plus Log Loss.
  - Prediction output now includes `model_weight_run` so the dashboard/report can audit which learned weights were used.

---
*Update this file after every 2 view/browser/search operations.*
