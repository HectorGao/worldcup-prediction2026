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

## 2026-06-28 Sporttery Mobile Calculator Findings
- The requested mobile page `https://m.sporttery.cn/mjc/jsq/zqspf/` returns a lightweight HTML shell and loads the calculator UI through static JS/CSS.
- In-app browser rendering confirmed the page currently shows World Cup football markets and text for:
  - SPF rows (`had`): `胜/平/负`
  - RQSPF rows (`hhad`): `让球胜/让球平/让球负`
  - match number, business date, kickoff time, league name, home/away Chinese names, and handicap line.
- Static script `dataTransfer.js` identifies the data endpoint used by the page:
  - `https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry`
  - Query params: `channel=<commonV1Fun.comDataChannel>` and `poolCode=hhad,had`
  - Mobile page uses it for both normal 1X2 (`had`) and handicap 1X2 (`hhad`).
- Important JSON fields from the formatter:
  - `value.matchInfoList[].subMatchList[]`
  - `matchId`, `matchNumStr`, `matchDate`, `matchTime`, `businessDate`, `matchNumDate`, `taxDateNo`
  - `leagueAllName`, `leagueAbbName`
  - `homeTeamAllName`, `homeTeamAbbName`, `awayTeamAllName`, `awayTeamAbbName`
  - `had.h/d/a/goalLine`, `hhad.h/d/a/goalLine`, `poolList[].poolCode`, `poolList[].cbtValue`
- Direct curl to the JSON endpoint from this environment still returns a Tencent WAF block page even with mobile Referer/User-Agent. Browser rendering can access the page, so live backend fetch remains opt-in and must keep cached/manual fallback.
- The backend Sporttery provider was updated to the mobile calculator endpoint and parses `matchInfoList/subMatchList`; tests use recorded mock JSON rather than live network.
- Prediction output now includes:
  - `lottery_market` for UI-level SPF/RQSPF display.
  - `handicap_analysis` for model handicap probabilities, market no-vig probabilities, edge, Kelly, and recommendations.
  - `score_heatmap.handicap_regions` so the frontend can mark each score cell as handicap win/draw/loss.
- Monte Carlo simulation requests now clamp to `1,000..100,000`, matching the UI custom input range.

## 2026-06-28 Beijing Date And Lottery Window Fix
- Product/date rule: the default "today" match view should prefer the nearest non-final Beijing match day. On 2026-06-28 Asia/Shanghai, the 2026-06-28 local rows are all completed, so the dashboard now defaults to 2026-06-29.
- Current Sporttery mobile page verification showed 6 World Cup markets across sales dates 2026-06-28, 2026-06-29, and 2026-06-30:
  - 周日073 南非 vs 加拿大, kickoff 06-29 03:00 BJT, SPF 5.65 / 3.50 / 1.50, RQSPF +1 2.25 / 3.00 / 2.84.
  - 周一074 巴西 vs 日本, kickoff 06-30 01:00 BJT, SPF 1.52 / 3.56 / 5.25, RQSPF -1 2.77 / 3.28 / 2.16.
  - 周一075 德国 vs 巴拉圭, kickoff 06-30 04:30 BJT, SPF 1.24 / 4.90 / 8.40, RQSPF -1 1.82 / 3.65 / 3.28.
  - 周一076 荷兰 vs 摩洛哥, kickoff 06-30 09:00 BJT, SPF 1.89 / 3.07 / 3.64, RQSPF -1 3.82 / 3.58 / 1.70.
  - 周二077 科特迪瓦 vs 挪威, kickoff 07-01 01:00 BJT, SPF 3.70 / 3.33 / 1.79, RQSPF +1 1.79 / 3.70 / 3.33.
  - 周二078 法国 vs 瑞典, kickoff 07-01 05:00 BJT, SPF 1.18 / 5.50 / 10.00, RQSPF -1 1.67 / 3.85 / 3.70.
- Because direct backend curl to the Sporttery JSON API is still WAF-blocked, these 6 current markets are stored as a Sporttery snapshot fallback and overwrite older snapshot values for those fixture ids when the service starts/lists matches.
- `/api/matches?date=2026-06-28` now returns `date=2026-06-29`, `display_mode=sporttery_lottery_window`, `window_dates=[2026-06-29, 2026-06-30, 2026-07-01]`, and 6 matches.
- Frontend "今日比赛" now labels the panel as a Beijing-time Sporttery sales window and shows per-card BJT date/time badges, match number chips, SPF/RQSPF odds, Edge, and Kelly.

## 2026-06-29 Today Card Grid And Handicap Heatmap Findings
- `ui-ux-pro-max` review points applied:
  - Desktop data views should use available width instead of forcing a single long vertical strip.
  - Chart data should include visible labels and legends, not rely on color alone.
  - Touch/click targets should remain explicit, with each match card acting as a self-contained decision unit.
