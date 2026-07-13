# Codex for Open Source application guide

Checked against the live OpenAI form on 2026-07-13: <https://openai.com/form/codex-for-oss/>.

The form requires the GitHub profile and repository to be public. The repository is currently public at <https://github.com/HectorGao/worldcup-prediction2026>; its default branch is `main`, and the live GitHub count at review time was 0 stars. The form says applications are reviewed on a rolling basis.

Do not submit until the open-source readiness pull request is reviewed and merged into `main`.

## Field-by-field guide

| Form field | Recommended entry |
| --- | --- |
| Last name | **[MANUAL INPUT REQUIRED: your legal/preferred last name]** |
| First name | **[MANUAL INPUT REQUIRED: your legal/preferred first name]** |
| Email | **[MANUAL INPUT REQUIRED: the email associated with your ChatGPT account]** |
| GitHub username | `HectorGao` |
| GitHub repository URL | `https://github.com/HectorGao/worldcup-prediction2026` |
| Role | Select **Primary maintainer** if you own final maintenance and release decisions. Otherwise select **Core maintainer**. |
| Why is this repository eligible? | Paste the first copy-ready answer below. Maximum 500 characters. |
| I am interested in | Select **Codex Security** and **API credits for the project** if you intend to use both for repository maintenance. |
| OpenAI organization ID | **[MANUAL INPUT REQUIRED: copy the organization ID from https://platform.openai.com/settings/organization/general]** |
| How will you use API credits? | Paste the second copy-ready answer below. Maximum 500 characters. |
| Anything else? | Paste the third copy-ready answer below. Maximum 500 characters. |

Before submitting, confirm that your GitHub user profile remains publicly visible, `main` contains the merged open-source files, all CI checks pass, and the form's linked program terms are acceptable to you.

## Copy-ready English answers

### Why is this repository eligible?

> worldcup-prediction2026 is a newly public, maintainer-led football forecasting project. It combines reproducible Dixon-Coles, Elo, Poisson, Monte Carlo, ensemble, and optional XGBoost workflows with a FastAPI dashboard and rights-aware public data snapshots. It currently has 0 GitHub stars and no tracked package downloads; its value is as a transparent, testable reference for sports-model engineering and responsible data publication.

### How will you use API credits for your project?

> I would use API credits for maintainer tooling, not to make the open-source application depend on a paid API. Planned uses include issue classification, contributor documentation assistance, regression-test generation, pull-request summaries, data-provenance checks, release-note drafting, and maintenance automation with human review. Credentials and restricted provider data will remain outside the repository.

### Anything else?

> I am the repository owner and primary maintainer, responsible for algorithms, data updates, tests, releases, security, and contributor review. The project separates Apache-2.0 code from third-party data rights, publishes sanitized reproducible snapshots, and keeps restricted raw provider payloads private. Codex support would help me sustain documentation, triage, reviews, tests, security checks, and careful future development as the community grows.

## Longer project narrative for follow-up questions

The project provides a local-first 2026 World Cup prediction dashboard. Contributors can improve documented model assumptions, deterministic tests, accessibility, translations, data provenance, provider adapters that respect source terms, and reproducible release tooling. Maintenance includes synchronizing match facts, regenerating sanitized snapshots, evaluating model regressions, reviewing source-license changes, triaging issues, reviewing pull requests, and supporting dynamic and static deployments.

Codex would be useful as a supervised maintainer assistant: drafting and reviewing documentation, producing focused test cases, summarizing changes, checking repository policy, identifying security risks, helping triage issues, reviewing pull requests, and reducing routine release work. Final decisions, data-rights judgments, secret handling, and releases remain human-reviewed.

## Truthfulness notes

- Do not claim download counts, adoption, contributors, or accuracy metrics unless you later collect verifiable evidence.
- Keep the current 0-star statement unless the GitHub count changes before submission; refresh it on the submission day.
- The project is open source and publicly visible, but this application should not describe it as critical infrastructure or broadly used software without evidence.
- Do not state that OpenAI API features already exist in the runtime. The proposed credits are for future maintainer tooling.
- The form submission itself is a personal external action and has not been completed by this repository change.
