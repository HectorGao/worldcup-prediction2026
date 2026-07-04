const state = {
  matches: [],
  selectedPrediction: null,
  matchPredictions: {},
  availableDates: [],
  sourceValidation: [],
  rosterWeight: 0.25,
  simulations: 10000,
  rosterHealth: null,
  rounds: [],
  roundMatches: [],
  selectedStage: null,
  activeView: 'rounds',
  selectedTeamDetail: null,
  currentMatchMeta: null,
  lastUpdateSummary: null,
  roundSyncSummary: null,
  roundRegressionSummary: null,
  lastPredictionParams: null,
  predictionParamsDirty: false,
  lastCalculationTime: null,
  detailAutoloading: false,
  collapsedPredictionSections: {
    'monte-carlo': true,
    profiles: true,
    'ai-analysis': true,
    squads: true
  }
};

const API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';
const formatPercent = (value) => `${(value * 100).toFixed(1)}%`;
const statusEl = () => document.querySelector('#status');

function setStatus(message, kind = '') {
  const node = statusEl();
  node.textContent = message;
  node.className = `status-line ${kind}`.trim();
  node.setAttribute('aria-busy', kind ? 'false' : 'true');
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function selectedDate() {
  return document.querySelector('#match-date').value || state.availableDates.at(-1) || '2026-06-29';
}

function teamDisplay(entity, side = '') {
  if (!entity) return '-';
  const flag = side ? entity[`${side}_flag`] || '' : entity.flag || '';
  const zh = side ? entity[`${side}_team_zh`] : entity.team_zh;
  const canonical = side ? entity[`${side}_team`] : entity.team;
  return `${flag ? `${flag} ` : ''}${zh || canonical || '-'}`;
}

async function bootstrap() {
  try {
    updateActiveView();
    await loadAvailableDates();
    await loadMatches();
    await loadHealth();
    await loadRosterHealth();
    await loadRounds();
    await loadReport();
    await loadRankings();
    await loadKnockout();
    if (state.activeView === 'detail') {
      await ensureDetailPrediction();
    } else {
      setStatus('数据已加载。公开网页赛程与历史 CSV/Elo fallback 可用。', 'success');
    }
  } catch (error) {
    setStatus(`加载失败：${error.message}。请确认后端服务已启动并可访问 /api。`, 'error');
  }
}

async function loadAvailableDates() {
  const payload = await api('/api/matches/available-dates');
  state.availableDates = payload.dates;
  const input = document.querySelector('#match-date');
  if (payload.default_date) {
    input.value = payload.default_date;
  } else if (state.availableDates.length) {
    input.value = state.availableDates[state.availableDates.length - 1];
  }
}

async function loadMatches() {
  const date = selectedDate();
  setStatus(`正在加载 ${date} 的比赛...`);
  const payload = await api(`/api/matches?date=${date}`);
  state.matches = payload.matches;
  state.currentMatchMeta = payload;
  document.querySelector('#match-date').value = payload.date || date;
  renderMatches();
  setStatus(matchStatusMessage(payload), 'success');
  loadMatchCardPredictions();
}

async function syncMatches() {
  const date = selectedDate();
  const button = document.querySelector('#sync-button');
  button.disabled = true;
  button.textContent = '...';
  setStatus('正在获取线上真实赛果、更新球队参数并重新预测未完赛比赛...');
  try {
    const payload = await api(`/api/results/update?fetch_online_results=true&use_xgboost=true&recalculate=true&sync_fifa=true&sync_fifa_rosters=true&sync_sportmonks=true&use_sportmonks=true&sync_footballdata_io=true&sync_sporttery_odds=true&sync_sporttery_history=true&backfill_historical_matches=true&train_over25=true&date=${encodeURIComponent(date)}`, { method: 'POST' });
    state.lastUpdateSummary = payload;
    state.matchPredictions = {};
    state.selectedPrediction = null;
    await loadAvailableDates();
    document.querySelector('#match-date').value = date;
    await loadMatches();
    await loadRounds();
    await loadReport();
    await loadRankings();
    await loadKnockout();
    await loadRosterHealth();
    renderUpdateSummary(payload);
    setStatus(
      `同步完成：今日完赛 ${payload.today_finished_matches?.length ?? 0} 场，本届已完赛 ${payload.world_cup_finished_match_count ?? 0} 场，重算预测 ${payload.prediction_count ?? 0} 条。`,
      'success'
    );
  } catch (error) {
    setStatus(`同步失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '↻';
  }
}

async function refreshSportteryOdds() {
  const button = document.querySelector('#sporttery-refresh-button');
  button.disabled = true;
  button.textContent = '刷新中...';
  setStatus('正在刷新竞彩当前页面赔率...');
  try {
    const payload = await api('/api/odds/sporttery/refresh', { method: 'POST' });
    await loadMatches();
    renderSportteryRefreshSummary(payload);
    const groupCount = Object.keys(payload.grouped_by_date || {}).length;
    setStatus(
      `赔率刷新完成：${payload.mode}，更新 ${payload.updated ?? 0} 场，比赛日 ${groupCount} 组，未匹配 ${(payload.unmatched_matches || []).length} 场。`,
      'success'
    );
  } catch (error) {
    setStatus(`赔率刷新失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '刷新赔率';
  }
}

async function syncRoundOverview() {
  const date = selectedDate();
  const button = document.querySelector('#round-sync-button');
  button.disabled = true;
  button.textContent = '同步中...';
  setStatus('正在按顺序同步完赛结果、竞彩赔率，并重新预测竞彩页面比赛...');
  try {
    const payload = await api(`/api/rounds/sync?date=${encodeURIComponent(date)}`, { method: 'POST' });
    state.roundSyncSummary = payload;
    state.lastUpdateSummary = payload.result_sync;
    state.matchPredictions = {};
    await loadAvailableDates();
    await loadMatches();
    await loadRounds();
    await loadReport();
    await loadRankings();
    await loadKnockout();
    await loadRosterHealth();
    renderRoundSyncSummary(payload);
    setStatus(
      `赛程同步完成：完赛 ${payload.summary?.finished_total ?? 0} 场，竞彩赔率更新 ${payload.summary?.sporttery_updated ?? 0} 场，重算 ${payload.summary?.prediction_count ?? 0} 场。`,
      'success'
    );
  } catch (error) {
    setStatus(`赛程同步失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '同步';
  }
}

async function regressRoundOverview() {
  const date = selectedDate();
  const button = document.querySelector('#round-regress-button');
  button.disabled = true;
  button.textContent = '回归中...';
  setStatus('正在用已完赛比赛回归球队参数，并重新预测未完赛比赛...');
  try {
    const payload = await api(`/api/rounds/regress?date=${encodeURIComponent(date)}&auto_sync=true`, { method: 'POST' });
    state.roundRegressionSummary = payload;
    if (payload.sync) {
      state.roundSyncSummary = payload.sync;
    }
    state.matchPredictions = {};
    await loadMatches();
    await loadRounds();
    await loadRankings();
    await loadKnockout();
    renderRoundRegressionSummary(payload);
    setStatus(
      payload.needs_sync
        ? '回归需要先同步完赛结果。'
        : `回归完成：使用 ${payload.finished_match_count ?? 0} 场本届世界杯完赛样本，重算 ${payload.unfinished_predictions?.length ?? 0} 场未完赛比赛。`,
      payload.needs_sync ? 'error' : 'success'
    );
  } catch (error) {
    setStatus(`回归失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '回归';
  }
}

function renderUpdateSummary(payload) {
  const target = document.querySelector('#daily-report');
  if (!target || !payload) return;
  const regression = payload.regression_evaluation || {};
  const r32 = payload.r32_regression?.after || {};
  const retraining = payload.retraining || {};
  const today = payload.today_finished_matches || [];
  const advanced = payload.advanced_teams || [];
  const eliminated = payload.eliminated_teams || [];
  const lines = [
    `今日完赛：${today.length} 场`,
    ...today.map((match) => `${match.home_team} ${match.home_goals_90}-${match.away_goals_90} ${match.away_team}${match.decided_by_penalties ? `，点球 ${match.home_penalties}-${match.away_penalties}` : ''}`),
    `晋级：${advanced.length ? advanced.join('、') : '暂无'}`,
    `淘汰：${eliminated.length ? eliminated.join('、') : '暂无'}`,
    `本届已完赛：${payload.world_cup_finished_match_count ?? 0} 场`,
    `本届世界杯数据权重：${safePercent(retraining.world_cup_data_weight)}`,
    `XGBoost：${payload.xgboost?.available ? '已导入并参与融合' : payload.xgboost?.engine || 'fallback'}`,
    `回归检验：胜平负 ${safePercent(regression.accuracy_90)} · LogLoss ${formatNumber(regression.log_loss)} · Brier ${formatNumber(regression.brier_score)} · 进球MAE ${formatNumber(regression.goals_mae)}`,
    r32.match_count ? `1/16回归：90分钟 ${safePercent(r32.accuracy_90)} · 平局召回 ${r32.draw_recall == null ? '无平局样本' : safePercent(r32.draw_recall)} · Brier ${formatNumber(r32.brier_score)}` : null
  ];
  target.textContent = lines.filter(Boolean).join('\n');
}

function renderSportteryRefreshSummary(payload) {
  const target = document.querySelector('#daily-report');
  if (!target || !payload) return;
  const grouped = payload.grouped_by_date || {};
  const lines = [
    `竞彩赔率刷新：${payload.mode || 'unknown'}`,
    `更新时间：${formatDateTime(payload.last_updated)}`,
    `更新本地比赛：${payload.updated ?? 0} 场`,
    `未匹配比赛：${(payload.unmatched_matches || []).length} 场`,
    `被过滤比赛：${(payload.filtered_matches || []).length} 场`,
    ...Object.entries(grouped).map(([date, rows]) => `${date}：${rows.length} 场 ${rows.map((row) => row.match_num || `${row.home_team}-${row.away_team}`).join('、')}`),
  ];
  if ((payload.unmatched_matches || []).length) {
    lines.push('未匹配赔率比赛：');
    payload.unmatched_matches.forEach((row) => {
      lines.push(`- ${row.match_num || '-'} ${row.home_team || '-'} vs ${row.away_team || '-'}：${row.reason || 'no_local_fixture_match'}`);
    });
  }
  if ((payload.filtered_matches || []).length) {
    lines.push('被过滤赔率比赛：');
    payload.filtered_matches.forEach((row) => {
      lines.push(`- ${row.match_num || '-'} ${row.home_team || '-'} vs ${row.away_team || '-'}：${row.reason || 'unknown'}`);
    });
  }
  if ((payload.match_results || []).length) {
    lines.push('赔率匹配明细：');
    payload.match_results.forEach((row) => {
      lines.push(`- ${row.match_num || '-'} ${row.home_team || '-'} vs ${row.away_team || '-'}：${row.status || '-'} / ${row.reason || '-'}`);
    });
  }
  if (payload.last_error) {
    lines.push(`失败原因：${payload.last_error}`);
  }
  target.textContent = lines.join('\n');
}

function renderRoundSyncSummary(payload = state.roundSyncSummary) {
  const target = document.querySelector('#round-sync-summary');
  if (!target) return;
  if (!payload) {
    target.innerHTML = '';
    return;
  }
  const lastSync = payload.last_sync_time || payload.completed_at;
  const lastSyncNode = document.querySelector('#round-last-sync');
  if (lastSyncNode) {
    lastSyncNode.textContent = `最后同步 ${formatDateTime(lastSync)}`;
  }
  const result = payload.result_sync || {};
  const sporttery = payload.sporttery || {};
  const predictions = payload.predictions || [];
  target.innerHTML = `
    <div class="sync-summary-card">
      <strong>同步结果</strong>
      <span>完赛同步 ${result.world_cup_finished_match_count ?? 0} 场 · 今日完赛 ${result.today_finished_matches?.length ?? 0} 场 · 竞彩赔率 ${sporttery.mode || 'unknown'} 更新 ${sporttery.updated ?? 0} 场</span>
      <small>${sporttery.source || 'Sporttery'} · ${formatDateTime(lastSync)}</small>
    </div>
    <div class="round-prediction-strip">
      ${predictions.length ? predictions.slice(0, 8).map(roundPredictionChip).join('') : '<span class="empty-state">暂无竞彩可售比赛预测。</span>'}
    </div>
  `;
}

function renderRoundRegressionSummary(payload = state.roundRegressionSummary) {
  const target = document.querySelector('#round-regression-view');
  if (!target) return;
  if (!payload) {
    target.innerHTML = '';
    return;
  }
  if (payload.needs_sync) {
    target.innerHTML = `<div class="empty-state">${payload.message || '请先同步赛果，再执行回归。'}</div>`;
    return;
  }
  const evaluation = payload.regression_evaluation || {};
  const r32 = payload.r32_regression || {};
  const r32After = r32.after || {};
  const r32Before = r32.before || {};
  const r32Weights = r32.blend_weights || {};
  const predictions = payload.current_sporttery_predictions?.length
    ? payload.current_sporttery_predictions
    : (payload.unfinished_predictions || []).slice(0, 12);
  target.innerHTML = `
    <div class="regression-head">
      <div>
        <strong>回归后的新预测结果</strong>
        <span>本届世界杯数据权重 ${safePercent(payload.world_cup_data_weight)} · 完成 ${formatDateTime(payload.completed_at)}</span>
      </div>
      <div class="regression-metrics">
        <span>胜平负 ${safePercent(evaluation.accuracy_90)}</span>
        <span>LogLoss ${formatNumber(evaluation.log_loss)}</span>
        <span>Brier ${formatNumber(evaluation.brier_score)}</span>
      </div>
    </div>
    ${r32.after ? `
      <div class="regression-r32-summary">
        <strong>1/16淘汰赛90分钟回归</strong>
        <span>优化前 ${safePercent(r32Before.accuracy_90)} · 优化后 ${safePercent(r32After.accuracy_90)}</span>
        <span>平局召回 ${r32After.draw_recall == null ? '无平局样本' : safePercent(r32After.draw_recall)} · LogLoss ${formatNumber(r32After.log_loss)} · Brier ${formatNumber(r32After.brier_score)}</span>
        <small>权重 Poisson ${safePercent(r32Weights.poisson)} · Monte Carlo ${safePercent(r32Weights.monte_carlo)} · XGBoost ${safePercent(r32Weights.xgboost)} · 市场 ${safePercent(r32Weights.market)} · 平局校准 ${safePercent(r32Weights.draw_adjustment)}</small>
      </div>
    ` : ''}
    <div class="regression-grid">
      ${predictions.length ? predictions.map(regressionPredictionCard).join('') : '<div class="empty-state">暂无未完赛比赛可预测。</div>'}
    </div>
  `;
}

function roundPredictionChip(item) {
  if (item.status === 'final') {
    const result = item.result || {};
    return `
      <article class="prediction-chip final">
        <strong>${teamLabel(item, 'home')} ${result.home_goals_90 ?? '-'}-${result.away_goals_90 ?? '-'} ${teamLabel(item, 'away')}</strong>
        <span>完赛 · ${result.winner ? `晋级 ${result.winner}` : '结果已同步'}</span>
      </article>
    `;
  }
  return `
    <article class="prediction-chip">
      <strong>${teamLabel(item, 'home')} vs ${teamLabel(item, 'away')}</strong>
      <span>胜 ${safePercent(item.probabilities_90?.home)} · 平 ${safePercent(item.probabilities_90?.draw)} · 负 ${safePercent(item.probabilities_90?.away)}</span>
      <small>让球 ${handicapProbLine(item)} · 推荐 ${recommendationLabel(item.recommendation)}</small>
    </article>
  `;
}

function regressionPredictionCard(item) {
  const delta = item.probability_delta || {};
  return `
    <article class="regression-card">
      <div class="round-card-top">
        <span>${item.stage || 'World Cup'}</span>
        <span>${formatDateTime(item.kickoff)}</span>
      </div>
      <strong>${teamLabel(item, 'home')} vs ${teamLabel(item, 'away')}</strong>
      <div class="regression-probs">
        <span>胜 ${safePercent(item.probabilities_90?.home)} <i>${deltaBadge(delta.home)}</i></span>
        <span>平 ${safePercent(item.probabilities_90?.draw)} <i>${deltaBadge(delta.draw)}</i></span>
        <span>负 ${safePercent(item.probabilities_90?.away)} <i>${deltaBadge(delta.away)}</i></span>
      </div>
      <div class="round-card-meta">让球 ${handicapProbLine(item)}</div>
      <div class="round-card-meta">${advanceLine(item)} · 推荐 ${recommendationLabel(item.recommendation)}</div>
    </article>
  `;
}

function teamLabel(item, side) {
  const flag = item[`${side}_flag`] || '';
  const zh = item[`${side}_team_zh`];
  const canonical = item[`${side}_team`];
  return `${flag ? `${flag} ` : ''}${zh || canonical || '-'}`;
}

function handicapProbLine(item) {
  const probs = item.handicap_probabilities || {};
  if (!Object.keys(probs).length) return '暂无';
  const line = item.handicap_line === null || item.handicap_line === undefined ? '' : `(${item.handicap_line}) `;
  return `${line}胜 ${safePercent(probs.home)} · 平 ${safePercent(probs.draw)} · 负 ${safePercent(probs.away)}`;
}

function advanceLine(item) {
  const advance = item.advancement_probabilities;
  if (!advance) return '晋级概率：小组赛不适用';
  return `晋级 ${safePercent(advance.home)} / ${safePercent(advance.away)}`;
}

function deltaBadge(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || Math.abs(number) < 0.0001) return '±0.0%';
  return `${number > 0 ? '+' : ''}${(number * 100).toFixed(1)}%`;
}

function recommendationLabel(value) {
  if (!value) return '暂无';
  return outcomeLabel(value);
}

function formatDateTime(value) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  });
}

