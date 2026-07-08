# Static Deployment

This project can be published as a free, GitHub Pages-like static site. The public site does not run the FastAPI server. GitHub Actions runs the existing Python prediction code, exports static JSON into `dist/api/`, and Cloudflare Pages serves `dist/`.

## Local Export

```bash
PYTHONPATH=backend python scripts/export_static_site.py
python -m http.server 8788 --directory dist
```

Open `http://127.0.0.1:8788/`. The static build sets `window.WORLDCUP_STATIC_BUILD = true`, so the frontend reads `/api/*.json` files and disables mutation actions such as sync, odds refresh, source validation, and roster enrichment.

By default, the exporter publishes the current/default match date to keep scheduled builds fast. Use this for a full historical export:

```bash
PYTHONPATH=backend python scripts/export_static_site.py --all-dates
```

Full team details can trigger expensive squad-strength recomputation, so the default static site exports lightweight team detail payloads. Use this only for an occasional manual full export:

```bash
PYTHONPATH=backend python scripts/export_static_site.py --full-team-details
```

## GitHub Actions Secrets

Required for Cloudflare Pages deploy:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

Optional data-source secrets used during export:

- `ODDS_API_KEY`
- `BETFAIR_APP_KEY`
- `BETFAIR_SESSION_TOKEN`
- `FOOTBALL_DATA_API_KEY`
- `FOOTBALLDATA_IO_API_KEY`
- `API_FOOTBALL_KEY`
- `SPORTMONKS_API_KEY`
- `KAGGLE_USERNAME`
- `KAGGLE_KEY`

`SPORTTERY_ENABLE_LIVE` is forced to `0` in CI so the public build uses cached/snapshot data instead of depending on a fragile live browser-only source.

## Cloudflare Pages

Create a Pages project named `worldcup-hectorgao`. The workflow deploys with:

```bash
pages deploy dist --project-name=worldcup-hectorgao
```

In Cloudflare Pages, add the custom domain:

```text
worldcup.hectorgao.com
```

Cloudflare will show the target Pages domain, for example:

```text
worldcup-hectorgao.pages.dev
```

## Aliyun DNS

In Alibaba Cloud DNS, add one record:

```text
Record type: CNAME
Host record: worldcup
Record value: <your Cloudflare Pages target, for example worldcup-hectorgao.pages.dev>
TTL: default
```

Do not add an A record for `worldcup.hectorgao.com` at the same time.

## Public Site Behavior

- `https://worldcup.hectorgao.com/` serves the static dashboard.
- `https://worldcup.hectorgao.com/api/meta.json` shows the static export time and fixture count.
- Prediction details are pre-generated at export time.
- Buttons that would normally mutate backend state are read-only notices in static mode.
