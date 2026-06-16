/* ── TradeJournal frontend — clean rewrite ──────────────────── */
const C = {
  green:'#00e5a0', red:'#ff4757', gold:'#f0c040', amber:'#ffa726',
  blue:'#4fa3ff', muted:'#6b7c93', border:'#1e2d3d', text:'#d0d8e4',
  panel:'#131920', panel2:'#1a2130',
};
const charts = {};
const selectedTags = new Set();
let riskModeEl = 'pip';
let calMonth, calYear;
let allTrades = [];
let lastMetrics = {};
let _pendingImageFile = null;
let _pendingImageURL  = null;

const $ = id => document.getElementById(id);
const fmt  = (n,d=2) => n==null||isNaN(n)?'—':Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtR = n => n==null||isNaN(n)?'—':(n>=0?'+':'')+Number(n).toFixed(2)+'R';
const fmt$ = n => n==null||isNaN(n)?'—':(n<0?'-$':'$')+fmt(Math.abs(n));
const cls  = n => n>0?'pos':n<0?'neg':'';
const baseOpts = (extra={}) => ({
  responsive:true, maintainAspectRatio:false,
  plugins:{legend:{display:false}, tooltip:{...extra.tooltip}},
  scales:{
    x:{grid:{color:C.border},ticks:{color:C.muted,font:{size:10}}},
    y:{grid:{color:C.border},ticks:{color:C.muted,font:{size:10}}},
    ...(extra.scales||{})
  },
});

/* ── Tabs ────────────────────────────────────────────────────── */
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
    btn.classList.add('active');
    $('tab-'+btn.dataset.tab).classList.add('active');
  });
});

/* ── Engine status badge ─────────────────────────────────────── */
async function pollEngineStatus() {
  try {
    const d = await (await fetch('/api/engine_status')).json();
    const badge = $('aiBadge'), label = $('aiLabel');
    if (d.groq) {
      badge.className='ai-badge on'; label.textContent='Groq LLM';
    } else if (d.offline_ready) {
      badge.className='ai-badge on'; label.textContent='Neural (offline)';
    } else {
      badge.className='ai-badge'; label.textContent='Loading models…';
      setTimeout(pollEngineStatus, 4000);
    }
  } catch(e) {}
}

/* ── Master refresh ──────────────────────────────────────────── */
async function refresh() {
  const [tradesRes, metricsRes] = await Promise.all([
    fetch('/api/trades'), fetch('/api/metrics')
  ]);
  allTrades = await tradesRes.json();
  const m = await metricsRes.json();
  lastMetrics = m;

  renderProp(m.prop);
  renderKPIs(m.kpi, m.prop);
  renderEquity(m.kpi);
  renderWL(m.kpi);
  renderRRRGauge(m.kpi.rrr);
  renderOrderTypes(allTrades);
  renderRadar(m.kpi);
  renderWinLossDisplay(m.kpi);
  renderTradingDays(m.prop);
  renderCalendar(m.calendar);
  renderIntraday(m.intraday);
  renderDuration(m.duration);
  renderRDist(m.kpi.r_distribution);
  renderBreakdown('bySession',   m.kpi.by_session);
  renderBreakdown('byWeekday',   m.kpi.by_weekday);
  renderBreakdown('byInstrument',m.kpi.by_instrument);
  renderBreakdown('bySetup',     m.kpi.by_setup);
  renderEmotionChart(m.emotion);
  renderDisciplineChart(allTrades);
  renderPsychBlotter(allTrades);
  renderBlotter(allTrades);
}

/* ── Discipline strip ────────────────────────────────────────── */
function renderProp(p) {
  const chips = [
    { cls: p.target_progress>=100?'target':'ok', label: p.account_label,
      value: fmt$(p.total_pnl), note: `${p.target_progress}% of ${fmt$(p.profit_target)} target`,
      progress: Math.min(p.target_progress,100) },
    { cls: p.drawdown_ok?'ok':'breach', label: 'Max drawdown',
      value: fmt$(-p.worst_drawdown), note: `limit ${fmt$(p.max_drawdown_limit)} · ${p.max_loss_pct}% rule`,
      progress: p.max_drawdown_limit?Math.min((p.worst_drawdown/p.max_drawdown_limit)*100,100):0 },
    { cls: p.loss_breaches.length?'breach':'ok', label: 'Daily drawdown',
      value: p.loss_breaches.length?`${p.loss_breaches.length} breach`:'0.00%',
      note: `limit ${fmt$(p.daily_loss_limit)} · ${p.daily_drawdown_pct}% rule`, progress:0 },
    { cls: p.consistency_ok?'ok':'breach', label: 'Consistency',
      value: p.best_day_share+'%', note: `best day share · limit ${p.consistency_pct_limit}%`,
      progress: Math.min((p.best_day_share/p.consistency_pct_limit)*100,100) },
    { cls:'ok', label:'ROI', value: p.roi+'%', note:`balance ${fmt$(p.current_balance)}`,
      progress: Math.min(p.target_progress,100) },
  ];
  $('disciplineStrip').innerHTML = chips.map(c=>`
    <div class="rule-chip ${c.cls}">
      <span class="rc-label">${c.label}</span>
      <span class="rc-value">${c.value}</span>
      <span class="rc-note">${c.note}</span>
      ${c.progress?`<div class="progress-bar"><div class="pb-fill" style="width:${c.progress}%"></div></div>`:''}
    </div>`).join('');
}