async function syncReferenceData() {
  const button = document.querySelector('#sync-reference-button');
  button.disabled = true;
  button.textContent = '刷新中...';
  setStatus('正在同步 worldcup.lyihub.com 全赛程、赛果和球员能力值...');
  try {
    const payload = await api('/api/scrape/lyihub?include_details=true&detail_limit=120', { method: 'POST' });
    await loadAvailableDates();
    await loadMatches();
    await loadRounds();
    await loadRosterHealth();
    setStatus(
      `参考数据已刷新：${payload.match_count} 场，详情 ${payload.detail_synced} 场，48队覆盖 ${payload.coverage.complete_teams}/${payload.coverage.expected_teams}。`,
      'success'
    );
  } catch (error) {
    setStatus(`参考数据刷新失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '刷新参考数据';
  }
}

async function loadPrediction(fixtureId) {
  setStatus('正在生成单场预测...');
  try {
    const prediction = await api(`/api/predict/${fixtureId}?roster_weight=${state.rosterWeight}&simulations=${state.simulations}`, { method: 'POST' });
    state.matchPredictions[fixtureId] = { status: 'ready', prediction };
    let analysis = null;
    try {
      analysis = await api(`/api/matches/${fixtureId}/analysis`);
    } catch (error) {
      setStatus(`核心预测已生成，详情分析加载失败：${error.message}`, 'error');
    }
    if (analysis?.prediction) {
      analysis = { ...analysis, prediction };
    }
    state.selectedPrediction = prediction;
    renderPrediction(state.selectedPrediction, analysis);
    await loadSquadPanels(state.selectedPrediction.fixture);
    renderSimulation(state.selectedPrediction);
    renderMatches();
    showView('detail');
    setStatus('单场预测已生成。', 'success');
    return prediction;
  } catch (error) {
    setStatus(`生成预测失败：${error.message}`, 'error');
    return null;
  }
}

async function ensureDetailPrediction() {
  if (state.selectedPrediction || state.detailAutoloading) return;
  const fixture = state.matches.find((match) => match.status !== 'final') || state.matches[0];
  if (!fixture) {
    document.querySelector('#prediction-detail').innerHTML = '<div class="empty-state">暂无可用比赛。请先进入“今日比赛”同步数据。</div>';
    return;
  }
  state.detailAutoloading = true;
  setStatus('正在为单场预测载入当前比赛...');
  try {
    await loadPrediction(fixture.id);
  } finally {
    state.detailAutoloading = false;
  }
}

async function loadMatchCardPredictions() {
  const missing = state.matches.filter((match) => !state.matchPredictions[match.id]);
  if (!missing.length) return;
  missing.forEach((match) => {
    state.matchPredictions[match.id] = { status: 'loading' };
  });
  renderMatches();
  await Promise.all(
    missing.map(async (match) => {
      try {
        const prediction = await api(`/api/predict/${match.id}?roster_weight=${state.rosterWeight}&simulations=${state.simulations}`, { method: 'POST' });
        state.matchPredictions[match.id] = { status: 'ready', prediction };
      } catch (error) {
        state.matchPredictions[match.id] = { status: 'error', error: error.message };
      }
      renderMatches();
    })
  );
}

async function loadRounds() {
  const payload = await api('/api/lyihub/rounds');
  state.rounds = payload.rounds || [];
  const stages = new Set(['all', ...state.rounds.map((round) => round.stage)]);
  if (!state.selectedStage || !stages.has(state.selectedStage)) {
    state.selectedStage = defaultRoundStage(state.rounds);
  }
  renderRounds();
  await loadRoundMatches(state.selectedStage);
}

async function loadRoundMatches(stage = 'all') {
  state.selectedStage = stage;
  const query = stage && stage !== 'all' ? `?stage=${encodeURIComponent(stage)}` : '';
  const payload = await api(`/api/lyihub/matches${query}`);
  state.roundMatches = payload.matches || [];
  renderRounds();
  renderRoundMatches();
}

async function loadHealth() {
  const health = await api('/api/health/data-sources');
  document.querySelector('#cadence').textContent = `${health.polling_cadence_minutes} 分钟轮询`;
  const sources = [...health.providers, ...(health.public_web_sources || [])];
  document.querySelector('#health-list').innerHTML = sources
    .map(
      (provider) => `
      <div class="health-item">
        <strong>${provider.name}</strong>
        <span>${provider.configured ? '已配置' : '未配置'} · ${provider.role}</span>
      </div>
    `
    )
    .join('');
}

async function loadRosterHealth() {
  const health = await api('/api/health/roster-data');
  state.rosterHealth = health;
  renderRosterHealth();
}

function renderRosterHealth() {
  const health = state.rosterHealth;
  if (!health) return;
  const provider = health.provider || {};
  document.querySelector('#roster-health').innerHTML = `
    <div class="health-item ${provider.configured ? 'ok' : 'warn'}">
      <strong>${provider.name || 'API-Football rosters'}</strong>
      <span>${provider.configured ? '已配置' : '未配置'} · season ${provider.season_priority?.join(' → ') || '2026 → 2025 → 2024'}</span>
    </div>
    <div class="health-item ${health.public_provider?.configured ? 'ok' : 'warn'}">
      <strong>${health.public_provider?.name || 'TheSportsDB public player search'}</strong>
      <span>${health.public_provider?.configured ? '已配置' : '未配置'} · ${health.public_provider?.rate_limit || '公开补全源'}</span>
    </div>
    <div class="health-item">
      <strong>补全队列</strong>
      <span>待处理 ${health.queue_pending} · 已完成 ${health.queue_done} · 失败 ${health.queue_failed}</span>
    </div>
    <div class="health-item">
      <strong>球员统计覆盖</strong>
      <span>${health.players_with_stats}/${health.players_total}</span>
    </div>
    ${(provider.manual_keys || [])
      .map(
        (item) => `
          <div class="health-item warn">
            <strong>${item.source}</strong>
            <span>可选手动注册：${item.env_var}</span>
          </div>
        `
      )
      .join('')}
  `;
}

async function validateSources() {
  const button = document.querySelector('#validate-sources-button');
  button.disabled = true;
  button.textContent = '校验中...';
  setStatus('正在校验 API key、额度和公开数据源...');
  try {
    const payload = await api('/api/health/validate-sources', { method: 'POST' });
    state.sourceValidation = payload.sources || [];
    renderSourceValidation();
    setStatus('数据源校验完成。', 'success');
  } catch (error) {
    setStatus(`数据源校验失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '校验数据源';
  }
}

function renderSourceValidation() {
  document.querySelector('#health-list').innerHTML = state.sourceValidation
    .map((source) => {
      const status = source.auth_valid ? '可用' : source.configured ? '不可用' : '未配置';
      const quota =
        source.quota_remaining === null || source.quota_remaining === undefined
          ? '额度未知'
          : `剩余额度 ${source.quota_remaining}`;
      const detail = source.last_error || `${quota} · 样本 ${source.sample_count ?? 0}`;
      return `
        <div class="health-item ${source.auth_valid ? 'ok' : 'warn'}">
          <strong>${source.name}</strong>
          <span>${status} · ${detail}</span>
        </div>
      `;
    })
    .join('');
}

async function loadReport() {
  const date = selectedDate();
  const payload = await api(`/api/reports/daily?date=${date}`);
  document.querySelector('#daily-report').textContent = payload.report;
}

async function loadRankings() {
  const payload = await api('/api/teams/rankings?alive_only=true');
  const rows = payload.teams.slice(0, 12);
  document.querySelector('#team-rankings').innerHTML =
    rows.length === 0
      ? '<div class="empty-state">暂无球队画像。点击同步数据获取历史比赛并计算 Elo。</div>'
      : rows
          .map(
            (team, index) => `
              <div class="rank-item">
                <strong>${index + 1}. ${teamDisplay(team)}</strong>
                <span>Elo ${team.elo} · 攻 ${team.attack_rating ?? '-'} · 中 ${team.midfield_rating ?? '-'} · 防 ${team.defense_rating ?? '-'} · 稳 ${team.defensive_stability ?? '-'} · 本届权重 ${safePercent(team.world_cup_data_weight)}</span>
              </div>
            `
          )
          .join('');
}

async function loadKnockout() {
  const payload = await api('/api/knockout');
  document.querySelector('#knockout-view').innerHTML = payload.champion_favorite
    ? `
      <div class="rank-item">
        <strong>冠军热门：${teamDisplay(payload.champion_favorite)}</strong>
        <span>${formatPercent(payload.champion_favorite.probability)}</span>
      </div>
      ${(payload.nodes?.[0]?.favorites || [])
        .map(
          (item) =>
            `<div class="rank-item"><strong>${teamDisplay(item)}</strong><span>${formatPercent(item.probability)}</span></div>`
        )
        .join('')}
    `
    : '<div class="empty-state">暂无淘汰赛模拟。同步历史比赛后可生成。</div>';
}

function renderMatches() {
  const list = document.querySelector('#matches');
  const meta = state.currentMatchMeta || {};
  const windowDates = meta.window_dates || [...new Set(state.matches.map((match) => match.date))];
  document.querySelector('#match-count').textContent = `${state.matches.length} 场比赛`;
  document.querySelector('#match-window-note').textContent =
    meta.display_mode === 'sporttery_lottery_window'
      ? `北京时间 ${meta.date} 起 · 竞彩销售窗 ${windowDates.join(' / ')} · 当前可售世界杯 ${state.matches.length} 场`
      : `北京时间 ${meta.date || selectedDate()} · 单日赛程`;
  list.innerHTML = state.matches
    .map(
      (match) => `
      <article class="match-row ${state.selectedPrediction?.fixture?.id === match.id ? 'selected' : ''}">
        <div class="match-main">
          <div class="match-topline">
            ${matchDateBadge(match)}
            <span>${match.group || 'World Cup'}</span>
          </div>
          <div class="teams">
            <strong>
              ${teamButton(match.home_team, teamDisplay(match, 'home'))}
              <span class="meta">vs</span>
              ${teamButton(match.away_team, teamDisplay(match, 'away'))}
            </strong>
            <span class="meta">${match.venue || 'venue pending'}</span>
          </div>
          ${inlinePrediction(match)}
        </div>
        ${matchOddsPanel(match)}
        <div class="match-actions">
          <span class="status-pill" data-status="${match.status}">${statusLabel(match.status)}</span>
          <button data-predict="${match.id}" aria-label="查看预测 ${teamDisplay(match, 'home')} vs ${teamDisplay(match, 'away')}">${
            state.matchPredictions[match.id]?.status === 'ready' ? '查看详情' : '生成预测'
          }</button>
        </div>
      </article>
    `
    )
    .join('');
  list.querySelectorAll('[data-predict]').forEach((button) => {
    button.addEventListener('click', () => loadPrediction(button.dataset.predict));
  });
  list.querySelectorAll('[data-team]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      loadTeamDetail(button.dataset.team);
    });
  });
}

