# Task Plan: World Cup Roster Coverage and Historical Window

## Goal
Improve the World Cup prediction system so decisions ignore matches older than 16 years and current-match roster/player strength coverage is materially improved through verified free/public data sources, with persistent evidence and progress tracked on disk.

## Current Phase
Phase 7 complete

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

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| None yet | 1 | Pending implementation. |
| TheSportsDB 429 Too Many Requests | 1 | Reduced public enrichment batch size to 10 and left failed items retryable. |
| `data/worldcup.db` did not contain roster tables | 1 | Confirmed FastAPI uses `data/worldcup.sqlite3`; final verification uses the service database. |
| Two Uzbekistan players could not be matched by TheSportsDB | 1 | Verified API-Football 2024 player statistics for Abdulla Abdullayev and Behruzjon Karimov, reset the exhausted queue item once, and processed both through the API-Football queue. |

## Notes
- User requirement: matches older than 16 years must not participate in decisions.
- User requirement: roster/player-stat coverage is currently too low and should be improved via web/browser research and usable free/public data.
- User requirement: if a needed source requires manual entry/registration, ask the user to perform that step.
- Final verification: no remaining manual entry is needed for the currently synced England, Portugal, and Uzbekistan squads; roster health is 78/78 players with stats and 0 failed queue items.
- New user goal on 2026-06-25: when viewing yesterday/earlier dates, show all matches for that date with actual score, predicted score, and prediction accuracy; clicking a team should show its World Cup matches and squad with player club; use `worldcup.lyihub.com` as data/UI reference; show group-stage and knockout round cards; validate coverage across all 48 teams.
