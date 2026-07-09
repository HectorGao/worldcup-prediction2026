# World Cup Prediction System Deployment

目标域名：`worldcup.hectorgao.com`

## 1. 当前项目结构判断

这个项目不是纯静态页面。

- 前端：`index.html` + `src/main.js` + `src/styles.css`，没有 Vite/React 构建步骤。
- 后端：Python FastAPI，入口为 `worldcup_predictor.api:app`。
- 前端通过同源 `/api/...` 调用后端接口。
- 本地后端负责：
  - 托管 `/` 和 `/src/*` 静态资源；
  - 提供预测、赛果同步、sporttery 赔率刷新、回归训练等 API；
  - 使用 SQLite 数据库，默认路径为 `data/worldcup.sqlite3`。

因此当前系统部署为一个 Python Web Service。线上 Render 使用只读预计算模式，只读取仓库里的 `precomputed/api/*.json`，不在用户请求中执行赛果同步、赔率抓取或模型重算。

## 2. 推荐免费部署方案

推荐使用 Render Free Web Service。

原因：

- 支持 Python + FastAPI 服务。
- 免费额度可以公开访问。
- 支持自定义域名和自动 HTTPS。
- 可以用一个服务同时托管前端页面和只读 API，避免跨域和前后端分离部署。
- 不需要购买服务器。

备选方案：

- Railway：也适合 FastAPI，但免费额度和计费策略变化较频繁。
- Hugging Face Spaces：可运行 Python Web App，但国内访问通常不如普通 Web Service 稳定，自定义域名也不如 Render 直接。
- Vercel / Netlify：更适合静态前端或轻量 Serverless；本项目包含长耗时同步、SQLite 和模型回归，不是首选。
- GitHub Pages / Cloudflare Pages：只能部署静态前端，除非重构为纯静态读取 JSON；当前不推荐，因为会丢失同步、预测、回归等后端功能。

## 3. 已添加的部署适配

项目中已补充：

- `render.yaml`：Render Blueprint 配置。
- `Procfile`：兼容 Render/Railway/Heroku 风格启动方式。
- `runtime.txt`：声明 Python 版本。
- `/healthz`：部署平台健康检查接口。
- `WORLDCUP_DB_PATH`：线上 SQLite 路径环境变量。
- `WORLDCUP_CORS_ORIGINS`：可追加允许跨域来源。
- `WORLDCUP_USE_PRECOMPUTED`：线上优先读取 `precomputed/api`。
- `WORLDCUP_READ_ONLY`：线上禁用同步、赔率刷新、模型回归等重任务接口。
- `precomputed/api/`：可提交的线上 API JSON 缓存。

核心预测算法、赔率解析、赛果同步、模型训练逻辑没有为了部署而改动。

## 4. Render 部署步骤

### 4.1 推送代码到 GitHub

先把当前分支推送到 GitHub 仓库。

```bash
git status
git push origin HEAD
```

### 4.2 在 Render 创建 Web Service

