# World Cup Prediction System Deployment

目标域名：`worldcup.hectorgao.com`

## 1. 当前项目结构判断

这个项目不是纯静态页面。

- 前端：`index.html` + `src/main.js` + `src/styles.css`，没有 Vite/React 构建步骤。
- 后端：Python FastAPI，入口为 `worldcup_predictor.api:app`。
- 前端通过同源 `/api/...` 调用后端接口。
- 后端同时负责：
  - 托管 `/` 和 `/src/*` 静态资源；
  - 提供预测、赛果同步、sporttery 赔率刷新、回归训练等 API；
  - 使用 SQLite 数据库，默认路径为 `data/worldcup.sqlite3`。

因此当前系统应部署为一个 Python Web Service，而不是 GitHub Pages / Cloudflare Pages 这种纯静态站。

## 2. 推荐免费部署方案

推荐使用 Render Free Web Service。

原因：

- 支持 Python + FastAPI 长运行服务。
- 免费额度可以公开访问。
- 支持自定义域名和自动 HTTPS。
- 可以用一个服务同时托管前端页面和后端 API，避免跨域和前后端分离部署。
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
WORLDCUP_CORS_ORIGINS=https://worldcup.hectorgao.com
SPORTTERY_ENABLE_LIVE=1
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

如果你需要长期保留线上同步后的 SQLite 数据，有两个选择：

1. 使用 Render Disk。这个通常不是免费方案。
2. 不在网页上频繁执行同步，把本地生成好的 `outputs/*.json` 和数据库作为发布前数据快照管理。

当前免费方案的建议是：线上用于公开展示和轻量刷新；重要数据同步和回归仍在本地或 Codex 中运行后提交输出文件。

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
{"status":"ok"}
```

3. API 可访问：

```text
https://worldcup.hectorgao.com/api/meta
https://worldcup.hectorgao.com/api/matches/available-dates
```

4. 页面功能检查：

- 顶部页面能加载；
- “今日比赛”能请求 `/api/matches`；
- “刷新赔率”能调用 `/api/odds/sporttery/refresh`；
- “同步”能调用 `/api/results/update`；
- “单场预测”能调用 `/api/predict/{fixture_id}`。

Render Free 服务首次访问可能冷启动，等待 30-60 秒后刷新即可。

## 7. 国内访问考虑

免费平台没有国内 SLA。大致判断：

- Render：部署简单，公开访问稳定性通常可以接受，但国内访问可能偶尔慢。
- Railway：类似 Render，免费额度策略变化较多。
- Vercel / Netlify / Cloudflare Pages：国内访问通常不错，但不适合当前 FastAPI + SQLite 后端形态。
- Hugging Face Spaces：适合 Python Demo，但国内访问不一定稳定。

如果必须显著优化国内访问，最终通常需要国内云服务或 CDN 备案方案；这超出“不购买服务器”的约束。

## 8. 常见问题

### 页面打开但数据为空

当前仓库不一定包含 `data/worldcup.sqlite3`。线上第一次启动会自动创建空数据库。

处理方式：

- 在页面点击“同步”或“刷新参考数据”补数据；
- 或者在本地运行同步/回归流程后，将需要公开展示的数据文件随代码提交；
- 如果要保留线上运行后生成的数据，需要使用持久化磁盘。

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

- 查看页面提示和 Render Logs；
- 确认 `SPORTTERY_ENABLE_LIVE=1`；
- 失败时系统应保留上一次 sporttery snapshot，不应生成假赔率。

### 同步或回归耗时较长

Render Free 机器资源有限，赛果同步、赔率抓取和 XGBoost 回归可能耗时较长。

建议：

- 公开页面主要用于查看和轻量刷新；
- 大规模同步和回归在本地或 Codex 环境运行；
- 运行完成后提交更新后的 `outputs/` 文件。
