/* ── 상태 ─────────────────────────────────────────────────────────────────── */
let currentPeriod       = '1m';
let currentMarketFilter = 'ALL';
let allStocks           = [];
let lastPositions       = [];
let lastCash            = 10_000_000;
let liveStockPrices     = {};
let watchlist           = new Set(JSON.parse(localStorage.getItem('stockWatchlist') || '[]'));
let stockPickerActiveIndex = -1;
let stockPickerMatches = [];
let stockPickerRequestId = 0;
let currentStockPrice = 0;

/* ── LW Charts ───────────────────────────────────────────────────────────── */
let lwChart  = null;
let lwCandle = null;
let lwVolume = null;

function initStockChart() {
  const container = document.getElementById('stockChart');
  if (!container || !window.LightweightCharts) return;

  lwChart = LightweightCharts.createChart(container, {
    layout:     { background: { color: '#FFFFFF' }, textColor: '#6B7280' },
    grid:       { vertLines: { color: '#F3F4F6' }, horzLines: { color: '#F3F4F6' } },
    crosshair:  { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#E5E7EB' },
    timeScale:  { borderColor: '#E5E7EB', timeVisible: true, secondsVisible: false },
    handleScroll: true, handleScale: true,
  });

  lwCandle = lwChart.addCandlestickSeries({
    upColor: '#E11D48', downColor: '#2563EB',
    borderUpColor: '#E11D48', borderDownColor: '#2563EB',
    wickUpColor:   '#E11D48', wickDownColor:   '#2563EB',
  });

  lwVolume = lwChart.addHistogramSeries({
    color: 'rgba(41,98,255,0.35)',
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
    scaleMargins: { top: 0.85, bottom: 0 },
  });

  new ResizeObserver(() => {
    if (lwChart && container) lwChart.resize(container.clientWidth, container.clientHeight);
  }).observe(container);
}

/* ── 포트폴리오 도넛 (미니) ─────────────────────────────────────────────── */
let portfolioChart = null;

function initPortfolioChart() {
  const el = document.getElementById('portfolioChart');
  if (!el || !window.LightweightCharts) return;
  // ApexCharts 미사용 시 간단히 생략 — 필요 시 별도 라이브러리 추가
}

function updatePortfolioMini(positions, cash) {
  const el = document.getElementById('portfolioChart');
  if (!el) return;
  const total = cash + positions.reduce((s, p) => s + (p.evalAmount || 0), 0);
  if (total <= 0) { el.innerHTML = ''; return; }

  const cashPct = Math.round(cash / total * 100);
  const colors = ['#2563EB', '#7C3AED', '#059669', '#EA580C', '#DB2777', '#0891B2', '#65A30D'];
  const stockBars = positions.map((p, i) => {
    const pct = Math.round((p.evalAmount || 0) / total * 100);
    return `<div title="${p.name} ${pct}%" style="flex:${pct};background:${colors[i % colors.length]};min-width:3px;"></div>`;
  });
  stockBars.push(`<div title="현금 ${cashPct}%" style="flex:${cashPct};background:#CBD5E1;min-width:3px;"></div>`);

  const sectors = positions.reduce((acc, p) => {
    const sector = p.sector || '기타';
    acc[sector] = (acc[sector] || 0) + (p.evalAmount || 0);
    return acc;
  }, {});
  const sectorItems = Object.entries(sectors).sort((a, b) => b[1] - a[1]);
  const sectorBars = sectorItems.map(([sector, amount], i) => {
    const pct = Math.round(amount / total * 100);
    return `<div title="${sector} ${pct}%" style="flex:${pct};background:${colors[i % colors.length]};min-width:3px;"></div>`;
  });
  if (cashPct) sectorBars.push(`<div title="현금 ${cashPct}%" style="flex:${cashPct};background:#CBD5E1;min-width:3px;"></div>`);
  const sectorLabels = sectorItems.map(([sector, amount], i) =>
    `<span style="display:inline-flex;align-items:center;gap:3px;"><i style="width:6px;height:6px;border-radius:50%;background:${colors[i % colors.length]};display:inline-block;"></i>${sector} ${Math.round(amount / total * 100)}%</span>`
  ).join(' · ');

  el.innerHTML = `<div style="font-size:10px;font-weight:700;color:var(--muted);margin-top:4px;">종목별 비중</div>
    <div style="display:flex;height:7px;border-radius:4px;overflow:hidden;gap:1px;margin-top:3px;">${stockBars.join('')}</div>
    <div style="font-size:10px;font-weight:700;color:var(--muted);margin-top:7px;">섹터별 비중</div>
    <div style="display:flex;height:7px;border-radius:4px;overflow:hidden;gap:1px;margin-top:3px;">${sectorBars.join('')}</div>
    <div style="font-size:9px;line-height:1.5;color:var(--muted);margin-top:4px;">${sectorLabels || '보유 주식 없음'}${sectorLabels ? ` · 현금 ${cashPct}%` : ''}</div>`;
}

/* ── 포맷터 ──────────────────────────────────────────────────────────────── */
function fmtKrw(v) { return Number(v).toLocaleString('ko-KR') + '원'; }
function fmtVol(v) {
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '억주';
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '만주';
  return Number(v).toLocaleString('ko-KR') + '주';
}
function colorByVal(v) { return v > 0 ? '#E11D48' : v < 0 ? '#2563EB' : '#787B86'; }

function selectedPosition() {
  const symbol = document.getElementById('stockSymbol')?.value;
  return lastPositions.find(position => position.symbol === symbol);
}

function updateOrderSummary() {
  const summary = document.getElementById('orderSummary');
  const qty = Number(document.getElementById('orderQty')?.value) || 0;
  if (!summary) return;
  const position = selectedPosition();
  const holdingQty = position?.quantity ?? 0;
  const orderAmount = currentStockPrice > 0 && qty > 0 ? currentStockPrice * qty : 0;
  summary.innerHTML = `보유 현금 <strong style="color:var(--fg);">${fmtKrw(lastCash)}</strong> · 보유 주식 <strong style="color:var(--fg);">${holdingQty.toLocaleString('ko-KR')}주</strong><br>예상 주문금액 <strong style="color:var(--accent-dark);">${orderAmount ? fmtKrw(orderAmount) : '-'}</strong>`;
}

function setOrderQuantityByPercent(side, percent) {
  if (!currentStockPrice) { showMsg('현재 시세를 불러온 뒤 선택해주세요.', true); return; }
  const position = selectedPosition();
  const quantity = side === 'buy'
    ? Math.floor(lastCash * (percent / 100) / currentStockPrice)
    : Math.floor((position?.quantity ?? 0) * (percent / 100));
  if (quantity < 1) {
    showMsg(side === 'buy' ? '보유 현금으로 매수 가능한 수량이 없습니다.' : '매도 가능한 보유 수량이 없습니다.', true);
    return;
  }
  const input = document.getElementById('orderQty');
  if (input) input.value = quantity;
  updateOrderSummary();
}

/* ── API fetch helper ────────────────────────────────────────────────────── */
async function requestJson(url, options = {}) {
  const fullUrl  = url.startsWith('/') ? API_BASE + url : url;
  const response = await fetch(fullUrl, { credentials: 'include', ...options });
  const raw = await response.text();
  let data = null;
  try { data = raw.trim() ? JSON.parse(raw) : null; } catch { throw new Error('응답 형식 오류'); }
  if (!response.ok) throw new Error(data?.message || '요청 실패');
  if (data === null) throw new Error('빈 응답');
  return data;
}

function showMsg(msg, isErr = false) {
  const el = document.getElementById('stockMessage');
  if (el) { el.textContent = msg; el.style.color = isErr ? '#E11D48' : '#2E7D32'; }
}

/* ── Watchlist ───────────────────────────────────────────────────────────── */
function saveWatchlist() { localStorage.setItem('stockWatchlist', JSON.stringify([...watchlist])); }
function updateWatchBtn(sym) {
  const btn = document.getElementById('watchlistBtn');
  if (!btn) return;
  const has = watchlist.has(sym);
  btn.textContent = has ? '⭐' : '☆';
  btn.style.color = has ? '#FFCC00' : 'rgba(255,255,255,0.4)';
}
document.getElementById('watchlistBtn')?.addEventListener('click', () => {
  const sym = document.getElementById('stockSymbol')?.value;
  if (!sym) return;
  watchlist.has(sym) ? watchlist.delete(sym) : watchlist.add(sym);
  saveWatchlist(); updateWatchBtn(sym);
  if (currentMarketFilter === 'WATCH') rebuildSelectOptions();
});

/* ── 마켓 리스트 (실시간 5초 polling) ───────────────────────────────────── */
async function loadBatchPrices() {
  try {
    const symbols = allStocks.slice(0, 50).map(stock => stock.symbol).join(',');
    const data = await requestJson(`/api/stocks/prices?symbols=${encodeURIComponent(symbols)}`);
    liveStockPrices = data.prices ?? {};
    renderStockMarketList();
  } catch {}
}

function renderStockMarketList() {
  const tbody = document.getElementById('stockMarketListBody');
  if (!tbody) return;

  const search = (document.getElementById('stockSearch')?.value ?? '').toLowerCase();
  const stocks = allStocks.filter(s => {
    if (currentMarketFilter === 'KOSPI'  && s.market !== 'KOSPI')  return false;
    if (currentMarketFilter === 'KOSDAQ' && s.market !== 'KOSDAQ') return false;
    if (currentMarketFilter === 'WATCH'  && !watchlist.has(s.symbol)) return false;
    if (search && !s.name.toLowerCase().includes(search) && !s.symbol.includes(search) && !(s.sector || '').toLowerCase().includes(search)) return false;
    return true;
  });

  if (!stocks.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="padding:12px;text-align:center;color:var(--muted);">종목 없음</td></tr>`;
    return;
  }

  tbody.innerHTML = stocks.map(s => {
    const p = liveStockPrices[s.symbol];
    const price = p ? Number(p.price).toLocaleString('ko-KR') : '-';
    const rate  = p ? Number(p.changeRate) : 0;
    const color = colorByVal(rate);
    const rateStr = p ? (rate >= 0 ? '+' : '') + rate.toFixed(2) + '%' : '-';
    const hasWatch = watchlist.has(s.symbol);
    return `<tr onclick="selectStockFromList('${s.symbol}')"
              style="cursor:pointer;border-bottom:1px solid var(--border);">
      <td style="padding:6px 10px;">
        <div style="font-weight:700;color:var(--fg);font-size:12px;">${s.name}</div>
        <div style="font-size:10px;color:var(--muted);">${s.market} · ${s.sector || '기타'}</div>
      </td>
      <td style="padding:6px 10px;text-align:right;font-weight:700;color:var(--fg);font-size:12px;">${price}</td>
      <td style="padding:6px 10px;text-align:right;font-size:11px;font-weight:700;color:${color};">${rateStr}</td>
      <td style="padding:6px 5px;text-align:center;" onclick="event.stopPropagation()">
        <button onclick="toggleStockWatch('${s.symbol}')"
          style="background:none;border:none;cursor:pointer;font-size:12px;color:${hasWatch ? '#FFCC00' : 'var(--muted)'};">${hasWatch ? '⭐' : '☆'}</button>
      </td>
    </tr>`;
  }).join('');
}

function toggleStockWatch(sym) {
  watchlist.has(sym) ? watchlist.delete(sym) : watchlist.add(sym);
  saveWatchlist();
  renderStockMarketList();
  updateWatchBtn(sym);
  if (currentMarketFilter === 'WATCH') rebuildSelectOptions();
}

async function selectStockFromList(sym) {
  currentMarketFilter = 'ALL';
  document.querySelectorAll('.market-tab').forEach(t => t.classList.toggle('active', t.dataset.market === 'ALL'));
  await selectStock(sym);
}

/* ── 종목 검색·선택 ─────────────────────────────────────────────────────── */
async function loadStockList() {
  const data = await requestJson('/api/stocks/list?limit=30');
  allStocks = data.stocks ?? [];
  const requestedSymbol = new URLSearchParams(window.location.search).get('symbol')?.trim().toUpperCase();
  if (requestedSymbol) {
    try {
      const search = await requestJson(`/api/stocks/search?q=${encodeURIComponent(requestedSymbol)}&limit=20`);
      const requestedStock = (search.stocks ?? []).find(stock => stock.symbol === requestedSymbol);
      if (requestedStock) addStockToPicker(requestedStock);
    } catch {}
  }
  rebuildSelectOptions();
  if (requestedSymbol && allStocks.some(stock => stock.symbol === requestedSymbol)) {
    const select = document.getElementById('stockSymbol');
    if (select) select.value = requestedSymbol;
    updateStockPickerSelected(requestedSymbol);
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
}

function addStockToPicker(stock) {
  if (!stock?.symbol || allStocks.some(item => item.symbol === stock.symbol)) return;
  allStocks.push(stock);
}

async function fetchStockPickerMatches(query = '') {
  const keyword = String(query).trim();
  if (!keyword) return allStocks.slice(0, 12);
  const data = await requestJson(`/api/stocks/search?q=${encodeURIComponent(keyword)}&limit=20`);
  return data.stocks ?? [];
}

function updateStockPickerSelected(symbol) {
  const stock = allStocks.find(item => item.symbol === symbol);
  const input = document.getElementById('stockPickerInput');
  const label = document.getElementById('stockPickerSelected');
  if (!stock) return;
  if (input) input.value = stock.name;
  if (label) label.textContent = `${stock.symbol} · ${stock.market} · ${stock.sector || '기타'}`;
}

function closeStockPicker() {
  const results = document.getElementById('stockSearchResults');
  const input = document.getElementById('stockPickerInput');
  if (results) results.classList.remove('open');
  if (input) {
    input.setAttribute('aria-expanded', 'false');
    updateStockPickerSelected(document.getElementById('stockSymbol')?.value);
  }
  stockPickerActiveIndex = -1;
}

function renderStockPickerResults() {
  const results = document.getElementById('stockSearchResults');
  const input = document.getElementById('stockPickerInput');
  if (!results || !input) return;
  const matches = stockPickerMatches;
  if (stockPickerActiveIndex >= matches.length) stockPickerActiveIndex = matches.length - 1;

  results.innerHTML = matches.length
    ? matches.map((stock, index) => `<button type="button" class="stock-picker-result${index === stockPickerActiveIndex ? ' active' : ''}" role="option" aria-selected="${stock.symbol === document.getElementById('stockSymbol')?.value}" data-symbol="${escapeHtml(stock.symbol)}">
        <span><strong class="stock-picker-result-name">${escapeHtml(stock.name)}</strong><small class="stock-picker-result-meta">${escapeHtml(stock.market)} · ${escapeHtml(stock.sector || '기타')}</small></span>
        <code class="stock-picker-result-code">${escapeHtml(stock.symbol)}</code>
      </button>`).join('')
    : '<div class="stock-picker-empty">일치하는 종목이 없습니다.</div>';
  results.classList.add('open');
  input.setAttribute('aria-expanded', 'true');
}

async function searchStockPicker(query = document.getElementById('stockPickerInput')?.value ?? '') {
  const requestId = ++stockPickerRequestId;
  try {
    const matches = await fetchStockPickerMatches(query);
    if (requestId !== stockPickerRequestId) return [];
    matches.forEach(addStockToPicker);
    stockPickerMatches = matches;
    if (stockPickerActiveIndex >= matches.length) stockPickerActiveIndex = matches.length - 1;
    renderStockPickerResults();
    return matches;
  } catch (error) {
    if (requestId !== stockPickerRequestId) return [];
    stockPickerMatches = [];
    const results = document.getElementById('stockSearchResults');
    if (results) {
      results.innerHTML = `<div class="stock-picker-empty">${escapeHtml(error.message || 'KRX 종목 검색을 사용할 수 없습니다.')}</div>`;
      results.classList.add('open');
    }
    return [];
  }
}

async function selectStock(symbol) {
  const select = document.getElementById('stockSymbol');
  if (!select) return;
  const stock = allStocks.find(item => item.symbol === symbol);
  if (!stock) return;
  if (!Array.from(select.options).some(option => option.value === symbol)) {
    const option = document.createElement('option');
    option.value = symbol;
    option.textContent = `${stock.name} · ${stock.sector || '기타'} (${symbol})`;
    select.appendChild(option);
  }
  select.value = symbol;
  updateStockPickerSelected(symbol);
  closeStockPicker();
  updateWatchBtn(symbol);
  await Promise.all([loadQuote(symbol), loadChart(symbol, currentPeriod)]);
}

function rebuildSelectOptions() {
  renderStockMarketList();
  const sel = document.getElementById('stockSymbol');
  if (!sel) return;
  const prevVal  = sel.value;
  sel.innerHTML  = '';

  if (!allStocks.length) {
    const opt = document.createElement('option');
    opt.disabled = true;
    opt.textContent = '종목 정보 없음';
    sel.appendChild(opt);
    return;
  }

  const markets = [...new Set(allStocks.map(s => s.market))];
  markets.forEach(market => {
    const grp = document.createElement('optgroup');
    grp.label = market;
    allStocks.filter(s => s.market === market).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.symbol;
      opt.textContent = `${s.name} · ${s.sector || '기타'} (${s.symbol})`;
      if (s.symbol === prevVal) opt.selected = true;
      grp.appendChild(opt);
    });
    sel.appendChild(grp);
  });

  sel.value = allStocks.some(s => s.symbol === prevVal) ? prevVal : allStocks[0].symbol;
  updateStockPickerSelected(sel.value);
}

/* ── 마켓 탭 ─────────────────────────────────────────────────────────────── */
document.getElementById('marketTabs')?.addEventListener('click', e => {
  const btn = e.target.closest('.market-tab');
  if (!btn) return;
  currentMarketFilter = btn.dataset.market;
  document.querySelectorAll('.market-tab').forEach(t => t.classList.toggle('active', t === btn));
  rebuildSelectOptions();
});

/* ── 차트 로드 ───────────────────────────────────────────────────────────── */
async function loadChart(symbol, period) {
  if (!lwCandle) return;
  try {
    const data = await requestJson(`/api/stocks/chart?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(period)}`);
    const candles = (data.data ?? []).map(d => ({ time: Math.floor(d.x / 1000), open: d.o, high: d.h, low: d.l, close: d.c }))
      .sort((a, b) => a.time - b.time);
    const volumes = (data.data ?? []).map(d => ({
      time: Math.floor(d.x / 1000), value: d.v,
      color: d.c >= d.o ? 'rgba(248,113,113,0.35)' : 'rgba(96,165,250,0.35)',
    })).sort((a, b) => a.time - b.time);
    lwCandle.setData(candles);
    lwVolume.setData(volumes);
    lwChart.timeScale().fitContent();
  } catch {}
}

/* ── 시세 조회 ───────────────────────────────────────────────────────────── */
async function loadQuote(symbol) {
  if (!symbol) return;
  try {
    const data = await requestJson(`/api/stocks/quote?symbol=${encodeURIComponent(symbol)}`);
    const rate  = Number(data.changeRate ?? 0);
    const color = colorByVal(rate);
    currentStockPrice = Number(data.price ?? 0);

    setText('chartStockName',  data.name ?? '-');
    setEl('quotePrice',        fmtKrw(data.price ?? 0), color);
    setEl('quoteChange',       (Number(data.change ?? 0) >= 0 ? '+' : '') + fmtKrw(data.change ?? 0), color);
    setEl('quoteChangeRate',   (rate >= 0 ? '+' : '') + rate.toFixed(2) + '%', color);
    setText('quoteVolume',     data.volume ? fmtVol(data.volume) : '-');
    setText('quoteMarket',     data.market ?? '-');
    if (data.simulated) document.getElementById('dataSourceBadge')?.classList.remove('hidden');
    else                document.getElementById('dataSourceBadge')?.classList.add('hidden');

    renderOrderBook(data.price);
    updateBreakEven(lastPositions, symbol);
    updateWatchBtn(symbol);
    updateOrderSummary();

    // 라이브 가격 업데이트
    liveStockPrices[symbol] = { ...liveStockPrices[symbol], price: data.price, changeRate: rate };
    renderStockMarketList();
    if (symbol === avgDownSymbol) refreshAvgDownLive();
  } catch {}
}

/* ── 시장 지수 ───────────────────────────────────────────────────────────── */
async function loadMarket() {
  try {
    const data = await requestJson('/api/stocks/market');
    for (const [key, val] of Object.entries({ KOSPI: data.KOSPI, KOSDAQ: data.KOSDAQ })) {
      const p = key.toLowerCase();
      setText(p + 'Price', Number(val.price).toLocaleString('ko-KR', { minimumFractionDigits: 2 }));
      const rate  = Number(val.changeRate);
      const color = colorByVal(rate);
      setEl(p + 'Change', `${rate >= 0 ? '▲' : '▼'} ${Math.abs(rate).toFixed(2)}%`, color);
    }
  } catch {}
}

/* ── 계좌 + 포지션 ───────────────────────────────────────────────────────── */
async function loadAccount() {
  const data = await requestJson('/api/stocks/account');
  lastCash = data.cash;
  setText('accountCash',   fmtKrw(data.cash));
  setText('accountAsset',  fmtKrw(data.totalAsset));
  const pnl = Number(data.totalPnlRate);
  setEl('accountPnlRate', (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + '%', colorByVal(pnl));
  updatePortfolioMini(lastPositions, data.cash);
  updateOrderSummary();
  refreshAvgDownLive();
}

async function loadPositions() {
  const data = await requestJson('/api/stocks/positions');
  lastPositions = data.positions ?? [];
  const tbody = document.getElementById('positionsBody');
  if (!tbody) return;

  if (!lastPositions.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="padding:10px;text-align:center;color:var(--muted);">포지션 없음</td></tr>`;
    updatePortfolioMini([], lastCash);
    updateOrderSummary();
    refreshAvgDownLive();
    return;
  }
  tbody.innerHTML = lastPositions.map(pos => {
    const pnl   = Number(pos.pnl ?? 0);
    const color = colorByVal(pnl);
    return `<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
      <td style="padding:6px 10px;font-weight:700;color:var(--fg);font-size:12px;">${pos.name}<br><span style="font-size:10px;color:var(--accent-dark);">${pos.symbol}</span></td>
      <td style="padding:6px 10px;font-size:10px;color:var(--muted);white-space:nowrap;">${pos.sector || '기타'}</td>
      <td style="padding:6px 10px;text-align:right;font-size:12px;color:var(--fg);">${pos.quantity}</td>
      <td style="padding:6px 10px;text-align:right;font-size:12px;color:rgba(255,255,255,0.7);">${fmtKrw(pos.avgPrice)}</td>
      <td style="padding:6px 10px;text-align:right;font-size:12px;color:var(--accent-dark);">${fmtKrw(pos.evalAmount)}</td>
      <td style="padding:6px 10px;text-align:right;font-size:13px;font-weight:800;color:${color};">${pnl >= 0 ? '+' : ''}${fmtKrw(pnl)}</td>
      <td style="padding:6px 6px;text-align:center;">
        <button type="button" class="ad-mini-btn" onclick="openAvgDownModal('${escapeHtml(pos.symbol)}')" title="${escapeHtml(pos.name)} 물타기 시뮬레이션">물타기</button>
      </td>
    </tr>`;
  }).join('');
  updatePortfolioMini(lastPositions, lastCash);
  updateBreakEven(lastPositions, document.getElementById('stockSymbol')?.value);
  updateOrderSummary();
  refreshAvgDownLive();
}