function inlinePrediction(match) {
  const entry = state.matchPredictions[match.id];
  const actualScore = match.actual_score || actualScoreText(match);
  const predictedScore = predictionScore(match, entry);
  const accuracy = predictionAccuracy(predictedScore, actualScore, match.prediction_accuracy);
  if (!entry || entry.status === 'loading') {
    return `
      <div class="inline-forecast loading" aria-label="预测概率加载中">
        <span>${forecastLabel(match, 'home')} --</span>
        <span>平局 --</span>
        <span>${forecastLabel(match, 'away')} --</span>
        ${scoreSummary(actualScore, predictedScore, accuracy)}
      </div>
    `;
  }
  if (entry.status === 'error') {
    return `<div class="inline-forecast error">预测暂不可用</div>`;
  }
  const probs = entry.prediction.probabilities;
  const best = Object.entries(probs).sort((left, right) => right[1] - left[1])[0][0];
  const value = bestValueItem(entry.prediction.value_analysis);
  const confidence = entry.prediction.ensemble?.confidence || '低';
  return `
    <div class="inline-forecast" aria-label="胜平负预测概率">
      ${forecastPill(forecastLabel(match, 'home'), probs.home, best === 'home')}
      ${forecastPill('平局', probs.draw, best === 'draw')}
      ${forecastPill(forecastLabel(match, 'away'), probs.away, best === 'away')}
      ${scoreSummary(actualScore, predictedScore, accuracy)}
      <span class="match-value-line">
        <b>价值</b>${value ? `${outcomeLabel(value.outcome)} · ${value.label}` : '盘口未配置'}
        <b>风险</b>${riskLabel(confidence, entry.prediction)}
      </span>
    </div>
  `;
}

function matchOddsPanel(match) {
  const entry = state.matchPredictions[match.id];
  const summaryPrediction =
    match.odds_markets || match.lottery_market
      ? {
          odds_markets: match.odds_markets,
          market: match.market,
          lottery_market: match.lottery_market,
          odds_data_status: match.odds_data_status,
          value_analysis: match.value_analysis,
          handicap_analysis: match.handicap_analysis,
          over25_summary: match.over25_summary,
          over25_prob: match.over25_prob,
          under25_prob: match.under25_prob,
        }
      : null;
  if (!entry || entry.status === 'loading') {
    if (summaryPrediction) {
      return oddsPanelMarkup(summaryPrediction);
    }
    return `
      <aside class="match-odds-panel loading" aria-label="赔率加载中">
        <strong>赔率</strong>
        <span>等待预测生成...</span>
      </aside>
    `;
  }
  if (entry.status === 'error') {
    return `
      <aside class="match-odds-panel muted" aria-label="赔率暂不可用">
        <strong>赔率</strong>
        <span>预测失败，盘口暂不可用</span>
      </aside>
    `;
  }
  return oddsPanelMarkup(entry.prediction);
}

function oddsPanelMarkup(prediction) {
  const h2h = prediction.odds_markets?.h2h || prediction.market || {};
  const handicap = prediction.odds_markets?.handicap || {};
  const lottery = prediction.lottery_market || {};
  const valueItems = prediction.value_analysis?.items || [];
  const handicapItems = prediction.handicap_analysis?.value_analysis?.items || [];
  return `
    <aside class="match-odds-panel" aria-label="赔率与盘口">
      <div class="odds-panel-head">
        <strong>赔率</strong>
        <span>${sourceLabel(lottery.source || prediction.odds_data_status?.source)}</span>
      </div>
      ${lottery.match_no ? `<div class="odds-ticket-line">${lottery.match_no} · ${lottery.league || '世界杯'} · ${lottery.sale_status || 'selling'}</div>` : ''}
      ${oddsMarketRow('胜平负', h2h, null)}
      ${oddsMarketRow('让球胜平负', handicap, handicap.line)}
      ${over25OddsLine(prediction)}
      ${valueItems.length ? `
        <div class="odds-edge-row">
          ${valueItems.map((item) => `<i class="${Math.abs(item.edge) < 0.03 ? 'efficient' : item.edge >= 0.05 ? 'value' : ''}">${outcomeLabel(item.outcome)} ${safePercent(item.edge)} · ${edgeLabel(item)}</i>`).join('')}
        </div>
      ` : '<div class="odds-edge-row muted">Market vs Model：盘口缺失</div>'}
      ${handicapItems.length ? `
        <div class="odds-edge-row handicap-row">
          ${handicapItems.map((item) => `<i class="${item.edge >= 0.05 ? 'value' : Math.abs(item.edge) < 0.03 ? 'efficient' : ''}">让${handicapOutcomeLabel(item.outcome)} ${safePercent(item.edge)} · Kelly ${safePercent(item.kelly?.full)}</i>`).join('')}
        </div>
      ` : ''}
    </aside>
  `;
}

function over25OddsLine(prediction) {
  const over = prediction.over25_summary || {};
  const available = over.value?.available;
  return `
    <div class="odds-market-row ${available ? '' : 'muted'}">
      <span class="odds-market-label">Over2.5</span>
      <span class="odds-cell"><b>大</b>${safePercent(over.final_over25_prob ?? prediction.over25_prob)}</span>
      <span class="odds-cell"><b>小</b>${safePercent(over.under25_prob ?? prediction.under25_prob)}</span>
      <span class="odds-cell"><b>${available ? 'Edge' : '状态'}</b>${available ? safePercent(over.over25_edge) : '无赔率'}</span>
    </div>
  `;
}

function oddsMarketRow(label, market, line) {
  const odds = market?.odds || {};
  const available = Boolean(market?.available);
  return `
    <div class="odds-market-row ${available ? '' : 'muted'}">
      <span class="odds-market-label">${label}${line ? ` <b>${line}</b>` : ''}</span>
      <span class="odds-cell"><b>胜</b>${available ? formatOdd(odds.home) : '--'}</span>
      <span class="odds-cell"><b>平</b>${available ? formatOdd(odds.draw) : '--'}</span>
      <span class="odds-cell"><b>负</b>${available ? formatOdd(odds.away) : '--'}</span>
    </div>
  `;
}

