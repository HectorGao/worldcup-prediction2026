const state = {
  matches: [],
  selectedPrediction: null,
  matchPredictions: {},
  availableDates: [],
  sourceValidation: [],
  rosterWeight: 0.25,
  rosterHealth: null,
  rounds: [],
  roundMatches: [],
  selectedStage: null,
  activeView: 'rounds',
  selectedTeamDetail: null
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
  return document.querySelector('#match-date').value || state.availableDates.at(-1) || '2026-06-16';
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
    setStatus('数据已加载。公开网页赛程与历史 CSV/Elo fallback 可用。', 'success');
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
  document.querySelector('#match-date').value = payload.date || date;
  renderMatches();
  setStatus(`已显示 ${payload.date || date} 的 ${state.matches.length} 场比赛。`, 'success');
  loadMatchCardPredictions();
}

async function syncMatches() {
  const date = selectedDate();
  const button = document.querySelector('#sync-button');
  button.disabled = true;
  button.textContent = '...';
  setStatus('正在同步公开网页赛程与历史比赛数据...');
  try {
    await api(`/api/sync?date=${date}`, { method: 'POST' });
    await api('/api/scrape/public-web', { method: 'POST' });
    await api('/api/scrape/lyihub?include_details=true&detail_limit=120', { method: 'POST' });
    await loadAvailableDates();
    await loadMatches();
    await loadRounds();
    await loadReport();
    await loadRankings();
    await loadKnockout();
    await loadRosterHealth();
    setStatus('同步完成：赛程、历史结果和 Elo 画像已刷新。', 'success');
  } catch (error) {
    setStatus(`同步失败：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '↻';
  }
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
    const prediction = await api(`/api/predict/${fixtureId}?roster_weight=${state.rosterWeight}`, { method: 'POST' });
    state.matchPredictions[fixtureId] = { status: 'ready', prediction };
    let analysis = null;
    try {
      analysis = await api(`/api/matches/${fixtureId}/analysis`);
    } catch (error) {
      setStatus(`核心预测已生成，详情分析加载失败：${error.message}`, 'error');
    }
    state.selectedPrediction = analysis?.prediction || prediction;
    renderPrediction(state.selectedPrediction, analysis);
    await loadSquadPanels(state.selectedPrediction.fixture);
    renderSimulation(state.selectedPrediction);
    renderMatches();
    showView('detail');
    setStatus('单场预测已生成。', 'success');
  } catch (error) {
    setStatus(`生成预测失败：${error.message}`, 'error');
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
        const prediction = await api(`/api/predict/${match.id}?roster_weight=${state.rosterWeight}`, { method: 'POST' });
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
  const payload = await api('/api/teams/rankings');
  const rows = payload.teams.slice(0, 12);
  document.querySelector('#team-rankings').innerHTML =
    rows.length === 0
      ? '<div class="empty-state">暂无球队画像。点击同步数据获取历史比赛并计算 Elo。</div>'
      : rows
          .map(
            (team, index) => `
              <div class="rank-item">
                <strong>${index + 1}. ${teamDisplay(team)}</strong>
                <span>Elo ${team.elo} · 攻 ${team.attack_rating ?? '-'} · 防 ${team.defense_rating ?? '-'}</span>
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
  document.querySelector('#match-count').textContent = `${state.matches.length} 场比赛`;
  list.innerHTML = state.matches
    .map(
      (match) => `
      <article class="match-row ${state.selectedPrediction?.fixture?.id === match.id ? 'selected' : ''}">
        <div class="teams">
          <strong>
            ${teamButton(match.home_team, teamDisplay(match, 'home'))}
            <span class="meta">vs</span>
            ${teamButton(match.away_team, teamDisplay(match, 'away'))}
          </strong>
          <span class="meta">${match.group || 'World Cup'} · ${match.venue || 'venue pending'} · ${match.kickoff}</span>
        </div>
        ${inlinePrediction(match)}
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
  return `
    <div class="inline-forecast" aria-label="胜平负预测概率">
      ${forecastPill(forecastLabel(match, 'home'), probs.home, best === 'home')}
      ${forecastPill('平局', probs.draw, best === 'draw')}
      ${forecastPill(forecastLabel(match, 'away'), probs.away, best === 'away')}
      ${scoreSummary(actualScore, predictedScore, accuracy)}
    </div>
  `;
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
        <article class="round-card">
          <div class="round-card-top">
            <span>${match.stage || match.group || 'World Cup'}</span>
            <span class="status-pill" data-status="${match.status}">${statusLabel(match.status)}</span>
          </div>
          <div class="round-card-score">
            ${teamButton(match.home_team, teamDisplay(match, 'home'))}
            <strong>${actualScoreText(match) || matchTime(match.kickoff)}</strong>
            ${teamButton(match.away_team, teamDisplay(match, 'away'))}
          </div>
          <div class="round-card-meta">${match.date} · ${match.venue || 'venue pending'}</div>
          <div class="round-card-meta">预测 ${match.predicted_score || '--'} · ${accuracyLabel(match.prediction_accuracy)}</div>
        </article>
      `
    )
    .join('');
  target.querySelectorAll('[data-team]').forEach((button) => {
    button.addEventListener('click', () => loadTeamDetail(button.dataset.team));
  });
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
  document.querySelector('#team-detail-view').innerHTML = `
    <div class="team-detail-grid">
      <section>
        <h3>本届对阵</h3>
        <div class="team-match-list">${matchRows || '<div class="empty-state">暂无比赛数据</div>'}</div>
      </section>
      <section>
        <h3>阵容与能力值</h3>
        <div class="coach-line">球员 ${detail.coverage.players} · 能力值 ${detail.coverage.players_with_ability} · 源能力 ${detail.coverage.players_with_source_ability ?? detail.coverage.players_with_ability} · 估算 ${detail.coverage.players_with_estimated_ability || 0} · 已知俱乐部 ${detail.coverage.players_with_club}</div>
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

function renderPrediction(prediction, analysis = null) {
  const fixture = prediction.fixture;
  const h2h = analysis?.head_to_head || {};
  const sections = analysis?.analysis_sections || [];
  const profiles = analysis?.team_profiles || {};
  document.querySelector('#selected-fixture').textContent = `${teamDisplay(fixture, 'home')} vs ${teamDisplay(fixture, 'away')}`;
  const probabilities = prediction.probabilities;
  document.querySelector('#prediction-detail').innerHTML = `
    <div class="prediction-body">
      <h3>胜/平/负</h3>
      <div class="prob-bars">
        ${bar('主胜', probabilities.home, 'home-fill')}
        ${bar('平局', probabilities.draw, 'draw-fill')}
        ${bar('客胜', probabilities.away, 'away-fill')}
      </div>
      <div class="metric-grid">
        <div class="metric-card"><strong>xG 主队</strong><span>${prediction.expected_goals.home.toFixed(2)}</span></div>
        <div class="metric-card"><strong>xG 客队</strong><span>${prediction.expected_goals.away.toFixed(2)}</span></div>
        <div class="metric-card"><strong>BTTS</strong><span>${formatPercent(prediction.btts)}</span></div>
        <div class="metric-card"><strong>Over 2.5</strong><span>${formatPercent(prediction.totals['2.5'].over)}</span></div>
      </div>
      <div class="control-card">
        <label for="roster-weight">阵容权重 <strong id="roster-weight-value">${Math.round(state.rosterWeight * 100)}%</strong></label>
        <input id="roster-weight" type="range" min="0" max="50" value="${Math.round(state.rosterWeight * 100)}" />
        <span>顶级锋线对薄弱后防会提高 xG；强防线会抵消对手进攻端优势。</span>
      </div>
      <h3>阵容三线强度</h3>
      <div class="profile-grid">
        ${strengthCard(teamDisplay(fixture, 'home'), prediction.roster_strength?.home)}
        ${strengthCard(teamDisplay(fixture, 'away'), prediction.roster_strength?.away)}
      </div>
      <div class="button-row">
        <button data-sync-squads="${fixture.id}">同步本场阵容</button>
        <button data-process-roster>补全球员统计</button>
      </div>
      <h3>最可能比分 Top 6</h3>
      <div class="score-grid">
        ${prediction.top_scorelines
          .map((item) => `<div class="score-item"><strong>${item.score}</strong><span>${formatPercent(item.probability)}</span></div>`)
          .join('')}
      </div>
      <h3>比分概率热力图</h3>
      ${scoreHeatmap(prediction.score_matrix)}
      <h3>球队画像</h3>
      <div class="profile-grid">
        ${profileCard(fixture.home_team, profiles.home)}
        ${profileCard(fixture.away_team, profiles.away)}
      </div>
      <h3>历史交锋</h3>
      <div class="analysis">
        可用样本 ${h2h.matches ?? 0} 场，加权样本 ${(h2h.weighted_matches ?? 0).toFixed(2)}；
        ${teamDisplay(fixture, 'home')} 视角 ${h2h.wins ?? 0} 胜 ${h2h.draws ?? 0} 平 ${h2h.losses ?? 0} 负。
      </div>
      ${sections.length ? `<h3>AI 解读草稿</h3>${sections.map((section) => `<div class="analysis"><strong>${section.title}</strong><br>${section.text}</div>`).join('')}` : ''}
      <h3>阵容与教练</h3>
      <div id="squad-panels" class="squad-grid">
        <div class="empty-state">正在加载阵容...</div>
      </div>
      <div class="analysis">${prediction.chinese_report}</div>
    </div>
  `;
  const slider = document.querySelector('#roster-weight');
  slider?.addEventListener('input', () => {
    state.rosterWeight = Number(slider.value) / 100;
    document.querySelector('#roster-weight-value').textContent = `${slider.value}%`;
  });
  slider?.addEventListener('change', () => loadPrediction(fixture.id));
  document.querySelector('[data-sync-squads]')?.addEventListener('click', () => syncFixtureSquads(fixture));
  document.querySelector('[data-process-roster]')?.addEventListener('click', () => processRosterQueue());
}

function renderSimulation(prediction) {
  const weights = prediction.model_blend_weights;
  document.querySelector('#simulation-summary').innerHTML = `
    <div class="metric-card"><strong>Dixon-Coles + ELO</strong><span>${formatPercent(weights.dixon_coles_elo)}</span></div>
    <div class="metric-card"><strong>市场校准</strong><span>${formatPercent(weights.market_calibration)}</span></div>
    <div class="metric-card"><strong>LLM 投票上限</strong><span>${formatPercent(weights.llm_vote_cap)}</span></div>
    <div class="metric-card"><strong>当前 LLM 权重</strong><span>${formatPercent(weights.llm_vote)}</span></div>
    <div class="metric-card"><strong>模型 xG</strong><span>${prediction.model_inputs?.home?.xg ?? '-'} / ${prediction.model_inputs?.away?.xg ?? '-'}</span></div>
    <div class="metric-card"><strong>DC rho</strong><span>${prediction.model_inputs?.rho ?? '-'}</span></div>
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

function scoreHeatmap(matrix) {
  const cells = new Map(matrix.map((item) => [`${item.home_goals}-${item.away_goals}`, item.probability]));
  const goals = [0, 1, 2, 3, 4];
  const visibleProbabilities = goals.flatMap((home) => goals.map((away) => cells.get(`${home}-${away}`) || 0));
  const maxProbability = Math.max(...visibleProbabilities, 0.01);
  return `
    <div class="heatmap-wrap" aria-label="比分概率热力图，颜色越暖代表概率越高">
      <div class="heatmap">
        <div class="heatmap-head"></div>
        ${goals.map((goal) => `<div class="heatmap-head">客 ${goal}</div>`).join('')}
        ${goals
          .map((home) => {
            const row = [`<div class="heatmap-head">主 ${home}</div>`];
            goals.forEach((away) => {
              const probability = cells.get(`${home}-${away}`) || 0;
              const level = heatLevel(probability, maxProbability);
              row.push(
                `<div class="heat-cell heat-level-${level}" title="${home}-${away} ${formatPercent(probability)}"><strong>${home}-${away}</strong><span>${formatPercent(probability)}</span></div>`
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
    </div>
  `;
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
  return `
    <div class="metric-card profile-card">
      <strong>${label}</strong>
      ${miniMeter('进攻', strength.attack_strength ?? 70)}
      ${miniMeter('中场', strength.midfield_control_strength ?? 70)}
      ${miniMeter('后防+门将', strength.defense_gk_strength ?? 70)}
      <small>覆盖率 ${formatPercent(strength.coverage ?? 0)} · 待补 ${(strength.missing_player_stats ?? 0)} 人</small>
    </div>
  `;
}

function miniMeter(label, value) {
  return `
    <div class="mini-meter">
      <span>${label}</span>
      <div class="bar-track"><div class="bar-fill home-fill" style="width:${Math.min(100, Math.max(0, value))}%"></div></div>
      <strong>${Number(value).toFixed(1)}</strong>
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

document.querySelector('#sync-button').addEventListener('click', syncMatches);
document.querySelector('#sync-reference-button').addEventListener('click', syncReferenceData);
document.querySelector('#validate-sources-button').addEventListener('click', validateSources);
document.querySelector('#process-roster-button').addEventListener('click', processRosterQueue);
document.querySelector('#public-roster-button').addEventListener('click', enrichPublicRosterQueue);
document.querySelector('#match-date').addEventListener('change', loadMatches);
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
}

function normalizeView(value) {
  const validViews = new Set(['rounds', 'today', 'detail', 'team-detail', 'models', 'health', 'report']);
  if (value === 'simulation') return 'models';
  return validViews.has(value) ? value : 'rounds';
}

bootstrap();