async function loadHistory() {
  try {
    const data = await requestJson('/api/stocks/orders/history');
    const tbody = document.getElementById('historyBody');
    if (!tbody) return;
    const hist = (data.history ?? []).slice(0, 30);
    if (!hist.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="padding:10px;text-align:center;color:var(--muted);">거래 내역 없음</td></tr>`;
      return;
    }
    tbody.innerHTML = hist.map(h => {
      const isBuy = h.type === 'BUY';
      const color = isBuy ? '#E11D48' : '#2563EB';
      const dt    = new Date(h.ts).toLocaleTimeString('ko-KR', { hour12: false });
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td style="padding:5px 10px;color:var(--muted);font-size:11px;">${dt}</td>
        <td style="padding:5px 10px;font-weight:700;color:var(--fg);font-size:12px;">${h.name}<br><span style="font-size:10px;color:var(--accent-dark);">${h.symbol}</span></td>
        <td style="padding:5px 10px;text-align:center;font-weight:800;font-size:12px;color:${color};">${isBuy ? '매수' : '매도'}</td>
        <td style="padding:5px 10px;text-align:right;color:rgba(255,255,255,0.7);font-size:12px;">${Number(h.quantity).toLocaleString('ko-KR')}주</td>
        <td style="padding:5px 10px;text-align:right;color:var(--accent-dark);font-weight:700;font-size:12px;">${fmtKrw(h.amount)}</td>
      </tr>`;
    }).join('');
  } catch {}
}