function sourceLabel(source) {
  if (!source) return '盘口来源待同步';
  const raw = String(source);
  const lower = raw.toLowerCase();
  if (raw.includes('China Sporttery') || raw.includes('中国体育彩票') || raw.includes('竞彩') || lower.includes('sporttery')) {
    return '中国体育彩票';
  }
  if (raw.includes('Odds')) return '非中国体育彩票赔率（已忽略）';
  return source;
}

function matchStatusMessage(payload) {
  const dates = payload.window_dates || [];
  if (payload.display_mode === 'sporttery_lottery_window') {
    return `已显示北京时间 ${payload.date} 起的竞彩可售世界杯窗口：${dates.join(' / ')}，共 ${payload.matches.length} 场。`;
  }
  return `已显示北京时间 ${payload.date || selectedDate()} 的 ${payload.matches.length} 场比赛。`;
}

function matchDateBadge(match) {
  return `
    <span class="match-date-badge">
      <b>${formatBeijingDate(match.kickoff)}</b>
      <i>${matchTime(match.kickoff)} BJT</i>
    </span>
  `;
}

function formatBeijingDate(value) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return date.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    timeZone: 'Asia/Shanghai'
  });
}

function teamButton(team, label) {
  return `<button class="team-link" data-team="${escapeAttr(team || '')}" title="查看${escapeAttr(label)}本届世界杯详情">${label}</button>`;
}

function actualScoreText(match) {
  if (match.home_score === null || match.home_score === undefined || match.away_score === null || match.away_score === undefined) {
    return null;
  }
  return `${match.home_score}-${match.away_score}`;
}

function predictionScore(match, entry) {
  if (entry?.prediction?.top_scorelines?.[0]?.score) return entry.prediction.top_scorelines[0].score;
  return match.predicted_score || null;
}

function scoreSummary(actualScore, predictedScore, accuracy) {
  const accuracyText = accuracyLabel(accuracy);
  return `
    <span class="score-summary">
      <b>实际</b>${actualScore || '未赛'}
      <b>预测</b>${predictedScore || '--'}
      <b>准确</b>${accuracyText}
    </span>
  `;
}

function predictionAccuracy(predictedScore, actualScore, serverAccuracy = null) {
  if (serverAccuracy && predictedScore) return serverAccuracy;
  if (!predictedScore || !actualScore || !predictedScore.includes('-') || !actualScore.includes('-')) return null;
  const [ph, pa] = predictedScore.split('-').map(Number);
  const [ah, aa] = actualScore.split('-').map(Number);
  if (![ph, pa, ah, aa].every(Number.isFinite)) return null;
  const predOutcome = ph > pa ? 'home' : ph < pa ? 'away' : 'draw';
  const actualOutcome = ah > aa ? 'home' : ah < aa ? 'away' : 'draw';
  return {
    exact_score: ph === ah && pa === aa,
    outcome_hit: predOutcome === actualOutcome,
    goal_diff_error: Math.abs(ph - pa - (ah - aa)),
    total_goal_error: Math.abs(ph + pa - (ah + aa))
  };
}

function accuracyLabel(accuracy) {
  if (!accuracy) return '待赛后';
  if (accuracy.exact_score) return '比分命中';
  if (accuracy.outcome_hit) return `赛果命中 · 差${accuracy.goal_diff_error}`;
  return `未命中 · 差${accuracy.goal_diff_error}`;
}

function forecastLabel(match, side) {
  const flag = match[`${side}_flag`] || '';
  const name = match[`${side}_team_zh`] || match[`${side}_team`] || '';
  return `${flag ? `${flag} ` : ''}${name}胜`;
}

function forecastPill(label, value, active) {
  return `<span class="forecast-pill ${active ? 'is-best' : ''}"><b>${label}</b>${formatPercent(value)}</span>`;
}

function outcomeLabel(outcome) {
  return { home: '主胜', draw: '平局', away: '客胜' }[outcome] || outcome || '-';
}

function bestValueItem(valueAnalysis) {
  const items = valueAnalysis?.items || [];
  const positive = items.filter((item) => item.label === '有价值').sort((left, right) => right.edge - left.edge);
  return positive[0] || items.sort((left, right) => Math.abs(right.edge || 0) - Math.abs(left.edge || 0))[0] || null;
}

function riskLabel(confidence, prediction) {
  const mc = prediction?.monte_carlo;
  const poisson = prediction?.poisson;
  const mcBest = mc ? bestOutcome({ home: mc.home_win, draw: mc.draw, away: mc.away_win }) : null;
  const poissonBest = poisson ? bestOutcome({ home: poisson.home_win, draw: poisson.draw, away: poisson.away_win }) : null;
  if (mcBest && poissonBest && mcBest !== poissonBest) return '模型分歧';
  if (confidence === '高') return '低';
  if (confidence === '中') return '中';
  return '高';
}

function bestOutcome(probabilities) {
  return Object.entries(probabilities).sort((left, right) => right[1] - left[1])[0]?.[0];
}

function oddsStrip(prediction) {
  const h2h = prediction.odds_markets?.h2h || prediction.market;
  if (!h2h?.available) {
    return '<span class="odds-strip muted"><b>1X2</b><i>盘口不可用</i></span>';
  }
  const odds = h2h.odds || {};
  return `
    <span class="odds-strip">
      <b>1X2</b>
      <i>主 ${formatOdd(odds.home)}</i>
      <i>平 ${formatOdd(odds.draw)}</i>
      <i>客 ${formatOdd(odds.away)}</i>
    </span>
  `;
}

function edgeStrip(prediction) {
  const items = prediction.value_analysis?.items || [];
  if (!items.length) return '<span class="edge-strip muted">Market vs Model：盘口缺失</span>';
  return `
    <span class="edge-strip">
      ${items
        .map((item) => `<i class="${Math.abs(item.edge) < 0.03 ? 'efficient' : item.edge >= 0.05 ? 'value' : ''}">${outcomeLabel(item.outcome)} ${safePercent(item.edge)} · ${edgeLabel(item)}</i>`)
        .join('')}
    </span>
  `;
}

function edgeLabel(item) {
  if (item.edge_label) return item.edge_label;
  if (Number(item.edge || 0) >= 0.05) return 'value bet';
  if (Math.abs(Number(item.edge || 0)) < 0.03) return 'market efficient';
  return 'watch';
}

function formatOdd(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : '--';
}

function handicapOutcomeLabel(outcome) {
  return { home: '胜', draw: '平', away: '负' }[outcome] || outcome || '-';
}

function formatHandicapLine(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return value ?? '--';
  return number > 0 ? `主队 +${number}` : `主队 ${number}`;
}

function renderRounds() {
  const total = state.rounds.reduce((sum, item) => sum + Number(item.match_count || 0), 0);
  document.querySelector('#round-count').textContent = state.rounds.length ? `${state.rounds.length} 个轮次 · ${total} 场` : '等待同步';
  const tabs = [
    {
      stage: 'all',
      label: '全部比赛',
      helper: '完整赛程',
      match_count: total,
      finished_count: state.rounds.reduce((sum, item) => sum + Number(item.finished_count || 0), 0)
    },
    ...state.rounds
  ];
  document.querySelector('#round-tabs').innerHTML = tabs
    .map(
      (round) => `
        <button class="round-switch-card ${state.selectedStage === round.stage ? 'active' : ''}" data-stage="${escapeAttr(round.stage)}" aria-pressed="${state.selectedStage === round.stage}">
          <span>${round.label || round.stage}</span>
          <strong>${round.finished_count || 0}/${round.match_count || 0}</strong>
          <small>${round.helper || stageHint(round)}</small>
        </button>
      `
    )
    .join('');
  document.querySelectorAll('#round-tabs [data-stage]').forEach((button) => {
    button.addEventListener('click', () => {
      loadRoundMatches(button.dataset.stage);
      showView('rounds');
    });
  });
}

function renderRoundMatches() {
  const target = document.querySelector('#round-matches');
  const title = document.querySelector('#selected-round-title');
  const selectedRound = selectedRoundSummary();
  if (title) {
    title.textContent = `${selectedRound.label} · ${selectedRound.finished}/${selectedRound.total} 已完赛`;
  }
  if (!state.roundMatches.length) {
    target.innerHTML = '<div class="empty-state">暂无轮次数据。点击“刷新参考数据”。</div>';
    return;
  }
  target.innerHTML = state.roundMatches
    .map(
      (match) => `
        <article class="round-card" data-round-fixture="${escapeAttr(match.id)}" role="button" tabindex="0" aria-label="查看预测 ${teamDisplay(match, 'home')} vs ${teamDisplay(match, 'away')}">
          <div class="round-card-top">
            <span>${match.stage || match.group || 'World Cup'}</span>
            <span class="status-pill" data-status="${match.status}">${statusLabel(match.status)}</span>
          </div>
          <div class="round-card-score">
            ${teamButton(match.home_team, teamDisplay(match, 'home'))}
            <strong>${actualScoreText(match) || matchTime(match.kickoff)}</strong>
            ${teamButton(match.away_team, teamDisplay(match, 'away'))}
          </div>
          ${roundResultLine(match)}
          <div class="round-card-meta">${match.date} · ${match.venue || 'venue pending'}</div>
          <div class="round-card-meta">预测 ${match.predicted_score || '--'} · ${accuracyLabel(match.prediction_accuracy)}</div>
          ${roundOddsLine(match)}
        </article>
      `
    )
    .join('');
  target.querySelectorAll('[data-team]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      loadTeamDetail(button.dataset.team);
    });
  });
  target.querySelectorAll('[data-round-fixture]').forEach((card) => {
    card.addEventListener('click', () => loadPrediction(card.dataset.roundFixture));
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        loadPrediction(card.dataset.roundFixture);
      }
    });
  });
}

function roundResultLine(match) {
  const result = match.finished_result;
  if (!result) return '';
  const extra = result.home_goals_extra_time !== null && result.home_goals_extra_time !== undefined
    ? ` · 加时 ${result.home_goals_extra_time}-${result.away_goals_extra_time}`
    : '';
  const penalties = result.home_penalties !== null && result.home_penalties !== undefined
    ? ` · 点球 ${result.home_penalties}-${result.away_penalties}`
    : '';
  const advance = result.winner ? ` · 晋级 ${result.winner}` : '';
  const loser = result.loser ? ` · 淘汰 ${result.loser}` : '';
  const source = result.source ? ` · ${result.source}` : '';
  const updated = result.fetched_at || result.synced_at;
  return `
    <div class="round-card-result">
      90分钟 ${result.home_goals_90 ?? match.home_score}-${result.away_goals_90 ?? match.away_score}${extra}${penalties}${advance}${loser}${source}${updated ? ` · ${formatDateTime(updated)}` : ''}
    </div>
  `;
}

function roundOddsLine(match) {
  const lottery = match.lottery_market || {};
  const h2h = match.odds_markets?.h2h || {};
  const handicap = match.odds_markets?.handicap || {};
  const over25 = match.over25_summary || {};
  if (!h2h.available && !handicap.available) {
    const label = match.historical_without_odds ? '无赔率历史复盘' : '等待同步';
    return `<div class="round-card-meta muted">赔率：${label}${over25.final_over25_prob !== undefined ? ` · Over2.5 ${safePercent(over25.final_over25_prob)}` : ''}</div>`;
  }
  return `
    <div class="round-card-odds">
      ${lottery.match_no ? `<span>${lottery.match_no}</span>` : ''}
      ${h2h.available ? `<span>胜 ${formatOdd(h2h.odds?.home)} 平 ${formatOdd(h2h.odds?.draw)} 负 ${formatOdd(h2h.odds?.away)}</span>` : ''}
      ${handicap.available ? `<span>让${handicap.line ?? ''} 胜 ${formatOdd(handicap.odds?.home)} 平 ${formatOdd(handicap.odds?.draw)} 负 ${formatOdd(handicap.odds?.away)}</span>` : ''}
      ${over25.final_over25_prob !== undefined ? `<span>Over2.5 ${safePercent(over25.final_over25_prob)}</span>` : ''}
    </div>
  `;
}