/* ── KPI cards ───────────────────────────────────────────────── */
function renderKPIs(k, p) {
  const cards = [
    {label:'Net P&L',      value:fmt$(k.total_pnl),     sub:`ROI ${p.roi}%`, hero:true, cl:cls(k.total_pnl)},
    {label:'Win Rate',     value:k.win_rate+'%',         sub:`${k.wins}W · ${k.losses}L · ${k.breakeven}BE`},
    {label:'Profit Factor',value:k.profit_factor===9999?'∞':fmt(k.profit_factor), sub:'gross win ÷ loss'},
    {label:'Avg / Trade',  value:fmt$(k.avg_per_trade),  sub:'return per trade', cl:cls(k.avg_per_trade)},
    {label:'Expectancy',   value:fmtR(k.expectancy_r),   sub:fmt$(k.avg_per_trade)+' avg', cl:cls(k.expectancy_r)},
    {label:'RRR',          value:fmt(k.rrr),             sub:'risk-to-reward'},
    {label:'Max Drawdown', value:fmt$(-k.max_drawdown),  sub:'-'+fmt(k.max_drawdown_r)+'R', cl:'neg'},
    {label:'Sharpe',       value:fmt(k.sharpe),          sub:'Sortino '+fmt(k.sortino)},
    {label:'Streak',       value:(k.current_streak>0?'+':'')+k.current_streak,
     sub:`max ${k.longest_win_streak}W · ${k.longest_loss_streak}L`, cl:cls(k.current_streak)},
    {label:'Trades',       value:k.total_trades,         sub:`${p.trading_days||0} days`},
  ];
  $('kpiRow').innerHTML = cards.map(c=>`
    <div class="kpi ${c.hero?'hero':''}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-val num ${c.cl||''}">${c.value}</div>
      <div class="kpi-sub">${c.sub||''}</div>
    </div>`).join('');
}

/* ── Charts ──────────────────────────────────────────────────── */
function renderEquity(k) {
  charts.eq?.destroy();
  if (!k.equity_curve.length) return;
  charts.eq = new Chart($('equityChart'), {
    type:'line',
    data:{ labels:k.equity_curve.map((_,i)=>i+1),
      datasets:[{data:k.equity_curve, borderColor:C.green, borderWidth:2,
        fill:true, backgroundColor:'rgba(0,229,160,0.06)', pointRadius:0, tension:0.2}] },
    options:baseOpts({tooltip:{callbacks:{label:c=>fmt$(c.parsed.y)}}}),
  });
}

function renderWL(k) {
  charts.wl?.destroy();
  charts.wl = new Chart($('wlChart'), {
    type:'doughnut',
    data:{ labels:['Win','Loss'], datasets:[{data:[k.wins,k.losses],
      backgroundColor:[C.green,C.red], borderWidth:0}] },
    options:{responsive:true, maintainAspectRatio:false, cutout:'68%',
      plugins:{legend:{display:false}, tooltip:{callbacks:{
        label:c=>` ${c.label}: ${c.raw} (${fmt((c.raw/k.total_trades)*100,1)}%)`}}}},
  });
  $('wlSub').textContent = `Win ${k.win_rate}% · Loss ${(100-k.win_rate).toFixed(1)}%`;
  $('wlLegend').innerHTML = `
    <div class="legend-item"><span class="legend-dot" style="background:${C.green}"></span>Win: ${k.win_rate}%</div>
    <div class="legend-item"><span class="legend-dot" style="background:${C.red}"></span>Loss: ${(100-k.win_rate).toFixed(1)}%</div>`;
}

function renderRRRGauge(rrr) {
  charts.rrr?.destroy();
  const capped = Math.min(rrr||0, 15);
  const ratio  = capped/15;
  const r = ratio<0.33?C.red:ratio<0.66?C.amber:C.green;
  charts.rrr = new Chart($('rrrGauge'), {
    type:'doughnut',
    data:{datasets:[{data:[capped,15-capped], backgroundColor:[r,'#1e2d3d'],
      borderWidth:0, circumference:180, rotation:270}]},
    options:{responsive:true, maintainAspectRatio:false, cutout:'72%',
      plugins:{legend:{display:false}, tooltip:{enabled:false}}},
  });
  const tag = (rrr||0)>=5?'Excellent':(rrr||0)>=2?'Good':'Needs Work';
  $('rrrLabel').innerHTML = `<div class="gauge-big">${fmt(rrr)}</div><div class="gauge-tag">${tag}</div>`;
}

function renderOrderTypes(trades) {
  charts.order?.destroy();
  const cnt = {Market:0, Stop:0, Other:0};
  trades.forEach(t=>{
    const ot=(t.order_type||'').toUpperCase();
    if(ot==='MARKET') cnt.Market++; else if(ot.includes('STOP')) cnt.Stop++; else cnt.Other++;
  });
  const labels=Object.keys(cnt).filter(k=>cnt[k]>0);
  const vals=labels.map(k=>cnt[k]);
  const colors=[C.green,C.red,C.amber];
  charts.order = new Chart($('orderChart'), {
    type:'doughnut',
    data:{labels, datasets:[{data:vals, backgroundColor:colors.slice(0,labels.length), borderWidth:0}]},
    options:{responsive:true, maintainAspectRatio:false, cutout:'68%', plugins:{legend:{display:false}}},
  });
  const total=vals.reduce((a,b)=>a+b,0);
  $('orderLegend').innerHTML = labels.map((l,i)=>
    `<div class="legend-item"><span class="legend-dot" style="background:${colors[i]}"></span>${l}: ${total?Math.round(vals[i]/total*100):0}%</div>`
  ).join('');
}

function renderRadar(k) {
  charts.radar?.destroy();
  const wr=k.win_rate/100;
  const pfCapped=Math.min((k.profit_factor===9999?5:k.profit_factor)/5,1);
  const rrrCapped=Math.min((k.rrr||0)/10,1);
  const exCapped=Math.min(Math.max(((k.expectancy_r||0)+3)/6,0),1);
  const streak=Math.min((k.longest_win_streak||0)/10,1);
  charts.radar = new Chart($('radarChart'), {
    type:'radar',
    data:{labels:['Win Rate','Profit Factor','RRR','Expectancy','Streak'],
      datasets:[{data:[wr,pfCapped,rrrCapped,exCapped,streak].map(v=>+(v*100).toFixed(1)),
        borderColor:C.green, backgroundColor:'rgba(0,229,160,0.1)',
        borderWidth:2, pointBackgroundColor:C.green, pointRadius:3}]},
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{r:{beginAtZero:true, max:100, grid:{color:C.border},
        angleLines:{color:C.border}, ticks:{display:false},
        pointLabels:{color:C.muted,font:{size:10}}}}},
  });
  $('edgeFooter').innerHTML = `
    <div class="edge-stat"><div class="es-label">Profit Factor</div><div class="es-value gold">${fmt(k.profit_factor)}</div></div>
    <div class="edge-stat"><div class="es-label">Avg Win/Loss</div><div class="es-value gold">${fmt(k.rrr)}</div></div>`;
}