/* ── 호가창 ──────────────────────────────────────────────────────────────── */
function renderOrderBook(price) {
  if (!price || price <= 0) return;
  const askBody = document.getElementById('askBody');
  const bidBody = document.getElementById('bidBody');
  if (!askBody || !bidBody) return;

  let tick = 1;
  if      (price >= 500000) tick = 1000;
  else if (price >= 100000) tick = 500;
  else if (price >=  50000) tick = 100;
  else if (price >=  10000) tick = 50;
  else if (price >=   1000) tick = 10;

  const qty = (p, o) => Math.max(50, ((p * 7 + o) % 2900) + 100);
  const askRows = Array.from({ length: 5 }, (_, i) => ({ price: price + tick * (5 - i), qty: qty(price + tick * (5 - i), 13) }));
  const bidRows = Array.from({ length: 5 }, (_, i) => ({ price: price - tick * (i + 1), qty: qty(price - tick * (i + 1), 31) }));

  askBody.innerHTML = askRows.map(r => `<tr style="background:rgba(37,99,235,0.04);">
    <td style="padding:4px 10px;text-align:right;color:#60A5FA;font-weight:700;font-size:11px;">${Number(r.price).toLocaleString('ko-KR')}</td>
    <td style="padding:4px 10px;text-align:right;color:var(--muted);font-size:11px;">${Number(r.qty).toLocaleString('ko-KR')}</td></tr>`).join('');
  bidBody.innerHTML = bidRows.map(r => `<tr style="background:rgba(225,29,72,0.04);">
    <td style="padding:4px 10px;text-align:right;color:#F87171;font-weight:700;font-size:11px;">${Number(r.price).toLocaleString('ko-KR')}</td>
    <td style="padding:4px 10px;text-align:right;color:var(--muted);font-size:11px;">${Number(r.qty).toLocaleString('ko-KR')}</td></tr>`).join('');

  setText('obCurrentPrice', Number(price).toLocaleString('ko-KR'));
  const spread = tick * 2;
  setText('obSpread', `${Number(spread).toLocaleString('ko-KR')} (${((spread / price) * 100).toFixed(3)}%)`);
}

