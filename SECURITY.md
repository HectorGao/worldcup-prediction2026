# Security Policy

## Supported version

Security fixes are applied to the current `main` branch. Historical snapshots and forks are not separately supported.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature for this repository when available: open the repository's **Security** tab, choose **Advisories**, then **Report a vulnerability**. Do not open a public issue for an unpatched vulnerability, exposed credential, private-data leak, or bypass of repository safety controls.

Include the affected revision, reproducible steps, impact, and a minimal proof of concept. Do not include real credentials or third-party personal data. Maintainers will acknowledge a complete report as capacity allows, investigate it privately, and coordinate disclosure after a fix is available.

## Credentials and private data

- Never commit `.env`, API keys, tokens, passwords, session cookies, database credentials, or provider account material.
- Use `.env.example` only for variable names and safe example values.
- Restricted raw provider payloads belong under ignored `.private_data/`.
- Run `scripts/check_repository_safety.sh --staged` before each commit.

If a secret may have been committed, revoke or rotate it immediately. Report the affected path and revision privately. Do not rewrite repository history without explicit maintainer coordination because published history changes affect every clone.

## Scope

Reports about the project's own code, deployment configuration, credential handling, and published data boundary are in scope. Accuracy disagreements about match predictions are not security vulnerabilities, though reproducible integrity or data-provenance defects are welcome as normal issues.
