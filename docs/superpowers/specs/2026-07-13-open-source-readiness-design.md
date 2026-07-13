# Open Source Readiness Design

## Goal

Publish `worldcup-prediction2026` as a complete, continuously maintained open-source project without changing its runtime logic, prediction logic, data-processing behavior, user interface, API behavior, CLI behavior, or local startup workflow.

## Scope and constraints

- Preserve the current application code and behavior.
- Preserve commit `851b7e240f2a9c9a7f6bd71d490fa78886c76ea0`, which enables precomputed data automatically on Render and adds API tests.
- Work on `codex/open-source-readiness` and open a pull request without automatically merging it.
- Do not force-push, rewrite history, remove unrelated branches, change repository visibility, or expose secrets.
- Do not delete data with uncertain copyright or redistribution status. Report risks and wait for the maintainer's decision.
- Separate the code license from data rights and third-party terms.

## Repository publication model

Use a self-contained public-data Git repository. Version the application source, tests, deployment configuration, CC0 inputs, sanitized project outputs, sanitized precomputed web snapshots, and a compliant SQLite data snapshot. Preserve restricted raw provider data in an ignored local `.private_data/` tree; do not delete or publish it.

The intended versioned data includes:

- `data/worldcup.sqlite3`, because the local dynamic application depends on its fixtures, historical matches, predictions, and model records; the published copy must exclude restricted raw provider payloads, odds snapshots, and unlicensed player/roster feeds;
- `precomputed/api/`, because the read-only hosted site uses these JSON snapshots;
- `outputs/`, when the files are current predictions, evaluation results, update reports, or other artifacts needed for presentation or reproducibility;
- new team snapshot directories currently present under `precomputed/api/teams/`, after restricted roster/player and provider fields are removed.

Before generating the public snapshot, copy the private source database, outputs, and precomputed snapshots into `.private_data/`. The public snapshot builder reads from that ignored source tree, never deletes it, and overwrites only the canonical public snapshot paths with sanitized data.

Do not classify a file as disposable solely because it is generated. Ignore only secrets, virtual environments, Python caches, operating-system files, editor state, build directories, SQLite transient files, and demonstrably disposable temporary files.

## Database and large-file policy

The current SQLite snapshot is approximately 41 MB, while `precomputed/` and `outputs/` are approximately 14 MB and 7.4 MB. No current versioned file requires Git LFS under GitHub's 100 MB hard limit. Keep the database in normal Git for this release because the maintainer explicitly requires all distributable data in Git and Git LFS is not currently installed.

Add a maintenance check that warns before files approach the GitHub limit. Re-evaluate Git LFS if the database or another individual file grows substantially, or if binary database deltas cause unacceptable repository growth. Document the trade-off so future maintainers can migrate deliberately.

## Licensing and data rights

- License project-authored source code under Apache License 2.0.
- Add `DATA.md` to document every data-source category, attribution, ownership, redistribution status, update frequency, acquisition path, and reproduction command.
- State explicitly that Apache-2.0 does not relicense third-party match data, team data, player data, odds, raw provider responses, or generated results derived from third-party inputs.
- Distinguish data that may be distributed in the repository from data users must obtain themselves.
- Review the current terms of each relevant provider before publication, including FIFA, Wikipedia/Wikimedia, Kaggle datasets, Lyihub, SportMonks, API-Football, football-data.org, The Odds API, Betfair, China Sporttery, and any fallback feeds found during implementation.
- If redistribution rights are unclear or adverse, record the affected files and recommended remediation without deleting them. Stop before push and request a maintainer decision.

## Security model

- Keep `.env` ignored and untracked.
- Keep `.env.example` with variable names and safe empty or non-secret example values only.
- Scan the worktree, staged content, SQLite schema and stored payloads, and reachable Git history for credentials and personal information without printing secret values.
- Add a maintainer-run safety script that reports affected paths and categories, checks staged files, rejects `.env`, and warns about likely credentials and oversized files.
- The maintenance workflow must never automatically stage every file, commit, or push.
- If a reachable historical commit contains a real credential, stop the publication workflow and report rotation and history-remediation options. Do not rewrite history without explicit approval.

## Documentation and community files

Create an accurate English `README.md` derived from the repository. Cover purpose, implemented features, stack, structure, prerequisites, installation, current startup commands, configuration, data sources, methodology, outputs, limitations, status, roadmap, contributions, security, code license, data rights, and the experimental-predictions disclaimer.

Add:

- `LICENSE` with Apache License 2.0;
- `CONTRIBUTING.md`;
- `CODE_OF_CONDUCT.md`;
- `SECURITY.md`;
- `DATA.md`;
- issue templates and configuration;
- a pull request template.

Do not claim unverified metrics, coverage, adoption, deployment status, accuracy, contributors, users, downloads, or stars.

## CI and maintainer workflow

Reuse existing test and export commands. Add only non-invasive validation that does not require source changes, such as the existing pytest suite, static export, repository safety checks, and documentation/link checks when they can run without imposing new formatting rules.

Provide a documented daily workflow that lets the maintainer:

1. update data or algorithms using the existing project commands;
2. inspect code and data changes;
3. run security, database-integrity, size, and test checks;
4. stage selected paths explicitly;
5. review the staged diff;
6. commit with a descriptive message;
7. push intentionally.

## Commit and pull request structure

Split changes into at least these commits:

1. open-source documentation and community health files;
2. `.gitignore`, `.env.example`, and security configuration;
3. reviewed data snapshots and data documentation;
4. CI and maintenance tooling.

Before pushing, compare application-related files against the starting state and confirm that this work did not modify runtime logic or functional content. The pre-existing commit `851b7e2` remains part of branch history and is not attributed to the open-source maintenance commits.

## Verification and release gate

The pull request may be pushed only after:

- tests and applicable existing build/export commands pass;
- the SQLite database passes `PRAGMA integrity_check`;
- worktree, staged-content, database, and history secret scans complete without an unresolved credential;
- public data and ignored-file inventories are complete;
- third-party data redistribution risks are documented and any blocking risk has a maintainer decision;
- no unintended runtime, prediction, UI, API, CLI, or startup-file changes are present;
- commit scope and branch history are reviewed.

Before merge, report the public data inventory, ignored files and reasons, credential findings, Git LFS decision, the contents of local-only commit `851b7e2`, and the complete pull request change list.

## Codex for Open Source Maintainers application

After repository work is verified, inspect the current application form at `https://openai.com/form/codex-for-oss/`. Produce a field-by-field guide and truthful, proportionate English answers based on the final repository. Mark personal, organizational, usage, or contact details that only the maintainer can supply.
