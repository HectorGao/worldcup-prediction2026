# Open Source Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the complete `worldcup-prediction2026` application, current distributable data, and a safe repeatable maintenance workflow as a professionally governed open-source repository.

**Architecture:** Preserve all runtime and prediction files, then add a documentation and governance layer around the existing FastAPI, static frontend, SQLite snapshot, generated outputs, and precomputed API snapshots. Treat code licensing, data rights, security checks, CI validation, and daily Git maintenance as separate concerns with explicit release gates.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, pytest, vanilla HTML/CSS/JavaScript, GitHub Actions, POSIX shell, Markdown.

## Global Constraints

- Do not modify runtime logic, prediction logic, data-processing behavior, user interface, API behavior, CLI behavior, local startup workflow, or functional content.
- Preserve commit `851b7e240f2a9c9a7f6bd71d490fa78886c76ea0` in branch history.
- Code is licensed under Apache License 2.0; third-party data and generated outputs are governed separately.
- Do not delete data with uncertain redistribution status; report it and wait for the maintainer's decision.
- Do not expose secret values in terminal output, documentation, commits, or reports.
- Do not force-push, rewrite history, automatically stage every file, automatically commit, or automatically push.
- Keep `data/worldcup.sqlite3`, `precomputed/api/`, and reviewed `outputs/` in Git unless a blocking security or redistribution finding requires a maintainer decision.

---

### Task 1: Establish audit baselines and classify repository content

**Files:**
- Create: `docs/audits/open-source-readiness-baseline.md`
- Reference: `docs/superpowers/specs/2026-07-13-open-source-readiness-design.md`
- Reference: `pyproject.toml`
- Reference: `package.json`
- Reference: `backend/worldcup_predictor/`
- Reference: `data/worldcup.sqlite3`
- Reference: `outputs/`
- Reference: `precomputed/api/`

**Interfaces:**
- Consumes: current `codex/open-source-readiness` worktree and Git history.
- Produces: a path-level baseline and classification used by all later release gates.

- [ ] **Step 1: Capture the immutable starting references**

Run:

```bash
git branch --show-current
git rev-parse HEAD
git rev-parse 851b7e2
git status --short
git diff --name-only 5853ae8 -- backend src index.html scripts pyproject.toml package.json Procfile render.yaml runtime.txt start_local_server.command tests
```

Expected: branch is `codex/open-source-readiness`; `851b7e2` resolves; existing data changes are visible; no maintenance work has modified application paths.

- [ ] **Step 2: Inventory data by path, size, tracked state, and role**

Run:

```bash
du -sh data outputs precomputed
find data outputs precomputed -type f -size +50M -print
git status --short -- data outputs precomputed
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
```

Expected: SQLite reports `ok`; no file currently exceeds 50 MB except any newly discovered file, which must be recorded.

- [ ] **Step 3: Write the baseline audit**

Create `docs/audits/open-source-readiness-baseline.md` with these exact sections:

```markdown
# Open Source Readiness Baseline

## Branch and commit baseline
## Application paths protected from maintenance edits
## Data intended for publication
## Files intended to remain ignored
## Large-file assessment
## Local-only commit 851b7e2
## Open audit questions
```

Record filenames, categories, sizes, and counts without copying secrets or third-party payload contents.

- [ ] **Step 4: Verify the baseline contains no placeholders or secret values**

Run:

```bash
rg -n 'TBD|TODO|REPLACE_ME|api[_-]?key\s*[=:]\s*[^[:space:]]+' docs/audits/open-source-readiness-baseline.md
git diff --check -- docs/audits/open-source-readiness-baseline.md
```

Expected: no placeholder or credential-like match; `git diff --check` exits 0.

### Task 2: Audit secrets, personal information, and Git history

**Files:**
- Create: `scripts/check_repository_safety.sh`
- Create: `tests/test_repository_maintenance.py`
- Modify: `.env.example`
- Modify: `.gitignore`
- Create: `docs/audits/security-audit.md`

**Interfaces:**
- Consumes: working tree, staged files, reachable Git objects, and `data/worldcup.sqlite3`.
- Produces: `scripts/check_repository_safety.sh [--staged]`, which exits nonzero for prohibited paths or likely credentials and prints paths/categories without secret values.

- [ ] **Step 1: Add failing tests for maintenance safety behavior**

Add tests that copy the script to a temporary Git repository and verify:

```python
def test_safety_script_rejects_tracked_dotenv(tmp_path): ...
def test_safety_script_rejects_staged_private_key(tmp_path): ...
def test_safety_script_accepts_safe_env_example(tmp_path): ...
def test_safety_script_warns_for_file_over_50_mb(tmp_path): ...
```

