# Repository Security Audit

Audit date: 2026-07-13 (Asia/Shanghai)

## Scope

The audit covered:

- tracked and untracked worktree files, excluding ignored local dependencies and caches;
- staged-content behavior through automated tests;
- reachable Git history;
- whether `.env` was tracked now or previously;
- the SQLite schema, integrity, and high-confidence credential patterns in stored content;
- files approaching GitHub's per-file size limit.

The audit reports paths and credential categories only. No matched value is copied into this report.

## Environment files

- `.env` exists locally, is ignored by the first `.gitignore` rule, and is not tracked.
- No reachable commit was found that tracked `.env` at that exact path.
- `.env.example` is tracked and contains variable names with empty or non-secret defaults only.
- The ignore rules exclude `.env` variants while explicitly allowing `.env.example`.

## Current worktree scan

The repository safety script found no high-confidence private-key, GitHub-token, OpenAI-key, AWS-key, or assigned credential pattern in files eligible for version control. The scan also found no prohibited credential-file suffix in the publication set.

The scanner deliberately does not print matching lines. It rejects `.env`, common private credential file formats, high-confidence token patterns, files over 95 MiB, a corrupt SQLite database, or sensitive-looking database column names. Files over 50 MiB generate a warning.

## Git history scan

Reachable history was checked without printing patch contents for:

- private-key headers;
- GitHub personal, OAuth, user, server, refresh, and fine-grained token patterns;
- OpenAI secret-key patterns;
- AWS access-key identifiers;
- long assigned values whose variable names indicate API keys, tokens, passwords, or secrets.

No matching commit or path was found. This is a high-confidence pattern audit, not a mathematical guarantee that arbitrary or unusually formatted credentials never existed. Credential rotation remains the appropriate response if a provider later reports exposure.

## SQLite review

`data/worldcup.sqlite3` passed `PRAGMA integrity_check`. Its schema has no column whose name indicates a password, token, secret, API key, email address, or phone number. A high-confidence strings scan found no private-key, GitHub-token, OpenAI-key, or AWS-key pattern in stored content.

The database contains public football information, including player names, team names, matches, scores, odds snapshots, provider payloads, and source URLs. No private contact information was identified by the schema and pattern review. Third-party data rights are addressed separately in the redistribution review.

## Large files

No intended publication file exceeded 50 MiB during this audit. The approximately 41 MB SQLite snapshot remains below the warning threshold and GitHub's 100 MB hard limit. Git LFS is not required for this publication.

## Controls added

- `scripts/check_repository_safety.sh` performs read-only worktree or staged-content checks.
- `tests/test_repository_maintenance.py` verifies that `.env` and private keys are rejected, `.env.example` is accepted, and large files are reported.
- `.gitignore` continues to protect local environment files and SQLite transient sidecars while allowing `data/worldcup.sqlite3` to be versioned.

The safety script never stages, commits, pushes, deletes, rotates, or rewrites anything.

## Result

No credential or private-contact-information blocker was identified. Publication remains conditional on the separate third-party data redistribution review and final staged-content scan.
