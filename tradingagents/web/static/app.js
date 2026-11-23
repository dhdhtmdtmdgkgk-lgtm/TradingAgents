"use strict";
const $ = id => document.getElementById(id);
let config, currentJob = null, mainReports = {}, historyReports = {}, selectedHistory = null;
let pollBusy = false, historyTimer, lastFinal = null, modalReport = null, rawMode = false, chartTicker = null;
const statusNames = {waiting:"대기 중", running:"실행 중", completed:"완료", failed:"실패"};
const icons = {waiting:"○", running:"◉", completed:"✓", failed:"!"};
function error(message) { $("global-error").hidden = !message; $("global-error").textContent = message || ""; }
async function api(path, options = {}) {
  const response = await fetch(path, {cache:"no-store", ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "서버 요청에 실패했습니다.");
  return data;
}
function reportButton(key, report, source) {
  const button = document.createElement("button"); button.type = "button"; button.textContent = report.label;
  button.addEventListener("click", () => openReport(key, source)); return button;
}
function openReport(key, source = "main") {
  const report = (source === "history" ? historyReports : mainReports)[key]; if (!report) return;
  modalReport = report; rawMode = false; $("modal-title").textContent = report.label; drawModal();
  $("report-modal").showModal(); $("modal-content").scrollTop = 0;
}
function drawModal() {
  $("raw-toggle").textContent = rawMode ? "보고서 보기" : "Markdown 원문";
  $("modal-content").classList.toggle("raw-markdown", rawMode);
  if (rawMode) $("modal-content").textContent = modalReport.markdown;
  else $("modal-content").innerHTML = modalReport.html;
  safeLinks($("modal-content"));
}
function safeLinks(element) {
  element.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
}
function displayFinal(markdown, html, rating) {
  if (markdown !== lastFinal && markdown) { $("final-report").innerHTML = html; safeLinks($("final-report")); lastFinal = markdown; }
  $("final-rating").hidden = !rating; $("final-rating").textContent = rating || "";
}
function drawStages(job) {
  mainReports = job?.reports || {};
  const stages = job?.stages || {};
  let completed = 0;
  for (const stage of config.stages) {
    const row = document.querySelector(`[data-stage="${stage.id}"]`);
    const status = stages[stage.id] || "waiting"; if (status === "completed") completed++;
    row.className = `agent-row stage-${status}` + (mainReports[stage.id] ? " can-read" : "");
    row.disabled = !mainReports[stage.id];
    row.querySelector(".stage-icon").textContent = icons[status];
    row.querySelector(".state-label").textContent = statusNames[status];
    row.querySelector(".read-label").hidden = !mainReports[stage.id];
  }
  $("agent-counter").textContent = `${completed} / ${config.stages.length}`;
  $("progress-fill").style.width = `${completed / config.stages.length * 100}%`;
  document.querySelectorAll(".debate-actions button").forEach(b => { b.disabled = !mainReports[b.dataset.report]; });
}
function chart(symbol) {
  symbol = symbol.trim().toUpperCase(); if (!symbol || symbol === chartTicker) return;
  chartTicker = symbol; $("chart-symbol").textContent = symbol;
  const container = document.createElement("div"); container.className = "tradingview-widget-container";
  // Isolate TradingView in its own origin: no third-party script reads the form or API.
  const widget = document.createElement("iframe"); widget.className = "tradingview-widget-container__widget";
  widget.title = `${symbol} TradingView 차트`; widget.style.border = "0";
  widget.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups"); widget.referrerPolicy = "no-referrer";
  const url = new URL("https://www.tradingview-widget.com/embed-widget/advanced-chart/"); url.searchParams.set("locale", "kr");
  url.hash = encodeURIComponent(JSON.stringify({autosize:true, symbol, interval:"D", timezone:"Asia/Seoul", theme:"light", style:"1", locale:"kr", allow_symbol_change:false, hide_side_toolbar:true, calendar:false, support_host:"https://www.tradingview.com"}));
  widget.src = url.toString(); container.append(widget);
  const attribution = document.createElement("div"); attribution.className = "tradingview-widget-copyright";
  const link = document.createElement("a"); link.href = "https://www.tradingview.com/"; link.textContent = `${symbol} chart by TradingView`;
  link.target = "_blank"; link.rel = "noopener noreferrer"; attribution.append(link); container.append(attribution);
  $("chart").replaceChildren(container);
}
function setupStages() {
  for (const stage of config.stages) {
    const row = document.createElement("button"); row.type = "button"; row.dataset.stage = stage.id;
    const left = document.createElement("span"); left.className = "row-left";
    const icon = document.createElement("span"); icon.className = "stage-icon";
    const label = document.createElement("span"); label.textContent = stage.label;
    const read = document.createElement("span"); read.className = "read-label"; read.textContent = "원문 열람";
    left.append(icon, label, read); const status = document.createElement("span"); status.className = "state-label";
    row.append(left, status); row.addEventListener("click", () => openReport(stage.id)); $("agents").append(row);
  }
  drawStages(null);
}
async function poll() {
  if (pollBusy) return; pollBusy = true;
  try {
    const job = await api("/api/jobs/active");
    if (!job) return;
    const changed = currentJob?.archive_id !== job.archive_id;
    if (currentJob?.id !== job.id) {
      chart(job.ticker); lastFinal = null;
      $("final-report").innerHTML = '<div class="empty-state"><h3>보고서를 작성하고 있습니다.</h3><p>Portfolio Manager 완료 후 원문이 표시됩니다.</p></div>';
    }
    currentJob = job; drawStages(job); displayFinal(job.final_markdown, job.final_html, job.final_rating);
    const running = job.status === "running";
    $("start-button").disabled = running;
    $("start-button").querySelector("span:last-child").textContent = running ? "분석 진행 중" : "분석 시작";
    $("run-status").classList.toggle("failed", job.status === "failed");
    $("run-status").textContent = job.status === "failed" ? job.error : running
      ? `${job.ticker} · ${job.trade_date} 분석 중 · 완료된 보고서를 바로 열람할 수 있습니다.`
      : `${job.ticker} · ${job.trade_date} 정상 완료 · 실행별 JSON 저장됨`;
    if (changed && job.archive_id && location.pathname === "/history") await loadHistory();
  } catch (e) { error("실행 상태를 연결하지 못했습니다. 서버가 실행 중인지 확인하세요. " + e.message); }
  finally { pollBusy = false; }
}
const optionalNumber = id => $(id).value === "" ? null : Number($(id).value);
$("analysis-form").addEventListener("submit", async event => {
  event.preventDefault(); error(""); $("start-button").disabled = true; drawStages(null);
  try {
    const payload = {ticker:$("ticker").value, trade_date:$("trade-date").value,
      quantity:optionalNumber("quantity"), average_price:optionalNumber("average-price"), cash:optionalNumber("cash"),
      quick_model:$("quick-model").value, deep_model:$("deep-model").value,
      research_rounds:Number($("research-rounds").value), risk_rounds:Number($("risk-rounds").value), confirm_cost:$("confirm-cost").checked};
    await api("/api/jobs", {method:"POST", headers:{"Content-Type":"application/json", "X-Dashboard-Token":config.csrf_token}, body:JSON.stringify(payload)});
    lastFinal = null; $("final-report").innerHTML = '<div class="empty-state"><h3>보고서를 작성하고 있습니다.</h3><p>PM 완료 전에도 개별 에이전트 보고서를 열람할 수 있습니다.</p></div>';
    $("final-rating").hidden = true; $("confirm-cost").checked = false; chart(payload.ticker); await poll();
  } catch (e) { error(e.message); $("start-button").disabled = false; }
});
async function selectHistory(id) {
  try {
    const record = await api(`/api/history/${encodeURIComponent(id)}`); selectedHistory = id;
    historyReports = record.reports;
    $("history-title").textContent = `${record.ticker} · ${record.trade_date}`;
    $("history-rating").hidden = !record.final_rating; $("history-rating").textContent = record.final_rating || "";
    const settings = record.settings;
    $("history-meta").textContent = `${new Date(record.completed_at).toLocaleString("ko-KR")} 완료 · ${settings.quick_think_llm || "—"} / ${settings.deep_think_llm || "—"} · 연구 ${settings.max_debate_rounds ?? "—"} / 위험 ${settings.max_risk_discuss_rounds ?? "—"}라운드`;
    $("history-report").innerHTML = record.final_html; safeLinks($("history-report")); $("history-report").scrollTop = 0;
    $("history-reports").replaceChildren(...Object.entries(record.reports).map(([key, report]) => reportButton(key, report, "history")));
    document.querySelectorAll(".history-item").forEach(b => b.classList.toggle("selected", b.dataset.id === id));
  } catch (e) { error(e.message); }
}
async function loadHistory() {
  try {
    const data = await api(`/api/history?ticker=${encodeURIComponent($("history-search").value.trim())}`);
    $("history-count").textContent = `${data.items.length}건`;
    $("history-note").textContent = "최근 완료순 · 실행별 개별 기록" + (data.skipped ? ` · 읽을 수 없는 파일 ${data.skipped}개 제외` : "");
    $("history-list").replaceChildren();
    for (const item of data.items) {
      const b = document.createElement("button"); b.type = "button"; b.className = "history-item"; b.dataset.id = item.id;
      const left = document.createElement("span"), name = document.createElement("strong"), date = document.createElement("span"), time = document.createElement("span"), rating = document.createElement("span");
      name.textContent = item.ticker; date.textContent = item.trade_date; date.className = "item-date";
      time.textContent = new Date(item.completed_at).toLocaleString("ko-KR"); time.className = "item-time";
      rating.textContent = item.rating || "REVIEW"; rating.className = "item-rating";
      left.append(name, date, time); b.append(left, rating); b.addEventListener("click", () => selectHistory(item.id)); $("history-list").append(b);
    }
    if (data.items.length) await selectHistory(data.items.some(i => i.id === selectedHistory) ? selectedHistory : data.items[0].id);
    else {
      selectedHistory = null; historyReports = {}; $("history-title").textContent = "완료된 분석 기록"; $("history-rating").hidden = true; $("history-meta").textContent = ""; $("history-reports").replaceChildren();
      $("history-report").innerHTML = '<div class="empty-state"><h3>일치하는 분석 기록이 없습니다.</h3><p>티커 검색어 또는 서버의 결과 경로를 확인하세요.</p></div>';
    }
  } catch (e) { error(e.message); }
}
function navigate(path) {
  const history = path === "/history";
  $("main-page").hidden = history; $("history-page").hidden = !history;
  $("nav-main").classList.toggle("active", !history); $("nav-history").classList.toggle("active", history);
  $("page-title").textContent = history ? "판단의 기록, 다시 살펴보세요." : "시장을 읽고, 근거를 확인하세요.";
  if (history) { selectedHistory = null; loadHistory(); }
}
document.querySelectorAll("nav a").forEach(a => a.addEventListener("click", e => { e.preventDefault(); window.history.pushState({}, "", a.pathname); navigate(a.pathname); }));
window.addEventListener("popstate", () => navigate(location.pathname));
$("history-search").addEventListener("input", () => { clearTimeout(historyTimer); historyTimer = setTimeout(loadHistory, 200); });
$("ticker").addEventListener("change", () => chart($("ticker").value));
$("modal-close").addEventListener("click", () => $("report-modal").close());
$("report-modal").addEventListener("click", e => { if (e.target === $("report-modal")) { const r = e.target.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.target.close(); } });
$("raw-toggle").addEventListener("click", () => { rawMode = !rawMode; drawModal(); });
document.querySelectorAll(".debate-actions button").forEach(b => b.addEventListener("click", () => openReport(b.dataset.report)));
(async () => {
  try {
    config = await api("/api/config"); $("trade-date").value = config.today; $("trade-date").max = config.today;
    $("today").textContent = config.today.replaceAll("-", ".");
    for (const tier of ["quick", "deep"]) {
      $(`${tier}-model`).value = config.models[tier].default;
      for (const id of config.models[tier].models) { const o = document.createElement("option"); o.value = id; $(`${tier}-options`).append(o); }
    }
    $("research-rounds").value = config.research_rounds; $("risk-rounds").value = config.risk_rounds;
    $("model-summary").textContent = `${config.models.quick.default} / ${config.models.deep.default}`;
    if (config.demo) { $("server-mode").textContent = "DEMO · 모의 실행"; $("server-mode").style.color = "#b57417"; $("cost-label").textContent = "오프라인 모의 분석을 실행합니다. 실제 API 비용은 없습니다."; }
    setupStages(); navigate(location.pathname); chart($("ticker").value); await poll(); setInterval(poll, 1000);
  } catch (e) { error("서버 설정을 불러오지 못했습니다. " + e.message); $("start-button").disabled = true; }
})();