The tests must assert only filenames and category labels, never the matched credential text.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/pytest -q tests/test_repository_maintenance.py
```

Expected: FAIL because `scripts/check_repository_safety.sh` does not exist.

- [ ] **Step 3: Implement the safety script**

The script must use `set -eu`, support `--staged`, and perform these checks:

```text
prohibited exact paths: .env
prohibited suffixes: .pem, .key, .p12, .pfx
credential categories: private key header, GitHub token, OpenAI key, AWS access key, generic assigned secret/token/password
large-file warning threshold: 50 MiB
hard file-size failure threshold: 95 MiB
SQLite checks: integrity plus schema/column-name review
output rule: print path and category only, never matching lines
```

Use `git ls-files`, `git diff --cached --name-only`, `rg -l`, `find`, and `sqlite3`; do not use `git add`, `git commit`, or `git push`.

- [ ] **Step 4: Harden `.gitignore` and `.env.example`**

Keep `.env` ignored. Replace the broad SQLite rule with explicit transient patterns and allow the canonical database:

```gitignore
.env
.env.*
!.env.example
*.sqlite3-wal
*.sqlite3-shm
*.sqlite3-journal
*.db-wal
*.db-shm
*.db-journal
!data/worldcup.sqlite3
```

Ensure `.env.example` contains only documented variable names with empty or safe values.

- [ ] **Step 5: Run current-tree, database, staged-content, and history scans**

Run the local script and an independent history scan. If a likely real credential appears, record only commit ID, path, and category, stop all push work, and request a maintainer decision.

Expected: `.env` remains untracked; no secret value appears in output; all findings are classified in `docs/audits/security-audit.md`.

- [ ] **Step 6: Run maintenance tests**

Run:

```bash
.venv/bin/pytest -q tests/test_repository_maintenance.py
scripts/check_repository_safety.sh
```

Expected: tests pass and safety script exits 0, or publication is blocked with a path-only report.

- [ ] **Step 7: Commit security configuration**

```bash
git add .gitignore .env.example scripts/check_repository_safety.sh tests/test_repository_maintenance.py docs/audits/security-audit.md
git diff --cached --check
git commit -m "chore: add repository safety controls"
```

### Task 3: Verify third-party data rights and document the data model

**Files:**
- Create: `DATA.md`
- Create: `docs/audits/data-redistribution-review.md`
- Modify: `docs/audits/open-source-readiness-baseline.md`

**Interfaces:**
- Consumes: provider implementations, provider websites/terms, database table inventory, `outputs/`, and `precomputed/api/`.
- Produces: authoritative data-source, attribution, redistribution, update, and reproduction guidance.

- [ ] **Step 1: Enumerate source categories from code and stored metadata**

Review FIFA, Wikipedia/Wikimedia, Kaggle, Lyihub, SportMonks, API-Football, football-data.org, The Odds API, Betfair, China Sporttery, and any additional source named in the database or output metadata.

- [ ] **Step 2: Verify current terms from primary sources**

For each provider, record:

```text
source name
official terms or license URL
data categories used
whether raw redistribution is explicitly allowed, restricted, prohibited, or unclear
whether attribution is required
repository paths affected
recommended treatment
date verified
```

Use official provider documentation or terms; use Wikimedia licensing pages for Wikipedia-derived content.

- [ ] **Step 3: Apply the publication decision gate**

If any checked-in data has restricted or unclear redistribution rights, do not delete or stage it. Add it to `docs/audits/data-redistribution-review.md` and stop before the data commit until the maintainer decides.

- [ ] **Step 4: Write `DATA.md`**

Include:

```markdown
# Data Guide and Rights Notice
## Code license versus data rights
## Repository data layout
## Data-source register
## Included data snapshots
## Data users must obtain themselves
## SQLite snapshot
## Updating data
## Reproducing precomputed API data
## Update frequency and provenance
## Reporting data-rights concerns
```

State that Apache-2.0 does not grant rights to third-party data or generated outputs derived from it.

- [ ] **Step 5: Validate data documentation links and placeholders**

Run:

```bash
rg -n 'TBD|TODO|example\.com' DATA.md docs/audits/data-redistribution-review.md
git diff --check -- DATA.md docs/audits/data-redistribution-review.md
```

Expected: no placeholders; whitespace check passes.

### Task 4: Review and version the complete data snapshot

**Files:**
- Add: `data/worldcup.sqlite3`
- Modify/Add: `outputs/**`
- Modify/Add: `precomputed/api/**`
- Modify: `DATA.md`

**Interfaces:**
- Consumes: approved redistribution review and current local data state.
- Produces: the complete data snapshot required for local cards, reproducibility, and hosted read-only API responses.

- [ ] **Step 1: Confirm approval for every risky data category**

Expected: no unresolved `restricted` or `unclear` row affects files proposed for staging. If unresolved, stop this task without deleting files.

- [ ] **Step 2: Validate data integrity and JSON syntax**

Run:

```bash
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
find outputs precomputed/api -type f -name '*.json' -print0 | xargs -0 -n1 python -m json.tool >/dev/null
```

Expected: SQLite reports `ok`; every JSON file parses.

- [ ] **Step 3: Review all data changes before staging**

Run:

```bash
git status --short -- data outputs precomputed/api
git diff --stat -- data outputs precomputed/api
find data outputs precomputed/api -type f -size +50M -print
```

Expected: current SQLite snapshot, 155 reviewed modifications, and two reviewed team directories are accounted for; every file over 50 MB is explained.

- [ ] **Step 4: Stage explicit data roots only after audit approval**

Run:

```bash
git add data/worldcup.sqlite3 outputs precomputed/api DATA.md docs/audits/data-redistribution-review.md docs/audits/open-source-readiness-baseline.md
scripts/check_repository_safety.sh --staged
git diff --cached --stat
```

Expected: only reviewed data and data documentation are staged; safety scan passes.

- [ ] **Step 5: Commit the data snapshot and documentation**

```bash
git commit -m "data: publish reproducible project snapshots"
```

### Task 5: Add open-source documentation and community health files

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `SECURITY.md`
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

**Interfaces:**
- Consumes: actual repository commands, `DATA.md`, deployment docs, source tree, and verified project behavior.
- Produces: contributor-facing project documentation and GitHub community templates.

- [ ] **Step 1: Add the unmodified Apache License 2.0 text**

Use the official Apache License 2.0 text and set the project copyright notice only where the standard permits.

- [ ] **Step 2: Write the English README from repository evidence**

Include title, purpose, existing features, existing screenshots only if found, stack, structure, prerequisites, installation, local startup, configuration, data sources, methodology, outputs, limitations, status, roadmap, contribution, security, code license, data rights, and experimental-prediction disclaimer.

Document the validated local command exactly:

```bash
.venv/bin/uvicorn worldcup_predictor.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

- [ ] **Step 3: Add contribution, conduct, and security policies**

Require focused commits, tests for behavior changes, explicit data provenance, no secrets, and responsible private vulnerability reporting through GitHub's security advisory feature when available.

- [ ] **Step 4: Add structured issue and pull request templates**

Templates must ask for reproduction details, environment, scope, tests, data-source changes, licensing impact, and confirmation that no secrets are included.

- [ ] **Step 5: Validate documentation locally**

Run:

```bash
git diff --check -- README.md LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md .github
rg -n 'TBD|TODO|REPLACE_ME|your.email@example.com' README.md CONTRIBUTING.md SECURITY.md .github
```

Expected: no placeholders and no whitespace errors.

- [ ] **Step 6: Commit community documentation**

```bash
git add README.md LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md .github/ISSUE_TEMPLATE .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs: add open source community files"
```

### Task 6: Add non-invasive CI and daily maintenance workflow

**Files:**
- Create: `.github/workflows/validate.yml`
- Create: `.github/dependabot.yml` only if dependency ecosystems are supported without changing dependency versions.
- Create: `scripts/review_daily_update.sh`
- Modify: `CONTRIBUTING.md`
- Modify: `README.md`
- Modify: `tests/test_repository_maintenance.py`

**Interfaces:**
- Consumes: existing pytest and static-export commands plus the safety script.
- Produces: read-only CI validation and `scripts/review_daily_update.sh`, which prepares a report but never stages, commits, or pushes.

- [ ] **Step 1: Add failing tests for the daily review script**

Add tests verifying the script:

```python
def test_daily_review_script_never_invokes_git_add_commit_or_push(): ...
def test_daily_review_script_reports_code_and_data_changes_separately(): ...
def test_daily_review_script_runs_safety_check_before_tests(): ...
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```bash
.venv/bin/pytest -q tests/test_repository_maintenance.py
```

Expected: FAIL because `scripts/review_daily_update.sh` does not exist.

- [ ] **Step 3: Implement the review-only daily script**

It must run, in order:

```text
branch/status summary
separate code/config and data change lists
large-file report
repository safety check
SQLite integrity check
pytest suite
static export validation
final manual staging/commit/push instructions
```

It must contain no `git add`, `git commit`, or `git push` command execution.

- [ ] **Step 4: Add validation CI**

Use `actions/checkout@v4`, `actions/setup-python@v5`, Python 3.11, `pip install -e '.[dev]'`, repository safety checks, SQLite integrity validation, `pytest -q`, and the existing static export command. Do not add new source formatting, linting, or typing rules.

- [ ] **Step 5: Add conservative Dependabot configuration if appropriate**

Configure monthly review-only pull requests for `pip` and `github-actions`, with a low open-PR limit. Do not update application dependencies in this task.

- [ ] **Step 6: Verify maintenance workflow**

Run:

```bash
.venv/bin/pytest -q tests/test_repository_maintenance.py
bash -n scripts/check_repository_safety.sh scripts/review_daily_update.sh
scripts/review_daily_update.sh
```

Expected: tests pass; shell syntax passes; the review script completes without staging or pushing.

- [ ] **Step 7: Commit CI and tooling**

```bash
git add .github/workflows/validate.yml .github/dependabot.yml scripts/review_daily_update.sh tests/test_repository_maintenance.py README.md CONTRIBUTING.md
git commit -m "ci: add safe maintenance validation"
```

### Task 7: Perform clean-environment and behavior-preservation verification

**Files:**
- Create: `docs/audits/final-verification.md`
- Do not modify: `backend/**`, `src/**`, `index.html`, runtime scripts, prediction files, or application tests except the pre-existing `851b7e2` changes.

**Interfaces:**
- Consumes: final branch tree and original starting references.
- Produces: evidence that the repository is installable, data-complete, tested, and behavior-preserving.

- [ ] **Step 1: Compare protected application paths against the maintenance baseline**

Run:

```bash
git diff --name-status 5853ae8 -- backend src index.html scripts/export_static_site.py scripts/update_after_results.py scripts/validate_data_sources.py pyproject.toml package.json Procfile render.yaml runtime.txt start_local_server.command
```

Expected: only newly added maintenance scripts appear under `scripts/`; no existing runtime or application file changed after `5853ae8`.

- [ ] **Step 2: Run full existing tests**

Run:

```bash
.venv/bin/pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Verify database and static export**

Run:

```bash
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py
```

Expected: SQLite reports `ok`; export exits 0 and produces `dist/` without staging it.

- [ ] **Step 4: Verify documented startup in an isolated temporary environment**

Create a temporary virtual environment outside the repository, install the project with dev dependencies, start Uvicorn with the README command, and request `/healthz`, `/`, and one documented API route. Stop the process after checks.

Expected: installation succeeds; health endpoint is successful; page and selected API response are non-empty.

- [ ] **Step 5: Run final security and staged-content audits**

Run:

```bash
scripts/check_repository_safety.sh
git status --short
git log --oneline origin/main..HEAD
git diff --check origin/main...HEAD
```

Expected: no unresolved security finding, all intended commits visible, and no whitespace errors.

- [ ] **Step 6: Record final evidence**

Document exact commands, exit codes, test counts, data inventory, ignored-file list, secret result, LFS decision, protected-path diff, limitations, and any manual decisions in `docs/audits/final-verification.md`.

- [ ] **Step 7: Commit audit reports**

```bash
git add docs/audits/open-source-readiness-baseline.md docs/audits/final-verification.md
git commit -m "docs: record open source readiness audit"
```

### Task 8: Push branch, create pull request, and prepare the application guide

**Files:**
- Create: `docs/codex-oss-application-guide.md`
- Modify: `docs/audits/final-verification.md` only to add final branch, commit, PR, and remote references.

**Interfaces:**
- Consumes: verified branch and current official OpenAI application form.
- Produces: remote branch, draft or ready pull request, merge-gate report, and copy-ready application responses.

- [ ] **Step 1: Inspect the current official application form**

Open `https://openai.com/form/codex-for-oss/` and record every current field, requirement, and relevant program statement. Use official OpenAI sources only.

- [ ] **Step 2: Write the field-by-field application guide**

For each field, provide a truthful answer based on the final repository. Mark personal contact details, usage metrics, organization details, and other unavailable facts as `[MANUAL INPUT REQUIRED]`.

- [ ] **Step 3: Run the complete release gate again**

Run:

```bash
.venv/bin/pytest -q
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
scripts/check_repository_safety.sh
git diff --check origin/main...HEAD
git status --short
```

Expected: all tests pass; database reports `ok`; safety check passes; only intentionally uncommitted files remain.

- [ ] **Step 4: Push without force**

Run:

```bash
git push -u origin codex/open-source-readiness
```

Expected: branch is created or updated normally on `origin`; no force option is used.

- [ ] **Step 5: Create a pull request without merging**

The PR body must summarize documentation, security controls, data publication, CI, tests, runtime-preservation evidence, data-rights findings, and required maintainer decisions. It must explicitly state that application behavior was not intentionally changed.

- [ ] **Step 6: Deliver the pre-merge report**

Report:

```text
files added and modified
public data directories and file types
ignored files and reasons
secret and personal-information findings
third-party redistribution findings
Git LFS decision
contents of commit 851b7e2
validation and clean-start results
runtime preservation result
branch, commit, repository URL, and PR URL
unresolved risks and limitations
field-by-field application guide
copy-ready English answers
```