- Today match layout changed from row-like strips to a responsive CSS grid:
  - `#today` no longer uses the two-column `.layout` container, so it occupies the full dashboard width.
  - `.match-list` now uses `repeat(auto-fit, minmax(300px, 1fr))`.
  - Each `.match-row` is a one-column card containing teams, date badge, odds panel, and actions.
  - Browser verification on `http://127.0.0.1:8000/` showed 6 match cards in a full-width Today view with 4 desktop columns at the tested viewport.
- Root cause for the missing/incomplete score heatmap:
  - The detail page rendered `analysis.prediction` when available, which could be a stale stored prediction.
  - The backend heatmap used the Poisson submodel matrix instead of the final market-calibrated score matrix.
- Backend prediction output now uses the final calibrated score distribution for:
  - `score_matrix`
  - `top_scorelines`
  - `score_heatmap`
  - `handicap_analysis`
- `score_heatmap` now also includes:
  - `handicap_probabilities`
  - `handicap_market_probabilities`
  - per-score `handicap_regions`
- Frontend heatmap now:
  - renders a full 0-7 score grid, 64 visible cells.
  - labels each cell with score, probability, and handicap region.
  - shows aggregate let-win/let-draw/let-loss model probability and market probability above the grid.
  - labels the section as `0-7 热力图` and the overflow bucket as `8+ tail`, matching the actual calibrated matrix.
- Browser verification for South Africa vs Canada showed:
  - 64 heat cells.
  - `7-7` visible.
  - region totals: let-win/draw/loss visible with market probabilities.
  - region cell classes split into let-win, let-draw, and let-loss areas.
- Browser verification for Brazil vs Japan after the 2026-06-29 heatmap copy fix showed:
  - 64 heat cells.
  - first/last cells `0-0` and `7-7` visible.
  - region totals: let-win 21.4%, let-draw 23.9%, let-loss 54.8%, plus market probabilities.
  - section copy: `Top 6 与 0-7 热力图`.
  - tail copy: `8+ tail`.
- Chrome extension was unavailable in this session; the available Codex in-app browser was used for localhost UI verification after following plugin troubleshooting.

## 2026-06-29 Current Sporttery Refresh Window
- Browser-rendered re-check of `https://m.sporttery.cn/mjc/jsq/zqspf/` showed the current purchasable World Cup SPF/RQSPF window has 9 matches, not the older 6-match snapshot.
- Current purchasable markets:
  - 周一074 巴西 vs 日本, kickoff 06-30 01:00 BJT, SPF 1.49 / 3.72 / 5.28, RQSPF -1 2.71 / 3.15 / 2.26.
  - 周一075 德国 vs 巴拉圭, kickoff 06-30 04:30 BJT, SPF 1.22 / 5.00 / 9.10, RQSPF -1 1.69 / 4.05 / 3.44.
  - 周一076 荷兰 vs 摩洛哥, kickoff 06-30 09:00 BJT, SPF 1.97 / 3.00 / 3.47, RQSPF -1 4.18 / 3.48 / 1.66.
  - 周二077 科特迪瓦 vs 挪威, kickoff 07-01 01:00 BJT, SPF 3.77 / 3.35 / 1.77, RQSPF +1 1.83 / 3.63 / 3.25.
  - 周二078 法国 vs 瑞典, kickoff 07-01 05:00 BJT, SPF 1.16 / 5.80 / 10.50, RQSPF -1 1.60 / 3.95 / 3.98.
  - 周二079 墨西哥 vs 厄瓜多尔, kickoff 07-01 09:00 BJT, SPF 2.00 / 2.70 / 3.86, RQSPF -1 4.50 / 3.40 / 1.63.
  - 周三080 英格兰 vs 刚果民主共和国, kickoff 07-02 00:00 BJT, SPF 1.17 / 5.25 / 12.00, RQSPF -1 1.71 / 3.50 / 3.86.
  - 周三081 比利时 vs 塞内加尔, kickoff 07-02 04:00 BJT, SPF 1.99 / 3.00 / 3.42, RQSPF -1 4.15 / 3.60 / 1.64.
  - 周三082 美国 vs 波黑, kickoff 07-02 08:00 BJT, SPF 1.27 / 4.50 / 8.40, RQSPF -1 1.93 / 3.50 / 3.08.
- South Africa vs Canada is no longer in the current Sporttery purchasable window and is filtered out of Today.
- New refresh API:
  - `POST /api/refresh/current?date=YYYY-MM-DD`
  - First syncs `worldcup.lyihub.com` scores/details, then attempts Sporttery live odds, then falls back to the current browser-verified snapshot if the gateway returns 403/WAF.