1. 打开 [Render Dashboard](https://dashboard.render.com/)。
2. 选择 `New` -> `Web Service`。
3. 连接 GitHub 仓库。
4. 选择当前项目仓库和要部署的分支。
5. Render 如果识别到 `render.yaml`，可以选择 Blueprint 部署；否则手动填写下面配置。

手动配置：

- Runtime: `Python 3`
- Build Command:

```bash
pip install --upgrade pip && pip install .
```

- Start Command:

```bash
uvicorn worldcup_predictor.api:app --app-dir backend --host 0.0.0.0 --port $PORT
```

- Health Check Path:

```text
/healthz
```

### 4.3 环境变量

必填：

```text
WORLDCUP_DB_PATH=/opt/render/project/src/data/worldcup.sqlite3
WORLDCUP_CORS_ORIGINS=https://worldcup-prediction2026.onrender.com,https://worldcup.hectorgao.com
WORLDCUP_USE_PRECOMPUTED=1
WORLDCUP_READ_ONLY=1
WORLDCUP_PRECOMPUTED_DIR=/opt/render/project/src/precomputed
SPORTTERY_ENABLE_LIVE=0
```

可选数据源 API key：

```text
SPORTMONKS_API_KEY=...
FOOTBALLDATA_IO_API_KEY=...
API_FOOTBALL_KEY=...
FOOTBALL_DATA_API_KEY=...
```

说明：

- 不要把 API key 写入前端或仓库。
- 如果不配置可选 API key，相关数据源会显示未配置，系统仍可使用已有 fallback 数据源。
- Render Free 服务的文件系统不是持久存储。服务重启或重新部署后，运行时生成的 SQLite 数据可能丢失。
- 线上只读模式不依赖运行时 SQLite 数据；比赛、预测、球队、淘汰赛和健康检查快照来自仓库里的 `precomputed/api`。

如果你需要长期保留线上同步后的 SQLite 数据，有两个选择：

1. 使用 Render Disk。这个通常不是免费方案。
2. 不在网页上执行同步，把本地生成好的 `outputs/*.json` 和 `precomputed/api/*.json` 作为发布前数据快照管理。

当前免费方案的建议是：线上用于公开展示；重要数据同步、赔率刷新和回归仍在本地或 Codex 中运行后提交输出文件与预计算 API 缓存。

### 4.4 更新线上比赛数据的本地流程

线上 Render 不执行重计算。需要更新比赛数据时，在本地运行：

```bash
SPORTTERY_ENABLE_LIVE=1 .venv/bin/python scripts/update_after_results.py --fetch-online-results --use-xgboost --recalculate --sync-fifa --sync-fifa-rosters --sync-sportmonks --use-sportmonks --sync-footballdata-io --sync-sporttery --sync-sporttery-history --backfill-historical --train-over25 --date 2026-07-08
```

脚本会默认导出线上只读 API 缓存到 `precomputed/api/`。如果只想更新本地输出、不刷新部署缓存，可额外加 `--skip-precomputed-export`。

也可以单独导出线上只读 API 缓存：

```bash
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py --precomputed --all-dates --simulations 10000
```

确认 `precomputed/api/` 已更新后提交并推送：

```bash
git add outputs precomputed render.yaml DEPLOYMENT.md backend src scripts tests
git commit -m "fix: resolve render api 502 and support precomputed match data"
git push origin main
```

本地网页按钮同样会自动刷新部署缓存：在非只读本地服务中点击“同步”“回归”“刷新赔率”“刷新参考信息”后，后端会在操作完成时重新写入 `precomputed/api/`，响应中的 `precomputed_export` 字段会记录导出状态。

## 5. 绑定 `worldcup.hectorgao.com`

### 5.1 在 Render 添加自定义域名

1. 打开 Render 对应 Web Service。
2. 进入 `Settings` -> `Custom Domains`。
3. 添加：

```text
worldcup.hectorgao.com
```

Render 会显示一个目标域名，通常类似：

```text
worldcup-prediction.onrender.com
```

以 Render 页面实际显示为准。

### 5.2 配置 DNS

在你的 DNS 服务商处添加一条记录：

```text
Type: CNAME
Name: worldcup
Value: <Render 提供的目标域名，例如 worldcup-prediction.onrender.com>
TTL: Auto 或 600
```

如果使用 Cloudflare 管理 DNS：

- 可以先设置为 `DNS only`，等 Render 证书签发成功后再考虑开启代理。
- 如果开启代理后出现证书或访问异常，改回 `DNS only`。

### 5.3 验证 HTTPS

DNS 生效后访问：

```text
https://worldcup.hectorgao.com
```

Render 会自动签发 TLS 证书。首次生效可能需要几分钟到几十分钟。

## 6. 验证部署成功

部署完成后检查：

1. 页面可访问：

```text
https://worldcup.hectorgao.com
```

2. 健康检查可访问：

```text
https://worldcup.hectorgao.com/healthz
```

应返回：

```json
{"status":"ok","deployment":{"read_only":true,"use_precomputed":true}}
```

3. API 可访问：

```text
https://worldcup.hectorgao.com/api/meta
https://worldcup.hectorgao.com/api/matches/available-dates
https://worldcup.hectorgao.com/api/matches?date=2026-07-08
```

4. 页面功能检查：

- 顶部页面能加载；
- “今日比赛”能请求 `/api/matches`；
- “刷新赔率”“同步”“回归”等按钮显示只读提示，不会在线上执行重任务；
- “单场预测”读取预计算预测结果。

Render Free 服务首次访问可能冷启动，等待 30-60 秒后刷新即可。冷启动后 `/api/matches` 应快速返回，不应触发线上同步或模型重算。

## 7. 国内访问考虑

免费平台没有国内 SLA。大致判断：

- Render：部署简单，公开访问稳定性通常可以接受，但国内访问可能偶尔慢。
- Railway：类似 Render，免费额度策略变化较多。
- Vercel / Netlify / Cloudflare Pages：国内访问通常不错，但不适合当前 FastAPI + SQLite 后端形态。
- Hugging Face Spaces：适合 Python Demo，但国内访问不一定稳定。

如果必须显著优化国内访问，最终通常需要国内云服务或 CDN 备案方案；这超出“不购买服务器”的约束。

## 8. 常见问题

### 页面打开但数据为空

线上不依赖 `data/worldcup.sqlite3`。如果页面打开但数据为空，优先检查 `precomputed/api` 是否已提交并随 Render 部署发布。

处理方式：

- 本地运行同步、赔率刷新和回归流程；
- 确认 `precomputed/api` 已由本地 API 操作或 `scripts/update_after_results.py` 自动刷新；必要时手动运行 `scripts/export_static_site.py --precomputed --all-dates`；
- 提交 `precomputed/api` 后重新部署；
- 不要提交 `data/*.sqlite3`。

### Render 构建失败，提示找不到包

确认 Build Command 是：

```bash
pip install --upgrade pip && pip install .
```

确认 Start Command 是：

```bash
uvicorn worldcup_predictor.api:app --app-dir backend --host 0.0.0.0 --port $PORT
```

### 自定义域名无法访问

检查：

- Render 中是否已经添加 `worldcup.hectorgao.com`；
- DNS 是否有 `CNAME worldcup -> Render 目标域名`；
- Cloudflare 是否暂时设置为 `DNS only`；
- Render 证书是否已经签发完成。

### sporttery 刷新失败

原因通常是目标网页限流、网络访问、WAF 或页面结构变化。

处理方式：

- 在本地环境刷新 sporttery 赔率；
- 确认本地 `SPORTTERY_ENABLE_LIVE=1`；
- 失败时系统应保留上一次 sporttery snapshot，不应生成假赔率；
- 线上 Render 设置 `SPORTTERY_ENABLE_LIVE=0`，页面只读取预计算赔率快照。

### 同步或回归耗时较长

Render Free 机器资源有限，赛果同步、赔率抓取和 XGBoost 回归可能耗时较长。

建议：

- 公开页面只用于查看预计算结果；
- 大规模同步和回归在本地或 Codex 环境运行；
- 运行完成后提交更新后的 `outputs/` 和 `precomputed/api/` 文件。