function defaultRoundStage(rounds) {
  const activeRound = rounds.find((round) => Number(round.finished_count || 0) < Number(round.match_count || 0));
  return activeRound?.stage || rounds.at(-1)?.stage || 'all';
}

function stageHint(round) {
  const finished = Number(round.finished_count || 0);
  const total = Number(round.match_count || 0);
  if (total && finished >= total) return '已完成';
  if (finished > 0) return '进行中';
  return '未开始';
}

function selectedRoundSummary() {
  const total = state.rounds.reduce((sum, item) => sum + Number(item.match_count || 0), 0);
  const finished = state.rounds.reduce((sum, item) => sum + Number(item.finished_count || 0), 0);
  if (state.selectedStage === 'all') {
    return { label: '全部比赛', total, finished };
  }
  const round = state.rounds.find((item) => item.stage === state.selectedStage) || {};
  return {
    label: round.stage || '轮次',
    total: Number(round.match_count || state.roundMatches.length || 0),
    finished: Number(round.finished_count || 0)
  };
}

function matchTime(value) {
  if (!value) return '--:--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' });
}

async function loadTeamDetail(team) {
  setStatus(`正在加载 ${team} 的本届世界杯详情...`);
  try {
    const detail = await api(`/api/teams/${encodeURIComponent(team)}/world-cup-detail`);
    state.selectedTeamDetail = detail;
    renderTeamDetail(detail);
    showView('team-detail');
    setStatus(`${detail.display.flag || ''}${detail.display.zh || detail.team}：${detail.coverage.matches} 场比赛，${detail.coverage.players} 名球员。`, 'success');
  } catch (error) {
    setStatus(`加载球队详情失败：${error.message}`, 'error');
  }
}

function renderTeamDetail(detail) {
  document.querySelector('#selected-team').textContent = `${detail.display.flag || ''} ${detail.display.zh || detail.team}`;
  const matchRows = detail.matches
    .map(
      (match) => `
        <div class="team-match-row">
          <strong>${match.stage || '-'} · ${match.date}</strong>
          <span>${teamDisplay(match, 'home')} ${actualScoreText(match) || matchTime(match.kickoff)} ${teamDisplay(match, 'away')}</span>
        </div>
      `
    )
    .join('');
  const playerRows = detail.players
    .map(
      (player) => {
        const abilityLabel = player.ability == null ? '-' : `${player.ability}${player.ability_estimated ? '（估算）' : ''}`;
        return `
        <div class="player-row">
          <strong>${player.shirt_number || '-'} · ${player.player_name}${player.club ? `（${player.club}）` : ''}</strong>
          <span>${positionLabel(player.position)} · 能力 ${abilityLabel} · ${player.league || '俱乐部待确认'}</span>
        </div>
      `;
      }
    )
    .join('');
  const powerRows = (detail.power_rankings || [])
    .slice(0, 16)
    .map((player) => `
      <div class="player-row">
        <strong>${player.player_name}</strong>
        <span>${positionLabel(player.position)} · FIFA power ${formatNumber(player.rating)} · ${sourceLabel(player.source)}</span>
      </div>
    `)
    .join('');
  document.querySelector('#team-detail-view').innerHTML = `
    <div class="team-detail-grid">
      <section>
        <h3>本届对阵</h3>
        <div class="team-match-list">${matchRows || '<div class="empty-state">暂无比赛数据</div>'}</div>
      </section>
      <section>
        <h3>阵容与能力值</h3>
        ${strengthCard(teamDisplay({ team: detail.team, team_zh: detail.display?.zh, flag: detail.display?.flag }), detail.squad_strength || {})}
        <div class="coach-line">球员 ${detail.coverage.players} · 能力值 ${detail.coverage.players_with_ability} · FIFA power ${detail.coverage.players_with_fifa_power || 0} · 源能力 ${detail.coverage.players_with_source_ability ?? detail.coverage.players_with_ability} · 估算 ${detail.coverage.players_with_estimated_ability || 0} · 已知俱乐部 ${detail.coverage.players_with_club}</div>
        ${powerRows ? `<div class="player-table power-ranking-table">${powerRows}</div>` : ''}
        <div class="player-table">${playerRows || '<div class="empty-state">暂无球员数据</div>'}</div>
      </section>
    </div>
  `;
}

function positionLabel(value) {
  const labels = { goalkeeper: '门将', defender: '后卫', midfielder: '中场', attacker: '前锋' };
  return labels[String(value || '').toLowerCase()] || value || '-';
}