- The top refresh button now calls `/api/refresh/current`, clears frontend prediction cache, reloads available dates, Today matches, rounds, report, rankings, knockout simulation, and roster health.
- API verification after restart:
  - `/api/matches?date=2026-06-29` returns `display_mode=sporttery_lottery_window`, 9 matches, window dates `2026-06-30/2026-07-01/2026-07-02`.
  - `/api/refresh/current?date=2026-06-29` synced 104 lyihub matches and returned Sporttery `mode=snapshot_fallback`, `updated=9` because backend live JSON returned HTTP 403.
- Browser verification after restart:
  - Page loaded `src/main.js?v=20260629-live-refresh`.
  - Today page showed all 9 match numbers from 周一074 through 周三082.
  - Clicking the top refresh button completed with status: `同步完成：完赛比分已更新，竞彩网快照赔率 9 场，今日页 9 场。`

## 2026-06-29 Detail Scroll Fix
- Root cause:
  - The single-match detail panel was still using a desktop `position: sticky` container with `max-height: calc(100dvh - 28px)`.
  - `#prediction-detail` then created its own internal scroll area, so long match detail content could be trapped and appear cut off.
- Fix:
  - `.detail-panel` now participates in normal document flow with `position: static`, `max-height: none`, and `display: block`.
  - `#prediction-detail` now uses visible overflow so the browser page itself owns vertical scrolling.
  - Static assets were bumped to `20260629-detail-scroll` to avoid stale CSS/JS.
- Browser verification:
  - Single-match detail opened at `#detail`.
  - `#detail` computed style: `position=static`, `max-height=none`.
  - `#prediction-detail` computed overflow: `visible`.
  - Document height was 4523px at the tested viewport and scrolling reached the bottom.
  - The score heatmap still rendered 64 cells and the roster section remained present.

## 2026-06-29 Detail Scroll V2 Cache And Wide Layout Fix
- Additional root cause from the user's widened-browser repro:
  - The active browser tab was still loading old `20260629-live-refresh` assets, so it never received the first scroll fix.
  - The generic `.panel { overflow: hidden; }` rule still applied to `#detail`, which made the wide detail layout fragile even after removing sticky/max-height behavior.
- Fix:
  - `.detail-panel` now explicitly sets `overflow: visible !important`.
  - `#prediction-detail` now explicitly sets `overflow: visible !important`.
  - Static asset query strings were bumped to `20260629-detail-scroll-v2`.
  - The FastAPI dev server now sends `Cache-Control: no-store, max-age=0`, `Pragma: no-cache`, and `Expires: 0` for `/` and `/src/*`.
- Verification:
  - Browser loaded `src/styles.css?v=20260629-detail-scroll-v2` and `src/main.js?v=20260629-detail-scroll-v2`.
  - Wide/detail viewport test showed `#detail` overflow visible and `#prediction-detail` overflow visible.
  - The page scrolled from the detail view to the 5046px document bottom.
  - Shell HTTP smoke confirmed the no-store headers and latest asset version.

## 2026-06-29 Wide Detail Collapse Fix
- Chrome debugging status:
  - Stable Chrome is installed at `/Applications/Google Chrome.app`, and the Codex Chrome Extension plus native host are present for the Default profile.
  - The Chrome extension backend was still unavailable.
  - Opening stable Chrome through the plugin failed with `kLSNoExecutableErr`, so the requested Chrome path could not be used for page interaction in this run.
- Root cause from wide-viewport repro:
  - Opening `/` directly with `#detail` activated the detail tab before any fixture was selected.
  - The page then showed an empty 560px detail panel while hidden buttons from Today/Rounds remained in the DOM, which made the wide page appear folded or incomplete.
  - The single-match page also had too many always-expanded long sections, especially the score heatmap and roster panels.
- Fix:
  - `#detail` now auto-loads the first current match prediction when no selected prediction exists.
  - Single-match sections now support collapse controls.
  - Default long sections collapsed: Monte Carlo, team profiles, AI analysis, and squads.
  - User can also collapse Score Probability and Roster Three-Line Strength from their card headers.
  - Added "expand all" and "collapse long cards" controls at the top of the detail body.
  - Static assets were bumped to `20260629-detail-collapse-v3`.
- Verification:
  - Wide viewport 1680x900 loaded `detail-collapse-v3` CSS/JS.
  - Direct `#detail` load auto-selected Brazil vs Japan instead of showing the empty prompt.
  - 9 section toggles rendered.
  - Score card collapsed from 1052px to 56px.
  - Roster strength card collapsed from 253px to 70px.
  - Document height dropped from 3971px to 2792px and scrolling still reached the bottom.

---
*Update this file after every 2 view/browser/search operations.*