function updateBreakEven(positions, sym) {
  const el = document.getElementById('quoteBreakEven');
  if (!el) return;
  const pos = positions?.find(p => p.symbol === sym);
  if (pos) { el.textContent = `${Number(pos.avgPrice).toLocaleString('ko-KR')}원`; el.style.color = '#FFCC00'; }
  else      { el.textContent = '-'; el.style.color = 'var(--muted)'; }
}

/* ── 주문 ────────────────────────────────────────────────────────────────── */
async function submitOrder(type) {
  const qty = Number(document.getElementById('orderQty')?.value);
  if (!Number.isFinite(qty) || qty <= 0) { showMsg('수량은 1 이상이어야 합니다.', true); return; }
  try {
    await requestJson(`/api/stocks/orders/${type}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: document.getElementById('stockSymbol')?.value, quantity: qty }),
    });
    showMsg(`${type === 'buy' ? '매수' : '매도'} 완료`);
    await Promise.all([loadAccount(), loadPositions(), loadQuote(document.getElementById('stockSymbol')?.value), loadHistory()]);
  } catch (e) { showMsg(e.message, true); }
}

document.getElementById('buyBtn')?.addEventListener('click',  () => submitOrder('buy'));
document.getElementById('sellBtn')?.addEventListener('click', () => submitOrder('sell'));
document.getElementById('orderQty')?.addEventListener('input', updateOrderSummary);
document.querySelectorAll('.order-percent-btn').forEach(button => {
  button.addEventListener('click', () => setOrderQuantityByPercent(button.dataset.orderSide, Number(button.dataset.percent)));
});

/* ── 초기화 버튼 ─────────────────────────────────────────────────────────── */
document.getElementById('resetBtn')?.addEventListener('click', async () => {
  if (!confirm('계좌를 초기화하시겠습니까?')) return;
  try {
    await requestJson('/api/stocks/account/reset', { method: 'POST' });
    showMsg('계좌 초기화 완료');
    await Promise.all([loadAccount(), loadPositions(), loadHistory()]);
  } catch (e) { showMsg(e.message, true); }
});

/* ── 기간 버튼 ───────────────────────────────────────────────────────────── */
document.getElementById('periodBtns')?.addEventListener('click', async e => {
  const btn = e.target.closest('.period-btn');
  if (!btn) return;
  currentPeriod = btn.dataset.period;
  document.querySelectorAll('#periodBtns .period-btn').forEach(b => b.classList.toggle('active', b === btn));
  await loadChart(document.getElementById('stockSymbol')?.value, currentPeriod);
});

/* ── 종목 변경 ───────────────────────────────────────────────────────────── */
document.getElementById('stockSymbol')?.addEventListener('change', async () => {
  const sym = document.getElementById('stockSymbol')?.value;
  if (!sym) return;
  updateStockPickerSelected(sym);
  await Promise.all([loadQuote(sym), loadChart(sym, currentPeriod)]);
});

/* ── 검색형 종목 선택기 ─────────────────────────────────────────────────── */
const stockPickerInput = document.getElementById('stockPickerInput');
const stockPickerResults = document.getElementById('stockSearchResults');

stockPickerInput?.addEventListener('focus', () => {
  stockPickerActiveIndex = -1;
  searchStockPicker(stockPickerInput.value);
  stockPickerInput.select();
});

stockPickerInput?.addEventListener('input', () => {
  stockPickerActiveIndex = -1;
  searchStockPicker(stockPickerInput.value);
});

stockPickerInput?.addEventListener('keydown', async event => {
  const matches = stockPickerMatches;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeStockPicker();
    stockPickerInput.blur();
    return;
  }
  if (!matches.length || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
  event.preventDefault();
  if (event.key === 'ArrowDown') {
    stockPickerActiveIndex = (stockPickerActiveIndex + 1) % matches.length;
    renderStockPickerResults();
  } else if (event.key === 'ArrowUp') {
    stockPickerActiveIndex = (stockPickerActiveIndex - 1 + matches.length) % matches.length;
    renderStockPickerResults();
  } else {
    await selectStock(matches[Math.max(stockPickerActiveIndex, 0)].symbol);
  }
});

async function submitStockPickerSearch() {
  if (!stockPickerInput) return;
  const matches = await searchStockPicker(stockPickerInput.value);
  if (!stockPickerInput.value.trim()) {
    stockPickerActiveIndex = -1;
    renderStockPickerResults();
    stockPickerInput.focus();
    return;
  }
  if (matches.length === 1) {
    await selectStock(matches[0].symbol);
    return;
  }
  stockPickerActiveIndex = matches.length ? 0 : -1;
  renderStockPickerResults();
  stockPickerInput.focus();
}

document.getElementById('stockPickerSearch')?.addEventListener('click', submitStockPickerSearch);

stockPickerResults?.addEventListener('click', async event => {
  const option = event.target.closest('[data-symbol]');
  if (option) await selectStock(option.dataset.symbol);
});

document.getElementById('stockPickerClear')?.addEventListener('click', async () => {
  if (!stockPickerInput) return;
  stockPickerInput.value = '';
  stockPickerInput.focus();
  stockPickerActiveIndex = -1;
  await searchStockPicker('');
});

document.addEventListener('click', event => {
  if (!event.target.closest('#stockPicker')) closeStockPicker();
});

/* ── 물타기 시뮬레이션 모달 ─────────────────────────────────────────────── */
let avgDownOpen        = false;  // 모달 열림 여부
let avgDownSymbol      = null;   // 시뮬레이션 대상 종목
let avgDownPriceEdited = false;  // 사용자가 매수 단가를 직접 수정했는지 (실시간 시세 동기화 여부 판단)

/** 모달 계산의 기준값 — 현재 선택 종목의 보유 정보 · 시세 · 현금 */
function avgDownBase() {
  const symbol   = avgDownSymbol;
  const position = lastPositions.find(pos => pos.symbol === symbol);
  const stock    = allStocks.find(item => item.symbol === symbol);
  return {
    symbol,
    name:     position?.name ?? stock?.name ?? symbol ?? '-',
    holdQty:  Number(position?.quantity ?? 0),
    holdAvg:  Number(position?.avgPrice ?? 0),
    price:    Number(currentStockPrice || position?.currentPrice || 0),
    cash:     Number(lastCash || 0),
  };
}

function adMsg(msg = '', isErr = false) {
  const el = document.getElementById('adMsg');
  if (el) { el.textContent = msg; el.style.color = isErr ? '#E11D48' : '#2E7D32'; }
}

/** 모달에 입력된 매수 단가 (미입력 시 현재가로 대체) */
function avgDownBuyPrice() {
  const raw = Number(document.getElementById('adBuyPrice')?.value);
  return Number.isFinite(raw) && raw > 0 ? raw : avgDownBase().price;
}

/** 보유 현금으로 매수 가능한 최대 수량 */
function avgDownMaxQty() {
  const price = avgDownBuyPrice();
  const cash  = avgDownBase().cash;
  return price > 0 ? Math.floor(cash / price) : 0;
}

/** 수량을 0~최대치로 보정한 뒤 입력창·슬라이더에 반영하고 재계산 */
function setAvgDownQty(qty) {
  const max   = avgDownMaxQty();
  const input = document.getElementById('adBuyQty');
  const range = document.getElementById('adQtySlider');
  const value = Math.max(0, Math.min(max, Math.floor(Number(qty) || 0)));
  if (input) input.value = value || '';
  if (range) { range.max = max; range.value = value; }
  setText('adMaxQtyLabel', `최대 ${max.toLocaleString('ko-KR')}주`);
  recalcAvgDown();
}

/** 보유 상태 카드 갱신 */
function renderAvgDownBase() {
  const { name, symbol, holdQty, holdAvg, price, cash } = avgDownBase();
  const cost    = holdQty * holdAvg;
  const pnl     = holdQty > 0 ? holdQty * price - cost : 0;
  const pnlRate = cost > 0 ? (pnl / cost) * 100 : 0;

  setText('adStockName', symbol ? `${name} · ${symbol}` : '-');
  setText('adHoldQty',   `${holdQty.toLocaleString('ko-KR')}주`);
  setText('adHoldAvg',   holdQty > 0 ? fmtKrw(holdAvg) : '-');
  setText('adCurPrice',  price > 0 ? fmtKrw(price) : '-');
  setEl('adPnl',         holdQty > 0 ? (pnl >= 0 ? '+' : '') + fmtKrw(Math.round(pnl)) : '-', holdQty > 0 ? colorByVal(pnl) : 'var(--fg)');
  setEl('adPnlRate',     holdQty > 0 ? (pnlRate >= 0 ? '+' : '') + pnlRate.toFixed(2) + '%' : '-', holdQty > 0 ? colorByVal(pnlRate) : 'var(--fg)');
  setText('adCash',      fmtKrw(cash));

  // 물타기는 "평단보다 싼 가격에 추가 매수"할 때만 평균단가가 내려간다
  const badge = document.getElementById('adStatusBadge');
  if (badge) {
    if (holdQty <= 0) {
      badge.textContent = '미보유 · 신규 진입';
      badge.className = 'badge badge-muted';
    } else if (pnlRate < 0) {
      badge.textContent = `손실 구간 ${pnlRate.toFixed(2)}% · 물타기 유효`;
      badge.className = 'badge badge-red';
    } else {
      badge.textContent = `수익 구간 +${pnlRate.toFixed(2)}% · 추가 매수 시 평단 상승`;
      badge.className = 'badge badge-green';
    }
    badge.style.fontSize = '10px';
  }
}

/** 추가 매수 시 평균단가 · 수익률 변화 계산 */
function recalcAvgDown() {
  const { holdQty, holdAvg, price, cash } = avgDownBase();
  const buyPrice = avgDownBuyPrice();
  const buyQty   = Math.max(0, Math.floor(Number(document.getElementById('adBuyQty')?.value) || 0));
  const buyAmount = buyPrice * buyQty;

  const totalQty  = holdQty + buyQty;
  const totalCost = holdQty * holdAvg + buyAmount;
  // 백엔드가 평균단가를 정수로 내림 저장하므로(stock_trading.execute_order) 동일하게 맞춘다
  const newAvg    = totalQty > 0 ? Math.floor(totalCost / totalQty) : 0;

  const line = document.getElementById('adBuyAmountLine');
  if (line) {
    line.innerHTML = buyQty > 0
      ? `${fmtKrw(buyPrice)} × ${buyQty.toLocaleString('ko-KR')}주 = <strong style="color:var(--accent-dark);">${fmtKrw(buyAmount)}</strong> · 보유 현금 ${fmtKrw(cash)}`
      : '추가 매수 수량을 입력하면 평균단가 변화를 계산합니다.';
  }

  setText('adAvgBefore', holdQty > 0 ? fmtKrw(holdAvg) : '없음');
  setText('adNewAvg',    totalQty > 0 && buyQty > 0 ? fmtKrw(newAvg) : '-');
  setText('adTotalQty',  buyQty > 0 ? `${totalQty.toLocaleString('ko-KR')}주` : '-');
  setText('adTotalCost', buyQty > 0 ? fmtKrw(Math.round(totalCost)) : '-');

  // 평균단가 변동폭 — 내려가면 파랑(물타기 성공), 올라가면 빨강
  const deltaEl = document.getElementById('adAvgDelta');
  if (deltaEl) {
    if (buyQty > 0 && holdQty > 0 && holdAvg > 0) {
      const delta = newAvg - holdAvg;
      const rate  = (delta / holdAvg) * 100;
      deltaEl.textContent = `${delta >= 0 ? '+' : '−'}${fmtKrw(Math.abs(delta))} (${delta >= 0 ? '+' : ''}${rate.toFixed(2)}%)`;
      deltaEl.style.color = delta < 0 ? '#1565C0' : delta > 0 ? '#E11D48' : 'var(--fg)';
    } else if (buyQty > 0) {
      deltaEl.textContent = '신규 진입';
      deltaEl.style.color = 'var(--muted)';
    } else {
      deltaEl.textContent = '-';
      deltaEl.style.color = 'var(--fg)';
    }
  }

  // 현재가 기준 수익률 — 물타기 전 → 후
  const pnlEl = document.getElementById('adNewPnlRate');
  if (pnlEl) {
    if (buyQty > 0 && price > 0 && newAvg > 0) {
      const beforeRate = holdAvg > 0 ? ((price - holdAvg) / holdAvg) * 100 : NaN;
      const afterRate  = ((price - newAvg) / newAvg) * 100;
      pnlEl.innerHTML = Number.isFinite(beforeRate)
        ? `<span style="color:${colorByVal(beforeRate)};">${beforeRate >= 0 ? '+' : ''}${beforeRate.toFixed(2)}%</span>
           <span style="color:var(--muted);font-weight:600;"> → </span>
           <span style="color:${colorByVal(afterRate)};">${afterRate >= 0 ? '+' : ''}${afterRate.toFixed(2)}%</span>`
        : `<span style="color:${colorByVal(afterRate)};">${afterRate >= 0 ? '+' : ''}${afterRate.toFixed(2)}%</span>`;
    } else {
      pnlEl.textContent = '-';
    }
  }

  // 본전(평균단가) 회복까지 현재가 대비 필요한 상승률
  const beEl = document.getElementById('adBreakEven');
  if (beEl) {
    const need = (avg) => price > 0 ? ((avg - price) / price) * 100 : NaN;
    if (buyQty > 0 && price > 0 && newAvg > 0) {
      const beforeNeed = holdQty > 0 ? need(holdAvg) : NaN;
      const afterNeed  = need(newAvg);
      const label = (v) => v <= 0 ? '이미 달성' : `+${v.toFixed(2)}%`;
      beEl.innerHTML = Number.isFinite(beforeNeed)
        ? `<span style="color:var(--muted);font-weight:700;">${label(beforeNeed)}</span>
           <span style="color:var(--muted);font-weight:600;"> → </span>
           <span style="color:var(--accent-dark);">${label(afterNeed)}</span>`
        : `<span style="color:var(--accent-dark);">${label(afterNeed)}</span>`;
    } else if (holdQty > 0 && price > 0) {
      const beforeNeed = need(holdAvg);
      beEl.textContent = beforeNeed <= 0 ? '이미 달성' : `+${beforeNeed.toFixed(2)}%`;
      beEl.style.color = 'var(--fg)';
    } else {
      beEl.textContent = '-';
    }
  }

  // 잔여 현금 — 실제 체결은 현재가 기준이라 부족 여부는 현재가로 판단
  const restEl  = document.getElementById('adRestCash');
  const needCash = price * buyQty;
  if (restEl) {
    if (buyQty > 0) {
      const rest = cash - buyAmount;
      restEl.textContent = fmtKrw(Math.round(rest));
      restEl.style.color = rest < 0 ? '#E11D48' : 'var(--fg)';
    } else {
      restEl.textContent = fmtKrw(cash);
      restEl.style.color = 'var(--fg)';
    }
  }

  // 실제 매수 가능 여부
  const execBtn = document.getElementById('adExecBtn');
  if (execBtn) {
    const ok = buyQty > 0 && price > 0 && needCash <= cash;
    execBtn.disabled = !ok;
    execBtn.style.opacity = ok ? '1' : '.45';
    execBtn.style.cursor  = ok ? 'pointer' : 'not-allowed';
    execBtn.textContent   = buyQty > 0 ? `${buyQty.toLocaleString('ko-KR')}주 매수` : '이 수량으로 매수';
  }

  recalcAvgDownReverse();
}

/** 목표 평균단가 역산 — 필요한 추가 매수 수량·금액 */
function recalcAvgDownReverse() {
  const box = document.getElementById('adReverseResult');
  if (!box) return;
  const { holdQty, holdAvg, cash } = avgDownBase();
  const buyPrice  = avgDownBuyPrice();
  const targetAvg = Number(document.getElementById('adTargetAvg')?.value);

  if (!Number.isFinite(targetAvg) || targetAvg <= 0) {
    box.innerHTML = '목표 평균단가를 입력하면 위 매수 단가 기준으로 필요한 수량·금액을 계산합니다.';
    return;
  }
  if (holdQty <= 0 || holdAvg <= 0) {
    box.innerHTML = '<span style="color:#E11D48;">보유 중인 종목이 없어 역산할 수 없습니다. 목표 평균단가는 매수 단가와 같아집니다.</span>';
    return;
  }
  if (buyPrice <= 0) {
    box.innerHTML = '<span style="color:#E11D48;">매수 단가를 먼저 입력하세요.</span>';
    return;
  }
  if (targetAvg === buyPrice) {
    box.innerHTML = '<span style="color:#E11D48;">목표 평균단가가 매수 단가와 같아 계산할 수 없습니다.</span>';
    return;
  }

  // (holdQty·holdAvg + q·buyPrice) / (holdQty + q) = targetAvg  →  q = holdQty(holdAvg − targetAvg) / (targetAvg − buyPrice)
  const requiredQty = holdQty * (holdAvg - targetAvg) / (targetAvg - buyPrice);
  if (requiredQty <= 0) {
    box.innerHTML = '<span style="color:#E11D48;">해당 매수 단가로는 목표 평균단가에 도달할 수 없습니다. 목표가 현재 평균단가와 매수 단가 사이의 값인지 확인하세요.</span>';
    return;
  }

  const qty    = Math.ceil(requiredQty);
  const amount = qty * buyPrice;
  const short  = amount - cash;
  box.innerHTML = `
    <div class="ad-row"><span>필요한 추가 매수 수량</span><b>${qty.toLocaleString('ko-KR')}주</b></div>
    <div class="ad-row"><span>필요한 추가 매수 금액</span><b>${fmtKrw(amount)}</b></div>
    <div class="ad-row"><span>도달 시 총 보유 수량</span><b>${(holdQty + qty).toLocaleString('ko-KR')}주</b></div>
    ${short > 0
      ? `<div style="margin-top:6px;color:#E11D48;font-weight:700;">보유 현금이 ${fmtKrw(short)} 부족합니다.</div>`
      : `<div style="margin-top:6px;"><button type="button" class="ad-mini-btn" onclick="setAvgDownQty(${qty})">이 수량으로 시뮬레이션</button></div>`}
  `;
}

/** 시세·계좌 갱신 시 열려 있는 모달을 최신 값으로 다시 그린다 */
function refreshAvgDownLive() {
  if (!avgDownOpen) return;
  const priceInput = document.getElementById('adBuyPrice');
  // 사용자가 직접 고치지 않았고 입력 중이 아닐 때만 현재가를 따라간다
  if (priceInput && !avgDownPriceEdited && document.activeElement !== priceInput) {
    const price = avgDownBase().price;
    if (price > 0) priceInput.value = price;
  }
  renderAvgDownBase();
  setAvgDownQty(document.getElementById('adBuyQty')?.value ?? 0);
}

async function openAvgDownModal(symbol) {
  const target = symbol || document.getElementById('stockSymbol')?.value;
  if (!target) { showMsg('종목을 먼저 선택해주세요.', true); return; }
  if (symbol && symbol !== document.getElementById('stockSymbol')?.value) await selectStock(symbol);

  avgDownSymbol      = target;
  avgDownOpen        = true;
  avgDownPriceEdited = false;
  adMsg('');

  const { price } = avgDownBase();
  const priceInput = document.getElementById('adBuyPrice');
  if (priceInput) priceInput.value = price > 0 ? price : '';
  const targetInput = document.getElementById('adTargetAvg');
  if (targetInput) targetInput.value = '';

  document.getElementById('avgDownOverlay')?.classList.add('open');
  renderAvgDownBase();
  setAvgDownQty(0);
  document.getElementById('adBuyQty')?.focus();
}

function closeAvgDownModal() {
  avgDownOpen = false;
  document.getElementById('avgDownOverlay')?.classList.remove('open');
}

/** 시뮬레이션한 수량 그대로 실제 시장가 매수 주문 */
async function executeAvgDownBuy() {
  const { symbol, price, cash, holdQty, holdAvg } = avgDownBase();
  const qty = Math.max(0, Math.floor(Number(document.getElementById('adBuyQty')?.value) || 0));
  if (qty < 1)          { adMsg('매수 수량을 1주 이상 입력하세요.', true); return; }
  if (!(price > 0))     { adMsg('현재 시세를 불러온 뒤 주문할 수 있습니다.', true); return; }
  if (price * qty > cash) { adMsg('보유 현금이 부족합니다.', true); return; }

  const buyPrice = avgDownBuyPrice();
  const notice = buyPrice !== price
    ? `\n\n※ 시뮬레이션 단가(${fmtKrw(buyPrice)})와 무관하게 현재가 ${fmtKrw(price)}로 체결됩니다.`
    : '';
  const newAvg = Math.floor((holdQty * holdAvg + price * qty) / (holdQty + qty));
  if (!confirm(`${symbol} ${qty.toLocaleString('ko-KR')}주를 ${fmtKrw(price)}에 매수합니다.\n주문금액 ${fmtKrw(price * qty)}\n예상 평균단가 ${fmtKrw(newAvg)}${notice}`)) return;

  const btn = document.getElementById('adExecBtn');
  if (btn) btn.disabled = true;
  try {
    await requestJson('/api/stocks/orders/buy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, quantity: qty }),
    });
    showMsg(`물타기 매수 완료 (${qty.toLocaleString('ko-KR')}주)`);
    await Promise.all([loadAccount(), loadPositions(), loadQuote(symbol), loadHistory()]);
    adMsg(`${qty.toLocaleString('ko-KR')}주 매수 완료 — 평균단가가 갱신되었습니다.`);
    setAvgDownQty(0);
  } catch (e) {
    adMsg(e.message, true);
  } finally {
    if (btn) btn.disabled = false;
    recalcAvgDown();  // 잔여 현금 기준으로 매수 버튼 활성 상태 재판정
  }
}

document.getElementById('avgDownBtn')?.addEventListener('click', () => openAvgDownModal());
document.getElementById('adCloseBtn')?.addEventListener('click', closeAvgDownModal);
document.getElementById('adCancelBtn')?.addEventListener('click', closeAvgDownModal);
document.getElementById('adExecBtn')?.addEventListener('click', executeAvgDownBuy);
document.getElementById('avgDownOverlay')?.addEventListener('click', event => {
  if (event.target.id === 'avgDownOverlay') closeAvgDownModal();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && avgDownOpen) closeAvgDownModal();
});
document.getElementById('adBuyPrice')?.addEventListener('input', () => {
  avgDownPriceEdited = true;
  setAvgDownQty(document.getElementById('adBuyQty')?.value ?? 0);
});
document.getElementById('adBuyQty')?.addEventListener('input', event => setAvgDownQty(event.target.value));
document.getElementById('adQtySlider')?.addEventListener('input', event => setAvgDownQty(event.target.value));
document.getElementById('adTargetAvg')?.addEventListener('input', recalcAvgDownReverse);
document.getElementById('adPercentBtns')?.addEventListener('click', event => {
  const btn = event.target.closest('[data-ad-percent]');
  if (!btn) return;
  const price = avgDownBuyPrice();
  if (!(price > 0)) { adMsg('매수 단가를 먼저 입력하세요.', true); return; }
  setAvgDownQty(Math.floor(avgDownBase().cash * (Number(btn.dataset.adPercent) / 100) / price));
});

/* ── 유틸 ────────────────────────────────────────────────────────────────── */
function setText(id, val) { const el = document.getElementById(id); if (el) el.textContent = val; }
function setEl(id, val, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = val;
  if (color) el.style.color = color;
}

/* ── 부트 ────────────────────────────────────────────────────────────────── */
(async () => {
  await initPage();
  initStockChart();

  await loadStockList();

  const sym = document.getElementById('stockSymbol')?.value;
  await Promise.all([loadMarket(), loadQuote(sym), loadAccount(), loadPositions()]);
  await Promise.all([loadChart(sym, currentPeriod), loadHistory(), loadBatchPrices()]);

  // 실시간 갱신
  setInterval(() => loadBatchPrices(),  5_000);
  setInterval(() => {
    const s = document.getElementById('stockSymbol')?.value;
    if (s) loadQuote(s);
  }, 5_000);
  setInterval(() => {
    loadMarket();
    loadAccount();
    loadPositions();
  }, 15_000);
  setInterval(() => loadHistory(), 30_000);
})();