function escapeAttr(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

function statusLabel(status) {
  if (status === 'final') return '已完赛';
  if (status === 'live') return '进行中';
  return '未开赛';
}

function predictionSectionAttrs(id, defaultCollapsed = false) {
  const collapsed = state.collapsedPredictionSections[id] ?? defaultCollapsed;
  return `class="prediction-section collapsible-section ${collapsed ? 'is-collapsed' : ''}" data-section-id="${escapeAttr(id)}"`;
}

function predictionSectionToggle(id, defaultCollapsed = false) {
  const collapsed = state.collapsedPredictionSections[id] ?? defaultCollapsed;
  return `<button type="button" class="section-collapse-button" data-section-toggle="${escapeAttr(id)}" aria-expanded="${collapsed ? 'false' : 'true'}">${collapsed ? '展开' : '折叠'}</button>`;
}

function renderPrediction(prediction, analysis = null) {
  const fixture = prediction.fixture;
  const h2h = analysis?.head_to_head || {};
  const sections = analysis?.analysis_sections || [];
  const profiles = analysis?.team_profiles || {};
  state.rosterWeight = Number(prediction.roster_weight ?? state.rosterWeight);
  state.simulations = Number(prediction.simulation_request?.used ?? prediction.monte_carlo?.simulations ?? state.simulations);
  state.lastPredictionParams = currentPredictionParams(fixture.id);
  state.predictionParamsDirty = false;
  state.lastCalculationTime = new Date().toISOString();
  document.querySelector('#selected-fixture').textContent = `${teamDisplay(fixture, 'home')} vs ${teamDisplay(fixture, 'away')}`;
  const probabilities = prediction.probabilities;
  document.querySelector('#prediction-detail').innerHTML = `
    <div class="prediction-body">
      <section class="prediction-section">
        <div class="section-title">
          <h3>融合结论</h3>
          <span>${prediction.ensemble?.recommended_result || outcomeLabel(bestOutcome(probabilities))} · 信心 ${prediction.ensemble?.confidence || '低'}</span>
        </div>
        <div class="prob-bars">
          ${bar('主胜', probabilities.home, 'home-fill')}
          ${bar('平局', probabilities.draw, 'draw-fill')}
          ${bar('客胜', probabilities.away, 'away-fill')}
        </div>
        <div class="metric-grid compact-metrics">
          <div class="metric-card"><strong>λ 主队</strong><span>${Number(prediction.expected_goals.home).toFixed(2)}</span></div>
          <div class="metric-card"><strong>λ 客队</strong><span>${Number(prediction.expected_goals.away).toFixed(2)}</span></div>
          <div class="metric-card"><strong>BTTS</strong><span>${formatPercent(prediction.btts)}</span></div>
          <div class="metric-card"><strong>Over 2.5</strong><span>${formatPercent(prediction.totals['2.5'].over)}</span></div>
        </div>
        ${divergenceNotice(prediction)}
      </section>

      <div class="detail-section-toolbar">
        <button type="button" data-expand-sections>展开全部卡片</button>
        <button type="button" data-collapse-sections>折叠长卡片</button>
      </div>

      <section ${predictionSectionAttrs('models')}>
        <div class="section-title">
          <h3>模型对照</h3>
          <div class="section-title-actions"><span>统计模型、模拟和盘口分层展示</span>${predictionSectionToggle('models')}</div>
        </div>
        <div class="model-grid">${modelComparisonCards(prediction)}</div>
        ${modelWeightAudit(prediction)}
      </section>

      <section ${predictionSectionAttrs('value')}>
        <div class="section-title">
          <h3>投注价值分析</h3>
          <div class="section-title-actions"><span>仅供复盘，不构成投注建议</span>${predictionSectionToggle('value')}</div>
        </div>
        ${valueAnalysisCard(prediction)}
      </section>

      <section ${predictionSectionAttrs('over25')}>
        <div class="section-title">
          <h3>Over2.5 / Under2.5</h3>
          <div class="section-title-actions"><span>90分钟总进球，仅模型概率或中国体育彩票盘口校准</span>${predictionSectionToggle('over25')}</div>
        </div>
        ${over25AnalysisCard(prediction)}
      </section>

      <div class="control-card">
        <label for="roster-weight">阵容权重 <strong id="roster-weight-value">${Math.round(state.rosterWeight * 100)}%</strong></label>
        <input id="roster-weight" type="range" min="0" max="50" value="${Math.round(state.rosterWeight * 100)}" />
        <span>顶级锋线对薄弱后防会提高 xG；强防线会抵消对手进攻端优势。</span>
      </div>

      <section ${predictionSectionAttrs('market')}>
        <div class="section-title">
          <h3>盘口与模型偏差</h3>
          <div class="section-title-actions"><span>value bet ≥5%，market efficient &lt;3%</span>${predictionSectionToggle('market')}</div>
        </div>
        ${marketVsModelCard(prediction)}
      </section>

      <section ${predictionSectionAttrs('roster-strength')}>
        <div class="section-title">
          <h3>阵容三线强度</h3>
          <div class="button-row">
            <button data-sync-squads="${fixture.id}">同步本场阵容</button>
            <button data-process-roster>补全球员统计</button>
            ${predictionSectionToggle('roster-strength')}
          </div>
        </div>
        <div class="profile-grid">
          ${strengthCard(teamDisplay(fixture, 'home'), prediction.roster_strength?.home)}
          ${strengthCard(teamDisplay(fixture, 'away'), prediction.roster_strength?.away)}
        </div>
      </section>

      <section ${predictionSectionAttrs('score')}>
        <div class="section-title">
          <h3>比分概率</h3>
          <div class="section-title-actions"><span>${teamDisplay(fixture, 'home')} vs ${teamDisplay(fixture, 'away')} · Top 6 与 0-7 热力图</span>${predictionSectionToggle('score')}</div>
        </div>
        <div class="score-match-title">${teamDisplay(fixture, 'home')} vs ${teamDisplay(fixture, 'away')}</div>
        <div class="score-grid">
          ${prediction.top_scorelines
            .map((item) => `<div class="score-item"><strong>${item.score}</strong><span>${formatPercent(item.probability)}</span></div>`)
            .join('')}
        </div>
        ${scoreHeatmap(prediction.score_heatmap || { matrix: prediction.score_matrix }, prediction.handicap_analysis, {
          homeLabel: teamDisplay(fixture, 'home'),
          awayLabel: teamDisplay(fixture, 'away'),
        })}
      </section>

      <section ${predictionSectionAttrs('monte-carlo', true)}>
        <div class="section-title">
          <h3>Monte Carlo 脚本</h3>
          <div class="section-title-actions"><span>${prediction.monte_carlo?.simulations?.toLocaleString('zh-CN') || '-'} 次模拟</span>${predictionSectionToggle('monte-carlo', true)}</div>
        </div>
        ${simulationControlCard()}
        ${monteCarloCard(prediction)}
      </section>

      <section ${predictionSectionAttrs('profiles', true)}>
        <div class="section-title">
          <h3>球队画像与交锋</h3>
          <div class="section-title-actions"><span>近期权重与历史样本</span>${predictionSectionToggle('profiles', true)}</div>
        </div>
        <div class="profile-grid">
          ${profileCard(fixture.home_team, profiles.home)}
          ${profileCard(fixture.away_team, profiles.away)}
        </div>
        <div class="analysis">
          可用样本 ${h2h.matches ?? 0} 场，加权样本 ${(h2h.weighted_matches ?? 0).toFixed(2)}；
          ${teamDisplay(fixture, 'home')} 视角 ${h2h.wins ?? 0} 胜 ${h2h.draws ?? 0} 平 ${h2h.losses ?? 0} 负。
        </div>
      </section>

      ${sections.length ? `<section ${predictionSectionAttrs('ai-analysis', true)}><div class="section-title"><h3>AI 解读草稿</h3><div class="section-title-actions"><span>结构化中文分析</span>${predictionSectionToggle('ai-analysis', true)}</div></div>${sections.map((section) => `<div class="analysis"><strong>${section.title}</strong><br>${section.text}</div>`).join('')}</section>` : ''}

      <section ${predictionSectionAttrs('squads', true)}>
        <div class="section-title">
          <h3>阵容与教练</h3>
          <div class="section-title-actions"><span>API-Football / 公开源 fallback</span>${predictionSectionToggle('squads', true)}</div>
        </div>
        <div id="squad-panels" class="squad-grid">
          <div class="empty-state">正在加载阵容...</div>
        </div>
      </section>
      <div class="analysis">${prediction.chinese_report}</div>
    </div>
  `;
  const slider = document.querySelector('#roster-weight');
  slider?.addEventListener('input', () => {
    state.rosterWeight = Number(slider.value) / 100;
    document.querySelector('#roster-weight-value').textContent = `${slider.value}%`;
    markPredictionParamsDirty(fixture.id);
  });
  const simulationSelect = document.querySelector('#simulation-count');
  simulationSelect?.addEventListener('change', () => {
    state.simulations = Number(simulationSelect.value);
    document.querySelector('#simulation-count-value').textContent = state.simulations.toLocaleString('zh-CN');
    const custom = document.querySelector('#simulation-custom');
    if (custom) custom.value = state.simulations;
    markPredictionParamsDirty(fixture.id);
  });
  const simulationCustom = document.querySelector('#simulation-custom');
  simulationCustom?.addEventListener('change', () => {
    const value = Math.max(1000, Math.min(100000, Number(simulationCustom.value || 10000)));
    state.simulations = value;
    simulationCustom.value = value;
    document.querySelector('#simulation-count-value').textContent = value.toLocaleString('zh-CN');
    markPredictionParamsDirty(fixture.id);
  });
  document.querySelector('#calculate-prediction-button')?.addEventListener('click', () => recalculatePrediction(fixture.id));
  document.querySelector('[data-sync-squads]')?.addEventListener('click', () => syncFixtureSquads(fixture));
  document.querySelector('[data-process-roster]')?.addEventListener('click', () => processRosterQueue());
}

function simulationControlCard() {
  return `
    <div class="control-card compact-control">
      <label for="simulation-count">Monte Carlo 模拟次数 <strong id="simulation-count-value">${state.simulations.toLocaleString('zh-CN')}</strong></label>
      <div class="calculation-controls">
        <select id="simulation-count">
          ${[5000, 10000, 20000, 50000]
            .map((value) => `<option value="${value}" ${state.simulations === value ? 'selected' : ''}>${value.toLocaleString('zh-CN')}</option>`)
            .join('')}
        </select>
        <input id="simulation-custom" type="number" min="1000" max="100000" step="1000" value="${state.simulations}" aria-label="自定义模拟次数" />
        <button id="calculate-prediction-button" type="button" disabled>计算</button>
      </div>
      <span id="calculation-status">最后计算 ${formatDateTime(state.lastCalculationTime)} · 使用 ${state.simulations.toLocaleString('zh-CN')} 次模拟</span>
    </div>
  `;
}

function currentPredictionParams(fixtureId) {
  return {
    fixtureId,
    rosterWeight: Number(state.rosterWeight.toFixed(4)),
    simulations: Number(state.simulations),
  };
}

function markPredictionParamsDirty(fixtureId) {
  const button = document.querySelector('#calculate-prediction-button');
  const status = document.querySelector('#calculation-status');
  const current = currentPredictionParams(fixtureId);
  state.predictionParamsDirty = JSON.stringify(current) !== JSON.stringify(state.lastPredictionParams);
  if (button) {
    button.disabled = !state.predictionParamsDirty;
    button.textContent = state.predictionParamsDirty ? '计算' : '已计算';
  }
  if (status) {
    status.textContent = state.predictionParamsDirty
      ? '参数已修改，点击计算刷新概率。'
      : `最后计算 ${formatDateTime(state.lastCalculationTime)} · 使用 ${state.simulations.toLocaleString('zh-CN')} 次模拟`;
  }
}

async function recalculatePrediction(fixtureId) {
  const button = document.querySelector('#calculate-prediction-button');
  const status = document.querySelector('#calculation-status');
  if (!state.predictionParamsDirty || !button) return;
  button.disabled = true;
  button.textContent = '计算中...';
  if (status) status.textContent = '正在重新计算当前参数...';
  const result = await loadPrediction(fixtureId);
  if (!result) {
    button.disabled = false;
    button.textContent = '计算';
    if (status) status.textContent = '计算失败，原有结果已保留。';
  }
}

function modelComparisonCards(prediction) {
  const market = prediction.market?.available ? prediction.market.implied_probability_no_vig : null;
  const cards = [
    modelCard('Elo/DC', prediction.elo, prediction.model_blend_weights?.dixon_coles_elo, '基线强弱'),
    modelCard('Poisson', prediction.poisson, prediction.model_blend_weights?.poisson, 'λ 与比分矩阵'),
    modelCard('Monte Carlo', prediction.monte_carlo, prediction.model_blend_weights?.monte_carlo, `${prediction.monte_carlo?.simulations?.toLocaleString('zh-CN') || '10,000'} 场景模拟`),
    modelCard('Market', market, prediction.model_blend_weights?.market, prediction.market?.available ? '去水盘口' : '暂无盘口'),
    modelCard('XGBoost', prediction.xgboost, prediction.model_blend_weights?.xgboost, 'AI model layer'),
    modelCard('Ensemble', prediction.ensemble, 1, '最终输出', true)
  ];
  return cards.join('');
}

function modelWeightAudit(prediction) {
  const run = prediction.model_weight_run || {};
  const weights = run.weights || {};
  const cells = [
    ['Elo/DC', weights.elo],
    ['Poisson', weights.poisson],
    ['Monte Carlo', weights.monte_carlo],
    ['盘口', weights.market],
    ['XGBoost', weights.xgboost]
  ]
    .filter(([, value]) => value !== undefined)
    .map(([label, value]) => `<div class="metric-card"><strong>${label}</strong><span>${safePercent(value)}</span></div>`)
    .join('');
  return `
    <div class="analysis weight-audit">
      <strong>融合权重校准</strong>
      <span>${run.sample_count || 0} 场已完赛样本 · ${run.reason || '默认权重'}</span>
      <div class="metric-grid compact-metrics">${cells}</div>
    </div>
  `;
}

function modelCard(title, source, weight, helper, prominent = false) {
  const probs = normalizeModelProbabilities(source);
  return `
    <article class="model-card ${prominent ? 'prominent' : ''}">
      <div class="model-card-head">
        <strong>${title}</strong>
        <span>权重 ${safePercent(weight ?? 0)}</span>
      </div>
      <div class="mini-probs">
        ${miniProbability('主胜', probs.home, 'home-fill')}
        ${miniProbability('平', probs.draw, 'draw-fill')}
        ${miniProbability('客胜', probs.away, 'away-fill')}
      </div>
      <small>${helper}</small>
      ${title === 'XGBoost' ? `<em class="model-badge">${source?.engine || 'AI model layer'}</em>` : ''}
    </article>
  `;
}

function normalizeModelProbabilities(source = {}) {
  if (!source) {
    return { home: 0, draw: 0, away: 0 };
  }
  return {
    home: Number(source.home ?? source.home_win ?? 0),
    draw: Number(source.draw ?? 0),
    away: Number(source.away ?? source.away_win ?? 0)
  };
}

function miniProbability(label, value, className) {
  return `
    <div class="mini-prob">
      <span>${label}</span>
      <div class="bar-track"><div class="bar-fill ${className}" style="width:${Math.max(0, Math.min(100, Number(value || 0) * 100))}%"></div></div>
      <b>${safePercent(value)}</b>
    </div>
  `;
}

function valueAnalysisCard(prediction) {
  const value = prediction.value_analysis;
  if (!value?.available) {
    return `
      <div class="value-card muted">
        <strong>盘口未配置，当前仅基于模型评估</strong>
        <span>${value?.risk_warning || '本系统只做数据分析，不构成投注建议。'}</span>
      </div>
    `;
  }
  return `
    <div class="value-card">
      <div class="value-summary">
        <strong>投注价值分析</strong>
        <span>${value.summary} · 信心 ${value.confidence || '低'}</span>
      </div>
      <div class="value-grid">
        ${value.items
          .map(
            (item) => `
              <div class="value-item ${item.label === '有价值' ? 'positive' : item.label === '不建议' ? 'negative' : ''}">
                <strong>${outcomeLabel(item.outcome)}</strong>
                <span>${item.label}</span>
                <small>模型 ${safePercent(item.model_probability)} · 市场 ${safePercent(item.market_probability)} · Edge ${safePercent(item.edge)}</small>
                <small>Kelly ${safePercent(item.kelly?.full)} / 半Kelly ${safePercent(item.kelly?.half)} / 1/4 Kelly ${safePercent(item.kelly?.quarter)}</small>
                <small>${riskBar(item)} ${item.recommendation}</small>
              </div>
            `
          )
          .join('')}
      </div>
      <p>${value.risk_warning}</p>
    </div>
  `;
}

function over25AnalysisCard(prediction) {
  const over = prediction.over25_summary || {};
  const value = over.value || {};
  return `
    <div class="value-card ${value.available ? '' : 'muted'}">
      <div class="value-summary">
        <strong>融合 Over2.5 ${safePercent(over.final_over25_prob ?? prediction.final_over25_prob ?? prediction.over25_prob)}</strong>
        <span>Under2.5 ${safePercent(over.under25_prob ?? prediction.under25_prob)} · ${value.available ? '含市场赔率' : '无赔率，仅模型概率'}</span>
      </div>
      <div class="metric-grid compact-metrics">
        <div class="metric-card"><strong>Poisson</strong><span>${safePercent(over.poisson_over25_prob ?? prediction.totals?.['2.5']?.over)}</span></div>
        <div class="metric-card"><strong>XGBoost</strong><span>${safePercent(over.xgboost_over25_prob ?? prediction.xgboost_over25_prob)}</span></div>
        <div class="metric-card"><strong>Monte Carlo</strong><span>${safePercent(over.monte_carlo_over25_prob ?? prediction.monte_carlo_over25_prob)}</span></div>
        <div class="metric-card"><strong>热力图</strong><span>${safePercent(over.score_heatmap_over25_prob ?? prediction.score_heatmap_over25_prob)}</span></div>
      </div>
      ${value.available ? `
        <div class="value-grid">
          <div class="value-item ${Number(over.over25_edge || 0) >= 0.05 ? 'positive' : ''}">
            <strong>Over2.5</strong>
            <span>市场 ${safePercent(over.over25_market_prob)} · Edge ${safePercent(over.over25_edge)}</span>
            <small>Kelly ${safePercent(over.over25_kelly?.full)} / 半Kelly ${safePercent(over.over25_kelly?.half)} / 1/4 Kelly ${safePercent(over.over25_kelly?.quarter)}</small>
            <small>仅数据分析，不构成投注建议。</small>
          </div>
        </div>
      ` : `<p>${value.reason || '无赔率，仅模型概率。'}</p>`}
    </div>
  `;
}

function marketVsModelCard(prediction) {
  const h2h = prediction.odds_markets?.h2h || prediction.market;
  const handicap = prediction.handicap_analysis || {};
  if (!h2h?.available) {
    return `
      <div class="market-panel muted">
        <div class="odds-strip muted"><b>1X2</b><i>盘口不可用</i></div>
        ${handicapComparisonCard(handicap)}
      </div>
    `;
  }
  const odds = h2h.odds || {};
  return `
    <div class="market-panel">
      <div class="odds-strip">
        <b>1X2赔率</b>
        <i>主 ${formatOdd(odds.home)}</i>
        <i>平 ${formatOdd(odds.draw)}</i>
        <i>客 ${formatOdd(odds.away)}</i>
      </div>
      <div class="model-market-bars">
        ${(prediction.value_analysis?.items || [])
          .map(
            (item) => `
              <div class="model-market-row">
                <span>${outcomeLabel(item.outcome)}</span>
                <div class="bar-track"><div class="bar-fill home-fill" style="width:${Math.max(0, Math.min(100, item.model_probability * 100))}%"></div></div>
                <div class="bar-track"><div class="bar-fill draw-fill" style="width:${Math.max(0, Math.min(100, item.market_probability * 100))}%"></div></div>
                <strong class="${item.edge >= 0.05 ? 'value' : Math.abs(item.edge) < 0.03 ? 'efficient' : ''}">${safePercent(item.edge)}</strong>
              </div>
            `
          )
          .join('')}
      </div>
      ${handicapComparisonCard(handicap)}
    </div>
  `;
}

function handicapComparisonCard(handicap) {
  if (!handicap?.available) {
    return `<div class="odds-strip muted"><b>让球</b><i>${handicap?.reason || '盘口不可用'}</i></div>`;
  }
  const items = handicap.value_analysis?.items || [];
  return `
    <div class="handicap-market-card">
      <div class="odds-strip">
        <b>让球胜平负</b>
        <i>盘口 ${formatHandicapLine(handicap.line)}</i>
        <i>${handicap.tail_note || 'tail 已计入概率'}</i>
      </div>
      <div class="model-market-bars">
        ${items
          .map(
            (item) => `
              <div class="model-market-row">
                <span>让${handicapOutcomeLabel(item.outcome)}</span>
                <div class="bar-track"><div class="bar-fill home-fill" style="width:${Math.max(0, Math.min(100, item.model_probability * 100))}%"></div></div>
                <div class="bar-track"><div class="bar-fill draw-fill" style="width:${Math.max(0, Math.min(100, item.market_probability * 100))}%"></div></div>
                <strong class="${item.edge >= 0.05 ? 'value' : Math.abs(item.edge) < 0.03 ? 'efficient' : ''}">${safePercent(item.edge)}</strong>
                <small>Kelly ${safePercent(item.kelly?.full)} · ${item.label}</small>
              </div>
            `
          )
          .join('')}
      </div>
    </div>
  `;
}

function riskBar(item) {
  const kelly = Number(item.kelly?.full || 0);
  if (kelly >= 0.08) return '<span class="kelly-risk high">high</span>';
  if (kelly >= 0.03) return '<span class="kelly-risk medium">medium</span>';
  return '<span class="kelly-risk low">low</span>';
}

function monteCarloCard(prediction) {
  const mc = prediction.monte_carlo || {};
  const ci = mc.confidence_interval || {};
  return `
    <div class="model-card prominent">
      <div class="model-card-head">
        <strong>${mc.typical_script || '均衡拉锯'}</strong>
        <span>均值 ${Number(mc.average_goals?.home ?? 0).toFixed(2)}-${Number(mc.average_goals?.away ?? 0).toFixed(2)}</span>
      </div>
      <div class="metric-grid compact-metrics">
        <div class="metric-card"><strong>主胜区间</strong><span>${intervalLabel(ci.home_win)}</span></div>
        <div class="metric-card"><strong>平局区间</strong><span>${intervalLabel(ci.draw)}</span></div>
        <div class="metric-card"><strong>客胜区间</strong><span>${intervalLabel(ci.away_win)}</span></div>
        <div class="metric-card"><strong>Over 2.5</strong><span>${safePercent(mc.over_2_5)}</span></div>
      </div>
      <div class="score-grid">
        ${(mc.scorelines || [])
          .slice(0, 6)
          .map((item) => `<div class="score-item"><strong>${item.score}</strong><span>${safePercent(item.probability)}</span></div>`)
          .join('')}
      </div>
    </div>
  `;
}

function divergenceNotice(prediction) {
  const risk = riskLabel(prediction.ensemble?.confidence, prediction);
  if (risk !== '模型分歧') return '';
  return '<div class="analysis warning">Poisson 与 Monte Carlo 的首选赛果不一致，本场应降低结论置信度并重点关注临场阵容和赔率变化。</div>';
}

function intervalLabel(interval) {
  if (!interval) return '-';
  return `${safePercent(interval.low)}-${safePercent(interval.high)}`;
}

function safePercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  return `${(number * 100).toFixed(1)}%`;
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  return number.toFixed(3);
}