function renderWinLossDisplay(k) {
  $('winLossDisplay').innerHTML = `
    <div class="wl-item"><div class="wl-icon">↑ Average Win</div><div class="wl-value pos">${fmt$(k.avg_win)}</div></div>
    <div class="wl-item"><div class="wl-icon">↓ Average Loss</div><div class="wl-value neg">${fmt$(k.avg_loss)}</div></div>`;
  $('bestWorstDisplay').innerHTML = `
    <div class="wl-item"><div class="wl-icon">★ Best trade</div><div class="wl-value pos">${fmt$(k.best_trade)}</div></div>
    <div class="wl-item"><div class="wl-icon">✕ Worst trade</div><div class="wl-value neg">${fmt$(k.worst_trade)}</div></div>`;
}

function renderTradingDays(p) {
  const days = Object.entries(p.day_pnl||{}).reverse();
  if (!days.length) { $('tradingDaysTable').innerHTML='<div class="empty">No trades yet</div>'; return; }
  $('tradingDaysTable').innerHTML = `<table>
    <thead><tr><th>Date</th><th>Profit (USD)</th><th>Profit (%)</th></tr></thead>
    <tbody>${days.map(([d,pnl])=>`
      <tr><td>${d}</td>
      <td class="num ${cls(pnl)}">${fmt$(pnl)}</td>
      <td class="num ${cls(pnl)}">${((pnl/p.starting_balance)*100).toFixed(2)}%</td></tr>`
    ).join('')}</tbody></table>`;
}

/* ── Calendar ────────────────────────────────────────────────── */
const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
const DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const now = new Date();
calMonth=now.getMonth(); calYear=now.getFullYear();

function calPrev(){calMonth--;if(calMonth<0){calMonth=11;calYear--;}renderCalendar(lastMetrics.calendar||{});}
function calNext(){calMonth++;if(calMonth>11){calMonth=0;calYear++;}renderCalendar(lastMetrics.calendar||{});}