function renderSimulation(prediction) {
  const weights = prediction.model_blend_weights;
  document.querySelector('#simulation-summary').innerHTML = `
    <div class="metric-card"><strong>Dixon-Coles + ELO</strong><span>${safePercent(weights.dixon_coles_elo)}</span></div>
    <div class="metric-card"><strong>Poisson</strong><span>${safePercent(weights.poisson)}</span></div>
    <div class="metric-card"><strong>Monte Carlo</strong><span>${safePercent(weights.monte_carlo)}</span></div>
    <div class="metric-card"><strong>盘口</strong><span>${safePercent(weights.market)}</span></div>
    <div class="metric-card"><strong>XGBoost</strong><span>${safePercent(weights.xgboost)}</span></div>
    <div class="metric-card"><strong>市场校准</strong><span>${safePercent(weights.market_calibration)}</span></div>
    <div class="metric-card"><strong>LLM 投票上限</strong><span>${formatPercent(weights.llm_vote_cap)}</span></div>
    <div class="metric-card"><strong>当前 LLM 权重</strong><span>${safePercent(weights.llm_vote)}</span></div>
    <div class="metric-card"><strong>Poisson λ</strong><span>${prediction.poisson?.lambda_home ?? '-'} / ${prediction.poisson?.lambda_away ?? '-'}</span></div>
    <div class="metric-card"><strong>模拟脚本</strong><span>${prediction.monte_carlo?.typical_script ?? '-'}</span></div>
  `;
}

function bar(label, value, className) {
  return `
    <div class="bar-row">
      <span>${label}</span>
      <div class="bar-track"><div class="bar-fill ${className}" style="width: ${value * 100}%"></div></div>
      <strong>${formatPercent(value)}</strong>
    </div>
  `;
}

function scoreHeatmap(heatmap, handicapAnalysis = {}, teams = {}) {
  const matrix = Array.isArray(heatmap) ? heatmap : heatmap?.matrix || [];
  const homeLabel = teams.homeLabel || '主队';
  const awayLabel = teams.awayLabel || '客队';
  const handicapRegions = heatmap?.handicap_regions || {};
  const cells = new Map(matrix.map((item) => [`${item.home_goals}-${item.away_goals}`, item.probability]));
  const goals = scoreHeatmapGoals(matrix);
  const visibleProbabilities = goals.flatMap((home) => goals.map((away) => cells.get(`${home}-${away}`) || 0));
  const maxProbability = Math.max(...visibleProbabilities, 0.01);
  const handicapLine = heatmap?.handicap ?? handicapAnalysis?.line;
  const handicapTotals =
    heatmap?.handicap_probabilities ||
    handicapAnalysis?.model_probabilities ||
    handicapAnalysis?.probabilities ||
    {};
  const handicapMarketTotals =
    heatmap?.handicap_market_probabilities ||
    handicapAnalysis?.market_probabilities ||
    {};
  return `
    <div class="heatmap-wrap" aria-label="比分概率热力图，颜色越暖代表概率越高">
      <div class="heatmap-axis-title">
        <strong>${escapeAttr(homeLabel)} vs ${escapeAttr(awayLabel)}</strong>
        <span>横轴：客队进球（${escapeAttr(awayLabel)}） · 纵轴：主队进球（${escapeAttr(homeLabel)}）</span>
      </div>
      ${handicapLine !== undefined && handicapLine !== null ? `
        <div class="heatmap-handicap-note">
          <b>让球区域 ${formatHandicapLine(handicapLine)}</b>
          ${handicapRegionSummary('home', handicapTotals, handicapMarketTotals)}
          ${handicapRegionSummary('draw', handicapTotals, handicapMarketTotals)}
          ${handicapRegionSummary('away', handicapTotals, handicapMarketTotals)}
        </div>
      ` : ''}
      <div class="heatmap" style="--heatmap-goals: ${goals.length}">
        <div class="heatmap-head heatmap-corner">主队进球<br>${escapeAttr(homeLabel)}</div>
        ${goals.map((goal) => `<div class="heatmap-head">客队进球<br>${escapeAttr(awayLabel)} ${goal}</div>`).join('')}
        ${goals
          .map((home) => {
            const row = [`<div class="heatmap-head">主队进球<br>${escapeAttr(homeLabel)} ${home}</div>`];
            goals.forEach((away) => {
              const probability = cells.get(`${home}-${away}`) || 0;
              const level = heatLevel(probability, maxProbability);
              const regularOutcome = home > away ? '主胜' : home === away ? '平局' : '客胜';
              const handicapOutcome = handicapRegions[`${home}-${away}`];
              const regionClass = handicapOutcome ? ` handicap-region-${handicapOutcome}` : '';
              const title = `${home}-${away} ${formatPercent(probability)} · 普通${regularOutcome}${handicapOutcome ? ` · 让球${handicapOutcomeLabel(handicapOutcome)}` : ''}`;
              row.push(
                `<div class="heat-cell heat-level-${level}${regionClass}" title="${escapeAttr(title)}"><strong>${home}-${away}</strong><span>${formatPercent(probability)}</span>${handicapOutcome ? `<em>让${handicapOutcomeLabel(handicapOutcome)}</em>` : ''}</div>`
              );
            });
            return row.join('');
          })
          .join('')}
      </div>
      <div class="heat-legend" aria-hidden="true">
        <span>低</span>
        <i class="heat-level-1"></i>
        <i class="heat-level-2"></i>
        <i class="heat-level-3"></i>
        <i class="heat-level-4"></i>
        <i class="heat-level-5"></i>
        <i class="heat-level-6"></i>
        <span>高</span>
      </div>
      ${heatmap?.tail_probability ? `<div class="heat-tail-note">8+ tail：${formatPercent(heatmap.tail_probability)}，${heatmap.tail_note || '未画入 0-7 热力图。'}</div>` : ''}
    </div>
  `;
}

function scoreHeatmapGoals(matrix) {
  const maxGoal = matrix.reduce((max, item) => {
    const home = Number.isInteger(item.home_goals) ? item.home_goals : -1;
    const away = Number.isInteger(item.away_goals) ? item.away_goals : -1;
    return Math.max(max, home, away);
  }, 4);
  const upper = Math.min(Math.max(maxGoal, 7), 9);
  return Array.from({ length: upper + 1 }, (_, index) => index);
}

function handicapRegionSummary(outcome, modelTotals = {}, marketTotals = {}) {
  const className = `region-${outcome}`;
  const model = modelTotals?.[outcome];
  const market = marketTotals?.[outcome];
  const marketText = market !== undefined && market !== null ? ` · 市场 ${formatPercent(market)}` : '';
  return `<span class="region-pill ${className}"><b>让${handicapOutcomeLabel(outcome)}</b>${formatPercent(model || 0)}${marketText}</span>`;
}

function heatLevel(probability, maxProbability) {
  const ratio = probability / maxProbability;
  if (ratio >= 0.82) return 6;
  if (ratio >= 0.62) return 5;
  if (ratio >= 0.44) return 4;
  if (ratio >= 0.27) return 3;
  if (ratio >= 0.12) return 2;
  return 1;
}

function profileCard(teamName, profile = {}) {
  return `
    <div class="metric-card profile-card">
      <strong>${profile.flag ? `${profile.flag} ` : ''}${profile.team_zh || teamName}</strong>
      <span>Elo ${profile.elo ?? '-'} · 攻 ${profile.attack_rating ?? '-'} · 防 ${profile.defense_rating ?? '-'}</span>
      <small>近期加权样本 ${(profile.recent_weighted_matches ?? 0).toFixed(1)} · ${profile.wins ?? 0}胜 ${profile.draws ?? 0}平 ${profile.losses ?? 0}负</small>
    </div>
  `;
}

function strengthCard(label, strength = {}) {
  const fallback = strength.fallback_fields || [];
  const source = strength.paper_strength_source || strength.source || 'fallback';
  const baseline = strength.strength_baseline || '50 = 本届世界杯48队平均水平';
  return `
    <div class="metric-card profile-card strength-card">
      <strong>${label}</strong>
      <small class="strength-baseline">${baseline}</small>
      ${miniMeter('进攻', strength.attack_line_strength ?? strength.attack_strength ?? 50)}
      ${miniMeter('中场', strength.midfield_line_strength ?? strength.midfield_control_strength ?? 50)}
      ${miniMeter('后防', strength.defense_line_strength ?? strength.defense_gk_strength ?? 50)}
      ${miniMeter('门将', strength.goalkeeper_strength ?? strength.defense_gk_strength ?? 50)}
      <small>来源 ${source} · 覆盖率 ${formatPercent(strength.coverage ?? 0)} · 置信 ${formatPercent(strength.model_confidence ?? strength.coverage ?? 0)} · 待补 ${(strength.missing_player_stats ?? 0)} 人${fallback.length ? ` · fallback ${fallback.length} 项` : ''}</small>
    </div>
  `;
}

function miniMeter(label, value) {
  const numeric = Number(value);
  const quality = numeric >= 65 ? 'elite' : numeric >= 55 ? 'strong' : numeric >= 45 ? 'medium' : numeric >= 35 ? 'weak' : 'very-weak';
  const width = ((Math.min(85, Math.max(30, numeric)) - 30) / 55) * 100;
  return `
    <div class="mini-meter strength-${quality}">
      <span>${label}</span>
      <div class="bar-track"><div class="bar-fill home-fill" style="width:${width}%"></div></div>
      <strong>${numeric.toFixed(1)}</strong>
    </div>
  `;
}

async function loadSquadPanels(fixture) {
  const container = document.querySelector('#squad-panels');
  if (!container) return;
  const teams = [fixture.home_team, fixture.away_team];
  const panels = await Promise.all(
    teams.map(async (team) => {
      try {
        const squad = await api(`/api/teams/${encodeURIComponent(team)}/squad`);
        return squadPanel(team, squad);
      } catch (error) {
        return `
          <div class="squad-panel">
            <h4>${team}</h4>
            <div class="empty-state">尚未同步阵容。点击“同步本场阵容”获取教练和球员名单。</div>
          </div>
        `;
      }
    })
  );
  container.innerHTML = panels.join('');
}

function squadPanel(team, squad) {
  const players = squad.players || [];
  if (squad.available === false) {
    return `
      <div class="squad-panel">
        <h4>${squad.flag ? `${squad.flag} ` : ''}${squad.team_zh || team}</h4>
        <div class="empty-state">尚未同步阵容。点击“同步本场阵容”获取教练和球员名单。</div>
      </div>
    `;
  }
  return `
    <div class="squad-panel">
      <h4>${team}</h4>
      <div class="coach-line">教练：${squad.coach?.name || '-'} · ${squad.coach?.nationality || '-'} · ${squad.coach?.start || '任期未知'}</div>
      <div class="player-table">
        ${players
          .slice(0, 26)
          .map(
            (player) => `
              <div class="player-row">
                <strong>${player.number || '-'} · ${player.name}</strong>
                <span>${player.position || '-'} · ${player.club || '待补俱乐部'} · ${player.league || '待补联赛'} · ${player.player_strength ? Number(player.player_strength).toFixed(1) : '-'}</span>
              </div>
            `
          )
          .join('')}
      </div>
    </div>
  `;
}

async function syncFixtureSquads(fixture) {
  setStatus('正在同步本场双方阵容和教练...');
  try {
    await api(`/api/squads/sync?team=${encodeURIComponent(fixture.home_team)}`, { method: 'POST' });
    await api(`/api/squads/sync?team=${encodeURIComponent(fixture.away_team)}`, { method: 'POST' });
    await loadRosterHealth();
    await loadSquadPanels(fixture);
    setStatus('本场双方阵容已同步，球员俱乐部统计已加入补全队列。', 'success');
  } catch (error) {
    setStatus(`同步阵容失败：${error.message}`, 'error');
  }
}

async function processRosterQueue() {
  const button = document.querySelector('#process-roster-button');
  if (button) {
    button.disabled = true;
    button.textContent = '补全中...';
  }
  setStatus('正在按免费额度补全球员俱乐部统计...');
  try {
    const payload = await api('/api/squads/process-queue?limit=10', { method: 'POST' });
    await loadRosterHealth();
    if (state.selectedPrediction) {
      await loadPrediction(state.selectedPrediction.fixture.id);
    }
    setStatus(`球员统计补全完成：成功 ${payload.processed}，失败 ${payload.failed}。`, 'success');
  } catch (error) {
    setStatus(`补全阵容队列失败：${error.message}`, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '补全阵容队列';
    }
  }
}

async function enrichPublicRosterQueue() {
  const button = document.querySelector('#public-roster-button');
  if (button) {
    button.disabled = true;
    button.textContent = '公开源中...';
  }
  setStatus('正在用公开来源补全球员俱乐部与联赛信息...');
  try {
    const payload = await api('/api/squads/enrich-public?limit=10', { method: 'POST' });
    await loadRosterHealth();
    if (state.selectedPrediction) {
      await loadPrediction(state.selectedPrediction.fixture.id);
    }
    setStatus(`公开源补全完成：成功 ${payload.enriched}，失败 ${payload.failed}。`, 'success');
  } catch (error) {
    setStatus(`公开源补全失败：${error.message}`, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '公开源补全';
    }
  }
}

function setPredictionSectionCollapsed(section, collapsed) {
  if (!section?.dataset?.sectionId) return;
  const id = section.dataset.sectionId;
  state.collapsedPredictionSections[id] = collapsed;
  section.classList.toggle('is-collapsed', collapsed);
  const button = section.querySelector('[data-section-toggle]');
  if (button) {
    button.textContent = collapsed ? '展开' : '折叠';
    button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}

function setAllPredictionSectionsCollapsed(collapsed, onlyLong = false) {
  const longSections = new Set(['score', 'monte-carlo', 'profiles', 'ai-analysis', 'squads']);
  document.querySelectorAll('#prediction-detail .collapsible-section').forEach((section) => {
    if (onlyLong && !longSections.has(section.dataset.sectionId)) return;
    setPredictionSectionCollapsed(section, collapsed);
  });
}

document.querySelector('#sync-button').addEventListener('click', syncMatches);
document.querySelector('#round-sync-button').addEventListener('click', syncRoundOverview);
document.querySelector('#round-regress-button').addEventListener('click', regressRoundOverview);
document.querySelector('#sporttery-refresh-button').addEventListener('click', refreshSportteryOdds);
document.querySelector('#sync-reference-button').addEventListener('click', syncReferenceData);
document.querySelector('#validate-sources-button').addEventListener('click', validateSources);
document.querySelector('#process-roster-button').addEventListener('click', processRosterQueue);
document.querySelector('#public-roster-button').addEventListener('click', enrichPublicRosterQueue);
document.querySelector('#match-date').addEventListener('change', loadMatches);
document.querySelector('#prediction-detail').addEventListener('click', (event) => {
  const toggle = event.target.closest('[data-section-toggle]');
  if (toggle) {
    const section = toggle.closest('.collapsible-section');
    setPredictionSectionCollapsed(section, !section.classList.contains('is-collapsed'));
    return;
  }
  if (event.target.closest('[data-collapse-sections]')) {
    setAllPredictionSectionsCollapsed(true, true);
    return;
  }
  if (event.target.closest('[data-expand-sections]')) {
    setAllPredictionSectionsCollapsed(false);
  }
});
document.querySelector('#report-button').addEventListener('click', async () => {
  setStatus('正在生成中文日报...');
  try {
    await loadReport();
    setStatus('中文日报已生成。', 'success');
  } catch (error) {
    setStatus(`生成日报失败：${error.message}`, 'error');
  }
});
document.querySelector('#ranking-button').addEventListener('click', async () => {
  setStatus('正在刷新球队排名...');
  try {
    await loadRankings();
    setStatus('球队排名已刷新。', 'success');
  } catch (error) {
    setStatus(`刷新球队排名失败：${error.message}`, 'error');
  }
});
document.querySelector('#knockout-button').addEventListener('click', async () => {
  setStatus('正在刷新淘汰赛模拟...');
  try {
    await loadKnockout();
    setStatus('淘汰赛模拟已刷新。', 'success');
  } catch (error) {
    setStatus(`刷新淘汰赛模拟失败：${error.message}`, 'error');
  }
});
document.querySelectorAll('[data-view-tab]').forEach((button) => {
  button.addEventListener('click', () => showView(button.dataset.viewTab));
});
window.addEventListener('hashchange', updateActiveView);

function updateActiveView() {
  const hashView = (window.location.hash || '#rounds').slice(1);
  showView(normalizeView(hashView), { updateHash: false });
}

function showView(view, options = {}) {
  const nextView = normalizeView(view);
  state.activeView = nextView;
  document.querySelectorAll('[data-view]').forEach((panel) => {
    const active = panel.dataset.view === nextView;
    panel.classList.toggle('active', active);
    panel.setAttribute('aria-hidden', active ? 'false' : 'true');
  });
  document.querySelectorAll('[data-view-tab]').forEach((button) => {
    const active = button.dataset.viewTab === nextView;
    button.classList.toggle('active', active);
    button.setAttribute('aria-current', active ? 'page' : 'false');
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  if (options.updateHash !== false && window.location.hash !== `#${nextView}`) {
    window.location.hash = nextView;
  }
  if (nextView === 'detail') {
    void ensureDetailPrediction();
  }
}

function normalizeView(value) {
  const validViews = new Set(['rounds', 'today', 'detail', 'team-detail', 'models', 'health', 'report']);
  if (value === 'simulation') return 'models';
  return validViews.has(value) ? value : 'rounds';
}

bootstrap();