function renderCalendar(cal) {
  $('calTitle').textContent=`${MONTHS[calMonth]}, ${calYear}`;
  const first=new Date(calYear,calMonth,1);
  const last=new Date(calYear,calMonth+1,0);
  let startDow=(first.getDay()+6)%7;
  const cells=[];
  for(let i=0;i<startDow;i++) cells.push(null);
  for(let d=1;d<=last.getDate();d++) cells.push(d);
  const todayStr=now.toISOString().slice(0,10);
  let monthPnl=0, monthTrades=0;
  const html=DAYS.map(d=>`<div class="cal-dow">${d}</div>`).join('')
    +cells.map(d=>{
      if(!d) return '<div class="cal-day empty"></div>';
      const key=`${calYear}-${String(calMonth+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      const dd=cal[key];
      if(dd){monthPnl+=dd.pnl;monthTrades+=dd.trades;}
      return `<div class="cal-day ${dd?(dd.pnl>0?'win':'loss'):''} ${key===todayStr?'today':''}">
        <div class="cd-num">${d}</div>
        ${dd?`<div class="cd-pnl ${cls(dd.pnl)}">${fmt$(dd.pnl)}</div>
        <div class="cd-count">${dd.trades} Trade${dd.trades>1?'s':''}</div>`:''}
      </div>`;
    }).join('');
  $('calendarGrid').innerHTML=html;
  $('calSummary').innerHTML=`<span>PnL:</span><b class="${cls(monthPnl)}">${fmt$(monthPnl)}</b>
    <span style="margin-left:16px">Trades:</span><b>${monthTrades}</b>`;
}

/* ── Intraday ────────────────────────────────────────────────── */
function renderIntraday(id) {
  if(!id||!id.best_hour){$('intradayGrid').innerHTML='<div class="empty">No data</div>';return;}
  const fmtH=h=>`${String(h).padStart(2,'0')}:00`;
  $('intradayGrid').innerHTML=`
    <div class="iad"><div class="iad-label">Best Hour</div><div class="iad-main">${fmtH(id.best_hour.hour)}</div><div class="iad-sub pos">${fmt$(id.best_hour.pnl)}</div></div>
    <div class="iad"><div class="iad-label">Worst Hour</div><div class="iad-main">${fmtH(id.worst_hour.hour)}</div><div class="iad-sub neg">${fmt$(id.worst_hour.pnl)}</div></div>
    <div class="iad"><div class="iad-label">Busiest Hour</div><div class="iad-main">${fmtH(id.busiest_hour.hour)}</div><div class="iad-sub">${id.busiest_hour.trades} trades</div></div>
    <div class="iad"><div class="iad-label">Total Trades</div><div class="iad-main">${id.total_trades}</div></div>`;
  charts.hour?.destroy();
  const hd=id.hour_data;
  const hrs=Object.keys(hd).map(Number).sort((a,b)=>a-b);
  charts.hour=new Chart($('hourChart'),{
    type:'bar',
    data:{labels:hrs.map(h=>String(h).padStart(2,'0')+':00'),
      datasets:[{data:hrs.map(h=>hd[h].pnl),
        backgroundColor:hrs.map(h=>hd[h].pnl>=0?C.green:C.red),borderRadius:3}]},
    options:baseOpts({tooltip:{callbacks:{label:c=>fmt$(c.parsed.y)}}}),
  });
}

/* ── Duration ────────────────────────────────────────────────── */
function renderDuration(d) {
  if(!d||!d.most_profitable_bucket){$('durationGrid').innerHTML='<div class="empty">No data</div>';return;}
  $('durationGrid').innerHTML=`
    <div class="iad"><div class="iad-label">Most Profitable</div><div class="iad-main">${d.most_profitable_bucket}</div><div class="iad-sub pos">${fmt$(d.most_profitable_pnl)}</div></div>
    <div class="iad"><div class="iad-label">Worst Hour</div><div class="iad-main">${d.worst_hour!=null?String(d.worst_hour).padStart(2,'0')+':00':'—'}</div><div class="iad-sub neg">${fmt$(d.worst_hour_pnl)}</div></div>
    <div class="iad"><div class="iad-label">Best PnL</div><div class="iad-main pos">${fmt$(d.most_profitable_pnl)}</div></div>
    <div class="iad"><div class="iad-label">Highest Win Rate</div><div class="iad-main">${d.highest_win_rate_bucket}</div><div class="iad-sub">${d.highest_win_rate}%</div></div>
    <div class="iad"><div class="iad-label">Most Common</div><div class="iad-main">${d.most_common_bucket}</div><div class="iad-sub">${d.most_common_count} trades</div></div>
    <div class="iad"><div class="iad-label">Best Avg PnL</div><div class="iad-main">${d.best_avg_pnl_bucket}</div><div class="iad-sub pos">${fmt$(d.best_avg_pnl)}</div></div>`;
  charts.dur?.destroy();
  const bd=d.bucket_data||{};
  const keys=Object.keys(bd);
  charts.dur=new Chart($('durationChart'),{
    type:'bar',
    data:{labels:keys,datasets:[{data:keys.map(k=>bd[k].pnl),
      backgroundColor:keys.map(k=>bd[k].pnl>=0?C.green:C.red),borderRadius:3}]},
    options:baseOpts({tooltip:{callbacks:{label:c=>fmt$(c.parsed.y)+'  WR: '+bd[keys[c.dataIndex]].win_rate+'%'}}}),
  });
}

/* ── R distribution ──────────────────────────────────────────── */
function renderRDist(dist) {
  charts.rd?.destroy();
  if(!dist||!Object.keys(dist).length) return;
  const labels=Object.keys(dist), vals=Object.values(dist);
  const colors=labels.map(l=>l.includes('-')&&!l.startsWith('0')?C.red:C.green);
  charts.rd=new Chart($('rDistChart'),{type:'bar',
    data:{labels,datasets:[{data:vals,backgroundColor:colors,borderRadius:3}]},
    options:baseOpts()});
}

/* ── Breakdown tables ────────────────────────────────────────── */
function renderBreakdown(elId, data) {
  const rows=Object.entries(data||{}).filter(([k])=>k!=='(none)'||Object.keys(data).length===1);
  const el=$(elId); if(!el) return;
  if(!rows.length){el.innerHTML='<div class="empty">No data</div>';return;}
  el.innerHTML=`<table>
    <thead><tr><th>Name</th><th>Trades</th><th>Win%</th><th>Avg R</th><th>P&L</th></tr></thead>
    <tbody>${rows.map(([n,a])=>`
      <tr><td>${n}</td><td class="num">${a.trades}</td><td class="num">${a.win_rate}%</td>
      <td class="num ${cls(a.avg_r)}">${fmtR(a.avg_r)}</td>
      <td class="num ${cls(a.pnl)}">${fmt$(a.pnl)}</td></tr>`).join('')}
    </tbody></table>`;
}

/* ── Emotion chart ───────────────────────────────────────────── */
function renderEmotionChart(emotion) {
  charts.em?.destroy();
  const entries=Object.entries(emotion||{}).filter(([k])=>k!=='(untagged)').sort((a,b)=>a[1].avg_r-b[1].avg_r);
  if(!entries.length) return;
  const labels=entries.map(e=>e[0]), vals=entries.map(e=>e[1].avg_r), counts=entries.map(e=>e[1].trades);
  charts.em=new Chart($('emotionChart'),{
    type:'bar', indexAxis:'y',
    data:{labels,datasets:[{data:vals,backgroundColor:vals.map(v=>v>=0?C.green:C.red),borderRadius:3}]},
    options:{responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>`${fmtR(c.parsed.x)} avg · ${counts[c.dataIndex]} trades`}}},
      scales:{x:{grid:{color:C.border},ticks:{color:C.muted,callback:v=>v+'R'}},
        y:{grid:{display:false},ticks:{color:C.text,font:{size:12}}}}},
  });
}

/* ── Discipline trend ────────────────────────────────────────── */
function renderDisciplineChart(trades) {
  charts.disc?.destroy();
  const ws=trades.filter(t=>t.sentiment_score!=null&&t.sentiment_source==='groq');
  if(ws.length<2) return;
  const labels=ws.map(t=>t.trade_date), vals=ws.map(t=>t.sentiment_score);
  const rolling=vals.map((_,i)=>{
    const slice=vals.slice(Math.max(0,i-2),i+1);
    return +(slice.reduce((a,b)=>a+b,0)/slice.length).toFixed(3);
  });
  charts.disc=new Chart($('disciplineChart'),{
    type:'line',
    data:{labels,datasets:[
      {data:rolling,borderColor:C.gold,borderWidth:2,fill:false,pointRadius:4,
       pointBackgroundColor:vals.map(v=>v>=0?C.green:C.red),tension:0.3,label:'Score'},
      {data:vals,borderColor:C.muted,borderWidth:1,fill:false,pointRadius:0,tension:0.3,label:'Raw'},
    ]},
    options:baseOpts({scales:{y:{min:-1,max:1,grid:{color:C.border},
      ticks:{color:C.muted,callback:v=>v>0?'+'+v:v}}},
      tooltip:{callbacks:{label:c=>c.dataset.label+': '+c.parsed.y}}}),
  });
}

/* ── Psychology blotter ──────────────────────────────────────── */
function renderPsychBlotter(trades) {
  const ws=trades.filter(t=>t.sentiment_source==='groq'||t.notes).reverse().slice(0,30);
  if(!ws.length){$('psychBlotter').innerHTML='<div class="empty">Log trades with notes to see psychology reads</div>';return;}
  $('psychBlotter').innerHTML=ws.map(t=>{
    const sc=t.sentiment_score??0;
    const scCls=sc>=0.2?'pos':sc<=-0.2?'neg':'neu';
    const phrases=(t.sentiment_phrases||[]).map(p=>
      `<span class="phrase-item"><span>${p.phrase}</span><span class="phrase-emotion">${p.emotion}</span></span>`).join('');
    const emotions=(t.emotions||[]).map(e=>`<span class="emotion-chip">${e}</span>`).join('');
    return `<div class="psych-entry">
      <div class="psych-header">
        <span class="psych-meta">${t.trade_date} · ${t.instrument} ${t.direction} · ${fmtR(t.realized_r)}</span>
        ${t.sentiment_label?`<span class="psych-score ${scCls}">${t.sentiment_label} (${sc>0?'+':''}${sc})</span>`:''}
      </div>
      ${emotions?`<div class="psych-emotions">${emotions}</div>`:''}
      ${t.sentiment_summary?`<div class="psych-summary">${t.sentiment_summary}</div>`:''}
      ${phrases?`<div class="psych-phrases">${phrases}</div>`:''}
      ${t.notes?`<div class="psych-notes-text">"${t.notes}"</div>`:''}
      ${t.sentiment_source!=='groq'&&t.notes?`<button class="retry-btn" onclick="retrySentiment(${t.id})">↻ Analyze</button>`:''}
    </div>`;
  }).join('');
}

/* ── Blotter ─────────────────────────────────────────────────── */
function renderBlotter(trades) {
  if(!trades.length){
    $('blotter').innerHTML='<tbody><tr><td><div class="empty">No trades. Log one or import a file.</div></td></tr></tbody>';
    return;
  }
  $('blotter').innerHTML=`
    <thead><tr>
      <th>Date</th><th>Chart</th><th>Symbol</th><th>Dir</th><th>Lots</th>
      <th>Entry</th><th>Exit</th><th>RR</th><th>P&L</th><th>R</th>
      <th>Status</th><th>Sentiment</th><th></th>
    </tr></thead>
    <tbody>${[...trades].reverse().map(t=>{
      const sc=t.sentiment_score??0;
      const dc=sc>0.2?C.green:sc<-0.2?C.red:C.muted;
      const isOpen=t.realized_pnl==null&&!t.exit_price;
      const statusBadge=isOpen?`<span class="open-trade-badge">● OPEN</span>`:`<span style="color:var(--muted);font-size:11px">Closed</span>`;
      const chartThumb=t.image_url
        ?`<img class="chart-thumb" src="${t.image_url}" alt="chart" onclick="event.stopPropagation();openLightbox('${t.image_url}')">`
        :`<span style="color:var(--faint);font-size:11px">—</span>`;
      return `<tr style="cursor:pointer" onclick="editTrade(${t.id})">
        <td class="num">${t.trade_date||''}</td>
        <td>${chartThumb}</td>
        <td><b>${t.instrument||''}</b></td>
        <td class="dir-${(t.direction||'').toLowerCase()}">${t.direction||''}</td>
        <td class="num">${t.lots??t.contracts??'—'}</td>
        <td class="num">${t.entry_price??'—'}</td>
        <td class="num">${t.exit_price??'—'}</td>
        <td class="num">${t.planned_rr?fmt(t.planned_rr):'—'}</td>
        <td class="num ${cls(t.realized_pnl)}">${t.realized_pnl!=null?fmt$(t.realized_pnl):'—'}</td>
        <td class="num ${cls(t.realized_r)}">${t.realized_r!=null?fmtR(t.realized_r):'—'}</td>
        <td>${statusBadge}</td>
        <td><span class="sent-dot" style="background:${dc}"></span>${t.sentiment_label||'—'}</td>
        <td onclick="event.stopPropagation()"><button class="del-btn" onclick="deleteTrade(${t.id})">✕</button></td>
      </tr>`;
    }).join('')}</tbody>`;
}

function filterBlotter() {
  const q=$('blotterSearch').value.toLowerCase();
  const dir=$('blotterDir').value;
  renderBlotter(allTrades.filter(t=>
    (!q||(t.instrument||'').toLowerCase().includes(q)||(t.notes||'').toLowerCase().includes(q))&&
    (!dir||t.direction===dir)));
}

/* ── Lightbox ────────────────────────────────────────────────── */
function openLightbox(url) {
  let lb=$('lightbox');
  if(!lb){
    lb=document.createElement('div');
    lb.id='lightbox';lb.className='lightbox';
    lb.innerHTML='<img id="lightboxImg">';
    lb.onclick=()=>lb.classList.remove('open');
    document.body.appendChild(lb);
  }
  $('lightboxImg').src=url;
  lb.classList.add('open');
}

/* ── Time picker ─────────────────────────────────────────────── */
function initTimePickers() {
  ['entry','exit'].forEach(side=>{
    const hSel=$(`tp_${side}_h`), mSel=$(`tp_${side}_m`);
    if(!hSel||!mSel) return;
    hSel.innerHTML='<option value="">HH</option>'+
      Array.from({length:24},(_,i)=>`<option value="${String(i).padStart(2,'0')}">${String(i).padStart(2,'0')}</option>`).join('');
    mSel.innerHTML='<option value="">MM</option>'+
      Array.from({length:60},(_,i)=>`<option value="${String(i).padStart(2,'0')}">${String(i).padStart(2,'0')}</option>`).join('');
  });
}

function syncTime(side) {
  const h=$(`tp_${side}_h`).value, m=$(`tp_${side}_m`).value;
  $(`f_${side}_time`).value=(h&&m)?`${h}:${m}`:'';
}

function setTimePicker(side, timeStr) {
  if(!timeStr) return;
  const parts=timeStr.split(':');
  if(parts.length>=2){
    $(`tp_${side}_h`).value=parts[0].padStart(2,'0');
    $(`tp_${side}_m`).value=parts[1].padStart(2,'0');
    syncTime(side);
  }
}

function resetTimePickers() {
  ['entry','exit'].forEach(side=>{
    $(`tp_${side}_h`).value='';
    $(`tp_${side}_m`).value='';
    $(`f_${side}_time`).value='';
  });
  const n=new Date();
  $('tp_entry_h').value=String(n.getHours()).padStart(2,'0');
  $('tp_entry_m').value=String(n.getMinutes()).padStart(2,'0');
  syncTime('entry');
}

/* ── Open trade toggle ───────────────────────────────────────── */
function toggleOpenTrade() {
  const open=$('f_open_trade').checked;
  $('exit_fields').style.opacity=open?'0.3':'1';
  $('exit_fields').style.pointerEvents=open?'none':'auto';
  $('exit_time_field').style.opacity=open?'0.3':'1';
  $('exit_time_field').style.pointerEvents=open?'none':'auto';
  if(open){$('f_exit').value='';$('f_pnl').value='';$('f_exit_time').value='';recalc();}
}

/* ── Image upload ────────────────────────────────────────────── */
function handleImageSelect(input) {
  const file=input.files[0];
  if(file) _setImagePreview(file);
}
function handleImageDrop(e) {
  e.preventDefault();
  $('imageUploadZone').classList.remove('drag-over');
  const file=e.dataTransfer.files[0];
  if(file&&file.type.startsWith('image/')) _setImagePreview(file);
}
function _setImagePreview(file) {
  _pendingImageFile=file;
  if(_pendingImageURL) URL.revokeObjectURL(_pendingImageURL);
  _pendingImageURL=URL.createObjectURL(file);
  $('imagePreview').src=_pendingImageURL;
  $('imagePreviewWrap').classList.remove('hidden');
  $('imageUploadPrompt').classList.add('hidden');
}
function _loadExistingImage(imageUrl) {
  if(imageUrl){
    $('imagePreview').src=imageUrl;
    $('imagePreviewWrap').classList.remove('hidden');
    $('imageUploadPrompt').classList.add('hidden');
  } else {
    _resetImageUpload();
  }
}
function _resetImageUpload() {
  _pendingImageFile=null;
  if(_pendingImageURL){URL.revokeObjectURL(_pendingImageURL);_pendingImageURL=null;}
  $('imagePreview').src='';
  $('imagePreviewWrap').classList.add('hidden');
  $('imageUploadPrompt').classList.remove('hidden');
  $('f_image').value='';
}
function removeImage() {
  const editId=$('f_edit_id').value;
  if(editId) fetch(`/api/trades/${editId}/image`,{method:'DELETE'}).catch(()=>{});
  _resetImageUpload();
}
async function _uploadPendingImage(tradeId) {
  if(!_pendingImageFile) return null;
  const fd=new FormData();
  fd.append('image',_pendingImageFile);
  try {
    const r=await fetch(`/api/trades/${tradeId}/image`,{method:'POST',body:fd});
    const d=await r.json();
    _pendingImageFile=null;
    if(_pendingImageURL){URL.revokeObjectURL(_pendingImageURL);_pendingImageURL=null;}
    return d.image_url||null;
  } catch(e){
    toast('Image upload error: '+e.message);
    return null;
  }
}

/* ── Live risk calculator ────────────────────────────────────── */
const INSTRUMENT_PIP = window.APP.instrumentPip;
const CONTRACT_SIZE = {
  XAUUSD:100, XAGUSD:100,
  EURUSD:100000,GBPUSD:100000,AUDUSD:100000,NZDUSD:100000,
  USDCAD:100000,USDCHF:100000,USDJPY:100000,
  EURGBP:100000,EURJPY:100000,GBPJPY:100000,
  BTCUSD:1,ETHUSD:1,US30:1,US500:1,NAS100:1,UK100:1,GER40:1,
};
const PIP_SIZE_JS = {XAUUSD:0.01,XAGUSD:0.01,USDJPY:0.01,EURJPY:0.01,GBPJPY:0.01};
function getCS(i){return CONTRACT_SIZE[i]||100000;}
function getPS(i){return PIP_SIZE_JS[i]||0.0001;}

function recalc() {
  const inst=($('f_instrument').value||'').toUpperCase();
  const cs=getCS(inst);
  const ps=getPS(inst);
  const lots=parseFloat($('f_lots').value)||0;
  const entry=parseFloat($('f_entry').value);
  const stop=parseFloat($('f_stop').value);
  const target=parseFloat($('f_target').value);
  const exit=parseFloat($('f_exit').value);
  const dir=$('f_direction').value;
  const pnlOverride=parseFloat($('f_pnl').value);
  const winLoss=document.querySelector('input[name="win_loss"]:checked')?.value||'auto';

  let risk=null;
  if(riskModeEl==='pip'){
    const sp=parseFloat($('f_stop_pips').value);
    if(sp&&lots) risk=sp*ps*cs*lots;
  } else if(riskModeEl==='dollar'){
    risk=parseFloat($('f_dollar_risk').value)||null;
  } else if(!isNaN(entry)&&!isNaN(stop)&&lots){
    risk=Math.abs(entry-stop)*cs*lots;
  }
  let plannedRR=null;
  if(!isNaN(entry)&&!isNaN(stop)&&!isNaN(target)&&entry!==stop)
    plannedRR=Math.abs(target-entry)/Math.abs(entry-stop);
  let pnl=null;
  if(!isNaN(pnlOverride)){pnl=pnlOverride;if(winLoss==='win'&&pnl<0)pnl=Math.abs(pnl);if(winLoss==='loss'&&pnl>0)pnl=-Math.abs(pnl);}
  else if(!isNaN(exit)&&!isNaN(entry)&&lots){
    const sign=dir==='Long'?1:-1;
    pnl=(exit-entry)*sign*cs*lots;
  }
  const realR=(pnl!=null&&risk)?pnl/risk:null;
  $('crRisk').textContent=risk?fmt$(risk):'—';
  $('crRR').textContent=plannedRR?fmt(plannedRR)+' : 1':'—';
  $('crPnL').textContent=pnl!=null?fmt$(pnl):'—';
  $('crPnL').className=pnl!=null?cls(pnl):'';
  $('crR').textContent=realR!=null?fmtR(realR):'—';
  $('crR').className=realR!=null?cls(realR):'';
}
['f_instrument','f_lots','f_entry','f_stop','f_target','f_exit',
 'f_stop_pips','f_target_pips','f_dollar_risk','f_direction','f_pnl']
  .forEach(id=>{const el=$(id);if(el)el.addEventListener('input',recalc);});

/* ── Setup tags ──────────────────────────────────────────────── */
document.querySelectorAll('#setupTags .toggle').forEach(el=>{
  el.addEventListener('click',()=>{
    const tag=el.dataset.tag;
    el.classList.toggle('on');
    selectedTags.has(tag)?selectedTags.delete(tag):selectedTags.add(tag);
  });
});

/* ── Risk mode ───────────────────────────────────────────────── */
function setRiskMode(mode,btn) {
  riskModeEl=mode;
  document.querySelectorAll('.risk-tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  $('riskPip').classList.toggle('hidden',mode!=='pip');
  $('riskDollar').classList.toggle('hidden',mode!=='dollar');
  $('riskPrice').classList.toggle('hidden',mode!=='price');
  recalc();
}

/* ── Custom setup tags ──────────────────────────────────────── */
function addCustomTag() {
  const input = $('f_custom_tag');
  const val = input.value.trim();
  if (!val) return;
  if (selectedTags.has(val)) { input.value=''; return; }
  selectedTags.add(val);
  const el = document.createElement('span');
  el.className = 'toggle on';
  el.dataset.tag = val;
  el.textContent = val;
  el.addEventListener('click', () => {
    el.classList.toggle('on');
    selectedTags.has(val) ? selectedTags.delete(val) : selectedTags.add(val);
  });
  $('setupTags').appendChild(el);
  input.value = '';
}

/* ── Sentiment preview ───────────────────────────────────────── */
async function previewSentiment() {
  const notes=$('f_notes').value;
  if(!notes.trim()){toast('Add notes first');return;}
  $('sentPreview').textContent='Analyzing with Groq LLM…';
  try {
    const r=await fetch('/api/sentiment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:notes})});
    const s=await r.json();
    $('sentPreview').innerHTML=s.source==='error'?`<span style="color:var(--red)">${s.summary}</span>`:
      `<b style="color:var(--${s.score>=0?'green':'red'})">${s.label}</b> (${s.score>0?'+':''}${s.score})<br>
       <span style="color:var(--muted)">${s.summary||''}${s.emotions?.length?' · '+s.emotions.join(', '):''}</span>`;
  } catch(e){$('sentPreview').textContent='Error: '+e.message;}
}

/* ── Save new trade ──────────────────────────────────────────── */
async function saveTrade() {
  const editId=$('f_edit_id').value;
  if(editId){ await updateTrade(parseInt(editId)); return; }

  if(!$('f_date').value){toast('Pick a date first');return;}
  if(!$('f_instrument').value){toast('Enter an instrument');return;}
  const isOpen=$('f_open_trade').checked;
  const payload={
    trade_date:$('f_date').value, entry_time:$('f_entry_time').value,
    exit_time:isOpen?null:$('f_exit_time').value, session:$('f_session').value,
    instrument:($('f_instrument').value||'').toUpperCase(), direction:$('f_direction').value,
    lots:$('f_lots').value, entry_price:$('f_entry').value,
    stop_price:$('f_stop').value, target_price:$('f_target').value,
    exit_price:isOpen?null:$('f_exit').value,
    realized_pnl:isOpen?null:$('f_pnl').value,
    commission:$('f_commission').value,
    stop_pips:riskModeEl==='pip'?$('f_stop_pips').value:null,
    target_pips:riskModeEl==='pip'?$('f_target_pips').value:null,
    dollar_risk:riskModeEl==='dollar'?$('f_dollar_risk').value:null,
    setups:[...selectedTags], notes:$('f_notes').value, emotions:[],
  };
  $('saveBtn').disabled=true; $('saveBtn').textContent='Saving…';
  try {
    const r=await fetch('/api/trades',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(_pendingImageFile) await _uploadPendingImage(d.id);
    const s=d.sentiment;
    toast(`Logged · ${s.source==='groq'?'Groq: '+s.label:'saved'}`,3000);
    clearEntryForm(); closeEntry(); await refresh();
  } catch(e){toast('Save failed: '+e.message);}
  finally{$('saveBtn').disabled=false;$('saveBtn').textContent='Save';}
}

/* ── Update existing trade ───────────────────────────────────── */
async function updateTrade(id) {
  const isOpen=$('f_open_trade').checked;
  const payload={
    trade_date:$('f_date').value, entry_time:$('f_entry_time').value,
    exit_time:isOpen?null:$('f_exit_time').value, session:$('f_session').value,
    instrument:($('f_instrument').value||'').toUpperCase(), direction:$('f_direction').value,
    lots:$('f_lots').value, entry_price:$('f_entry').value,
    stop_price:$('f_stop').value, target_price:$('f_target').value,
    exit_price:isOpen?null:$('f_exit').value,
    realized_pnl:isOpen?null:$('f_pnl').value,
    commission:$('f_commission').value,
    stop_pips:riskModeEl==='pip'?$('f_stop_pips').value:null,
    target_pips:riskModeEl==='pip'?$('f_target_pips').value:null,
    dollar_risk:riskModeEl==='dollar'?$('f_dollar_risk').value:null,
    setups:[...selectedTags], notes:$('f_notes').value, emotions:[],
  };
  $('saveBtn').disabled=true; $('saveBtn').textContent='Updating…';
  try {
    if(_pendingImageFile) await _uploadPendingImage(id);
    const r=await fetch(`/api/trades/${id}`,{method:'PUT',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!r.ok) throw new Error(await r.text());
    const d=await r.json();
    toast(`Updated · ${d.sentiment?.source==='groq'?'Groq re-analyzed':'saved'}`,3000);
    clearEntryForm(); closeEntry(); await refresh();
  } catch(e){toast('Update failed: '+e.message);}
  finally{$('saveBtn').disabled=false;$('saveBtn').textContent='Save';}
}

/* ── Edit trade (open modal pre-filled) ─────────────────────── */
function editTrade(id) {
  const t=allTrades.find(x=>x.id===id);
  if(!t) return;
  $('f_edit_id').value=id;
  $('entryModalTitle').textContent='Edit Trade';
  $('saveBtn').textContent='Update trade';
  $('f_date').value=t.trade_date||'';
  $('f_instrument').value=t.instrument||'';
  $('f_direction').value=t.direction||'Long';
  $('f_session').value=t.session||'London';
  $('f_lots').value=t.lots||'';
  $('f_entry').value=t.entry_price||'';
  $('f_stop').value=t.stop_price||'';
  $('f_target').value=t.target_price||'';
  $('f_exit').value=t.exit_price||'';
  $('f_pnl').value=t.realized_pnl!=null?t.realized_pnl:'';
  $('f_commission').value=t.commission||'0';
  $('f_notes').value=t.notes||'';
  setTimePicker('entry',t.entry_time);
  setTimePicker('exit',t.exit_time);
  const isOpen=t.realized_pnl==null&&!t.exit_price;
  $('f_open_trade').checked=isOpen;
  toggleOpenTrade();
  selectedTags.clear();
  document.querySelectorAll('#setupTags .toggle').forEach(el=>{
    const on=(t.setups||[]).includes(el.dataset.tag);
    el.classList.toggle('on',on);
    if(on) selectedTags.add(el.dataset.tag);
  });
  const sp=$('sentPreview');
  if(t.sentiment_label&&t.sentiment_source==='groq'){
    const sc=t.sentiment_score??0;
    sp.innerHTML=`<b style="color:var(--${sc>=0?'green':'red'})">${t.sentiment_label}</b> (${sc>0?'+':''}${sc})<br>
      <span style="color:var(--muted)">${t.sentiment_summary||''}</span>`;
  } else {
    sp.textContent=t.notes?'Click "Preview psychology read" to re-analyze.':'';
  }
  _loadExistingImage(t.image_url||null);
  recalc();
  $('entryModal').classList.add('open');
}

/* ── Delete ──────────────────────────────────────────────────── */
async function deleteTrade(id) {
  if(!confirm('Delete this trade?')) return;
  await fetch('/api/trades/'+id,{method:'DELETE'});
  await refresh();
}

async function retrySentiment(id) {
  toast('Re-analyzing…');
  await fetch('/api/trades/'+id+'/sentiment',{method:'POST'});
  await refresh();
  toast('Sentiment updated');
}

/* ── Clear form ──────────────────────────────────────────────── */
function clearEntryForm() {
  $('f_edit_id').value='';
  $('entryModalTitle').textContent='Log a Trade';
  $('saveBtn').textContent='Save';
  $('f_open_trade').checked=false;
  toggleOpenTrade();
  ['f_entry','f_stop','f_target','f_exit','f_pnl','f_lots',
   'f_stop_pips','f_target_pips','f_dollar_risk','f_notes','f_commission']
    .forEach(id=>{const el=$(id);if(el)el.value='';});
  selectedTags.clear();
  document.querySelectorAll('#setupTags .toggle.on').forEach(e=>e.classList.remove('on'));
  _resetImageUpload();
  $('sentPreview').textContent='';
  resetTimePickers();
  recalc();
}

/* ── Modal open/close ────────────────────────────────────────── */
function openEntry() {
  clearEntryForm();
  $('f_date').value=new Date().toISOString().slice(0,10);
  resetTimePickers();
  recalc();
  $('entryModal').classList.add('open');
}
function closeEntry()   { $('entryModal').classList.remove('open'); }
function openImport()   { $('importStatus').innerHTML=''; $('importModal').classList.add('open'); }
function closeImport()  { $('importModal').classList.remove('open'); }
function openSettings() {
  fetch('/api/settings').then(r=>r.json()).then(s=>{
    const LABELS={
      account_label:'Account label', starting_balance:'Starting balance $',
      profit_target:'Profit target $', daily_loss_limit:'Daily loss limit $',
      max_drawdown:'Max overall drawdown $', max_contracts:'Max contracts / lot size',
      consistency_pct:'Consistency limit (% of profit)',
    };
    $('settingsFields').innerHTML='<div style="padding:16px 20px">'
      +Object.entries(LABELS).map(([k,label])=>`
        <div class="field"><label>${label}</label>
        <input id="set_${k}" class="${k==='account_label'?'':'num'}" value="${s[k]??''}">
        </div>`).join('')+'</div>';
    $('settingsModal').classList.add('open');
  });
}
function closeSettings() { $('settingsModal').classList.remove('open'); }
async function saveSettings() {
  const KEYS=['account_label','starting_balance','profit_target','daily_loss_limit',
               'max_drawdown','max_contracts','consistency_pct'];
  const body={};
  KEYS.forEach(k=>{
    const el=$('set_'+k); if(el) body[k]=k==='account_label'?el.value:parseFloat(el.value);
  });
  await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  closeSettings(); toast('Rules saved'); await refresh();
}

['entryModal','importModal','settingsModal'].forEach(id=>{
  $(id).addEventListener('click',e=>{if(e.target===e.currentTarget)e.currentTarget.classList.remove('open');});
});

/* ── File import ─────────────────────────────────────────────── */
async function handleImport(input) {
  const file=input.files[0]; if(!file) return;
  $('importStatus').innerHTML=`<div style="color:var(--muted)">Uploading ${file.name}…</div>`;
  const fd=new FormData(); fd.append('file',file);
  try {
    const r=await fetch('/api/import',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error){$('importStatus').innerHTML=`<div style="color:var(--red)">${d.error}</div>`;return;}
    $('importStatus').innerHTML=`
      <div style="color:var(--green);margin-top:8px">
        ✓ Imported <b>${d.imported}</b> trades · Format: <b>${d.format}</b>
        ${d.skipped?` · Skipped: ${d.skipped}`:''}
        ${d.errors?.length?`<br><span style="color:var(--amber)">Warnings: ${d.errors.join('; ')}</span>`:''}
      </div>`;
    await refresh();
    setTimeout(closeImport,2000);
  } catch(e){$('importStatus').innerHTML=`<div style="color:var(--red)">${e.message}</div>`;}
}

/* ── Toast ───────────────────────────────────────────────────── */
let toastT;
function toast(msg,dur=2500) {
  const el=$('toast'); el.textContent=msg; el.classList.add('show');
  clearTimeout(toastT); toastT=setTimeout(()=>el.classList.remove('show'),dur);
}

/* ── Drag-over highlight ─────────────────────────────────────── */
document.addEventListener('DOMContentLoaded',()=>{
  const zone=$('imageUploadZone');
  if(zone){
    zone.addEventListener('dragover',()=>zone.classList.add('drag-over'));
    zone.addEventListener('dragleave',()=>zone.classList.remove('drag-over'));
  }
});

/* ── Init ────────────────────────────────────────────────────── */
initTimePickers();
resetTimePickers();
pollEngineStatus();
refresh();
