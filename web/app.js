"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { status: null, dataset: null, result: null, standards: null, busy: false, history: [], sessionId: getSessionId(), conversation: [], analyses: [], activeAnalysis: 0, liveTools: [], stages: [], streamingAnswer: "" };
  const colors = ["#258575", "#98b35b", "#d4aa61", "#718db7", "#b48d9d", "#6ca896", "#b6bf7b", "#748c6c"];
  const fieldNames = { rows: "记录数", cities: "城市", provinces: "省份", years: "年份", pollutant: "污染物", name: "名称", limit: "限值", annual_limit: "年均限值", annual: "年均限值", daily_limit: "日均限值", daily: "日均限值", unit: "单位", basis: "统计口径", note: "说明", description: "说明", source: "来源", standard: "标准", year: "年份", level: "等级", valid_rows: "有效记录", input_rows: "输入记录", total_rows: "总记录数", dropped_rows: "剔除记录", duplicate_rows: "重复记录", duplicates_removed: "去除重复记录", missing_values: "缺失值", missing_rows: "缺失记录", invalid_rows: "无效记录", negative_values: "负值", date_min: "起始日期", date_max: "结束日期", city_count: "城市数", file_count: "文件数", clean_rows: "清洗后记录", removed_rows: "移除记录", null_values: "空值", raw_rows: "原始记录", files: "数据文件", warnings: "质量说明", coverage: "覆盖率", valid_days: "有效天数", missing: "缺失", duplicates: "重复", cleaned_rows: "清洗后记录", source_rows: "源记录", invalid_dates: "无效日期", dropped_missing: "缺失剔除", dropped_duplicates: "重复剔除", dropped_invalid: "无效剔除", non_numeric_values: "非数值记录", negative_values_removed: "剔除负值", station_count: "监测站数量", daily_min_hours: "日均最低小时数", min_hours_per_day: "日均最低小时数", aggregation: "聚合方式", excluded_rows: "排除记录", total_files: "文件总数" };
  const intentNames = { ranking: "城市排名", exceedance: "超标分析", trend: "趋势分析", seasonal: "季节分析", anomaly: "异常观察" };
  const numeric = (v) => typeof v === "number" && Number.isFinite(v);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name) => `<svg aria-hidden="true"><use href="#i-${esc(name)}"/></svg>`;
  const arr = (v) => Array.isArray(v) ? v : [];
  const number = (v, digits = 2) => numeric(v) ? v.toLocaleString("zh-CN", { maximumFractionDigits: digits }) : String(v ?? "—");
  const display = (v) => v === null || v === undefined || v === "" ? "—" : typeof v === "boolean" ? (v ? "是" : "否") : numeric(v) ? number(v) : typeof v === "object" ? JSON.stringify(v) : String(v);
  const ms = (v) => numeric(v) ? v >= 1000 ? `${number(v / 1000, 2)} 秒` : `${number(v)} ms` : "";
  const text = (id, value) => { $(id).textContent = value ?? ""; };
  function renderAnswer(id, value) {
    const host = $(id); host.replaceChildren();
    for (const raw of String(value || "").split("\n")) {
      if (!raw.trim()) continue;
      const heading = /^#{1,4}\s+/.test(raw);
      const line = document.createElement(heading ? "h4" : "p");
      const plain = raw.replace(/^#{1,4}\s+/, "").replace(/^[-*]\s+/, "• ");
      for (const part of plain.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
        if (part.startsWith("**") && part.endsWith("**")) {
          const strong = document.createElement("strong"); strong.textContent = part.slice(2,-2); line.appendChild(strong);
        } else if (part.startsWith("`") && part.endsWith("`")) {
          const code = document.createElement("code"); code.textContent = part.slice(1,-1); line.appendChild(code);
        } else line.appendChild(document.createTextNode(part));
      }
      host.appendChild(line);
    }
  }

  function getSessionId() {
    try { const existing = localStorage.getItem("airscope.session"); if (existing && /^[a-zA-Z0-9-]{10,80}$/.test(existing)) return existing; } catch (_) { /* Storage may be unavailable in private mode. */ }
    return createSessionId();
  }
  function createSessionId() {
    const id = globalThis.crypto?.randomUUID ? crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => { const r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16); });
    try { localStorage.setItem("airscope.session", id); } catch (_) { /* The current in-memory session still works. */ }
    return id;
  }
  function safeHref(value, localOnly = false) {
    if (typeof value !== "string" || !value) return "";
    try {
      const url = new URL(value, location.href);
      if (!["http:", "https:"].includes(url.protocol)) return "";
      if (localOnly && url.origin !== location.origin) return "";
      return url.href;
    } catch (_) { return ""; }
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), path === "/api/analyze" ? 300000 : 20000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, headers: { "Accept": "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) } });
      let data;
      try { data = await response.json(); } catch (_) { throw new Error(`本地服务返回了无效响应（HTTP ${response.status}）。请确认分析服务已启动。`); }
      if (!response.ok) { const error = new Error(data.error || `请求失败（HTTP ${response.status}）。`); error.suggestions = data.suggestions; throw error; }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("等待服务响应超时。请检查模型与本地服务连接，再重试。");
      if (error instanceof TypeError) throw new Error("无法连接本地分析服务。请确认服务正在运行，然后重试。");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function notice(message = "", type = "", suggestions = []) {
    const host = $("notice");
    host.hidden = !message;
    host.className = `notice ${type}`;
    host.innerHTML = esc(message) + (arr(suggestions).length ? `<div class="notice-actions">${suggestions.map((s) => `<button type="button" data-suggestion="${esc(typeof s === "string" ? s : s.query || s.label || display(s))}">${esc(typeof s === "string" ? s : s.label || s.query || display(s))}</button>`).join("")}</div>` : "");
    host.querySelectorAll("[data-suggestion]").forEach((button) => button.addEventListener("click", () => useQuery(button.dataset.suggestion)));
  }
  function canAnalyze() { return state.status?.ready === true && state.status?.api_configured !== false; }
  function setBusy(value, loadingText) {
    state.busy = value;
    $("run-button").disabled = value || !canAnalyze();
    $("run-button").querySelector(".button-text").textContent = value ? "分析中…" : "开始分析";
    $("loading").hidden = !value;
    if (loadingText) $("loading").querySelector("strong").textContent = loadingText;
    $("query-input").setAttribute("aria-busy", String(value));
    $("results").setAttribute("aria-busy", String(value));
    ["followup-button", "new-session", "new-conversation"].forEach((id) => { $(id).disabled = value; });
    document.querySelectorAll(".example-button").forEach((button) => { button.disabled = value || !canAnalyze() || !button.dataset.query; });
  }
  function showEmpty(title, message, action = "data") {
    $("empty-state").hidden = false;
    $("empty-state").innerHTML = `${icon("data")}<h2>${esc(title)}</h2><p>${esc(message)}</p>${action === "retry" ? `<button class="secondary-button" type="button" id="retry-status">${icon("refresh")}重新连接</button>` : `<a class="secondary-button" href="#data">查看数据与口径 ${icon("arrow")}</a>`}`;
    $("retry-status")?.addEventListener("click", loadStatus);
  }
  function route() {
    const names = { workspace: "分析工作台", history: "分析记录", data: "数据与口径", guide: "演示说明" };
    const view = location.hash.slice(1) in names ? location.hash.slice(1) : "workspace";
    document.querySelectorAll(".view").forEach((el) => { el.hidden = el.id !== `view-${view}`; });
    document.querySelectorAll("[data-view]").forEach((el) => { const active = el.dataset.view === view; el.classList.toggle("active", active); active ? el.setAttribute("aria-current", "page") : el.removeAttribute("aria-current"); el.title = names[el.dataset.view]; });
    text("view-label", names[view]);
    hideTooltip();
    if (view === "history") loadHistory();
    if (view === "data" && state.dataset) renderDataset();
  }
  function updateStatus() {
    const dataset = state.dataset || {};
    const years = arr(dataset.years).filter((v) => Number.isFinite(Number(v))).map(Number).sort((a, b) => a - b);
    const maxYear = years[years.length - 1];
    const preferredYear = dataset.analysis_year !== undefined && dataset.analysis_year !== null ? Number(dataset.analysis_year) : NaN;
    const latest = Number.isFinite(preferredYear) && preferredYear > 0 ? preferredYear : maxYear;
    const cities = arr(dataset.cities);
    const ready = state.status?.ready === true;
    const configured = state.status?.api_configured;
    const model = state.status?.model || "DeepSeek";
    $("connection").innerHTML = `<span class="status-dot ${ready && configured !== false ? "" : "pending"}"></span>${ready ? configured === false ? "DeepSeek 待配置 · 数据已就绪" : "DeepSeek + 本地数据已就绪" : "等待导入本地数据"}`;
    text("model-name", model);
    text("model-caption", configured === false ? "API 尚未配置 · 本地数据工具可用" : `${state.status?.provider || "deepseek"} · 模型联网理解与回答 · 数据在本地计算`);
    text("model-connection", configured === false ? "待配置" : configured === true ? "API 已配置" : "等待模型状态");
    $("model-connection").classList.toggle("pending", configured !== true);
    text("engine-label", `${model} · 本地工具`);
    text("workspace-rows", numeric(dataset.rows) ? number(dataset.rows, 0) : "—");
    text("workspace-cities", cities.length ? number(cities.length, 0) : "—");
    text("workspace-years", years.length ? number(years.length, 0) : "—");
    text("heading-coverage", ready && years.length ? `${years.length > 1 ? `${years[0]} — ${maxYear}` : maxYear} / ${cities.length ? `${number(cities.length)} 座城市` : "本地数据"}` : "等待可用数据");
    text("history-count", numeric(state.status?.history_count) ? number(state.status.history_count, 0) : "—");
    const yearText = latest ? `${latest} 年` : "可用年份";
    const beijing = cities.find((city) => /^(北京|北京市)$/.test(String(city)));
    const summerCity = beijing || cities[0];
    const examples = [
      { label: `${yearText} · 年均 Top 5 与月度变化`, query: latest ? `找出${latest}年PM2.5年均浓度最高的5个城市，画出月度变化曲线` : "" },
      { label: `${yearText} · 各省 PM2.5 超标占比`, query: latest ? `筛选${latest}年PM2.5超标记录，统计各省份超标天数占比` : "" },
      { label: `${yearText} · ${summerCity || "城市"}夏季 O₃ 超标`, query: latest && summerCity ? `统计${latest}年${summerCity}夏季臭氧O3超标的总天数` : "" }
    ];
    $("examples").innerHTML = examples.map((example) => `<button type="button" class="example-button" data-query="${esc(example.query)}" ${!canAnalyze() || !example.query || state.busy ? "disabled" : ""}><span>${esc(example.label)}</span><span>↗</span></button>`).join("");
    $("examples").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => useQuery(button.dataset.query)));
    $("run-button").disabled = !canAnalyze() || state.busy;
    if (ready && latest) $("query-input").placeholder = `例如：找出 ${latest} 年 PM2.5 年均浓度最高的 5 个城市，画出月度变化曲线`;
    if (!ready) showEmpty("让真实数据，开启第一次观察。", "当前还没有可用于分析的本地数据。请按项目说明导入数据并重新连接。所有图表将在真实数据就绪后生成。");
    else if (configured === false) showEmpty("数据已就绪，等待连接 DeepSeek。", "DeepSeek API 尚未配置。请按项目说明在服务端配置后重新连接；页面不会读取或展示 API 密钥。", "retry");
    else if (!state.result && !state.busy) $("empty-state").hidden = true;
    return { ready, latest };
  }
  async function loadStatus() {
    try {
      const status = await api("/api/status");
      state.status = status; state.dataset = status.dataset || {};
      updateStatus(); renderDataset();
    } catch (error) {
      $("connection").innerHTML = `<span class="status-dot error"></span>本地服务未连接`;
      text("heading-coverage", "本地服务待连接");
      text("model-caption", "模型状态暂不可用"); text("model-connection", "未连接");
      $("run-button").disabled = true;
      showEmpty("连接本地分析服务。", error.message, "retry"); renderDatasetError(error.message);
    }
  }
  function useQuery(query) {
    if (state.busy || !query) return;
    if (location.hash !== "#workspace") location.hash = "workspace";
    $("query-input").value = query; $("query-input").focus();
    notice("问题已填入，点击「开始分析」发送给 DeepSeek。");
  }
  function loadConversation() {
    try {
      const saved = JSON.parse(localStorage.getItem(`airscope.conversation.${state.sessionId}`) || "[]");
      state.conversation = arr(saved).filter((message) => ["user", "assistant"].includes(message?.role) && typeof message.content === "string").slice(-16);
    } catch (_) { state.conversation = []; }
    renderConversation();
  }
  function appendMessage(role, content, extra = {}) {
    state.conversation.push({ role, content: String(content || "").slice(0, 16000), ...extra });
    state.conversation = state.conversation.slice(-16);
    try { localStorage.setItem(`airscope.conversation.${state.sessionId}`, JSON.stringify(state.conversation)); } catch (_) { /* Conversation continues in memory when storage is unavailable. */ }
    renderConversation();
  }
  function renderConversation() {
    const host = $("conversation-list"); host.replaceChildren();
    $("conversation").hidden = !state.conversation.length;
    text("conversation-count", `${state.conversation.filter((entry) => entry.role === "user").length} 轮 · 最近对话`);
    state.conversation.slice(-6).forEach((message) => {
      const article = document.createElement("article"); article.className = `chat-message ${message.role}${message.failed ? " failed" : ""}`;
      const label = document.createElement("span"); label.className = "chat-role"; label.textContent = message.role === "user" ? "你" : message.model || "DeepSeek";
      const content = document.createElement("p"); content.textContent = message.content.length > 420 ? `${message.content.slice(0, 420)}…` : message.content;
      article.append(label, content); host.appendChild(article);
    });
    host.scrollTop = host.scrollHeight;
  }
  function addStage(message) {
    if (!message || state.stages[state.stages.length - 1] === String(message)) return;
    state.stages.push(String(message));
    const li = document.createElement("li"); li.textContent = String(message); $("stage-list").appendChild(li);
    $("loading").querySelector("strong").textContent = String(message);
  }
  function receiveEvent(event) {
    if (!event || typeof event !== "object") throw new Error("服务返回了无效的流式事件。");
    if (event.type === "error") { const error = new Error(event.error || "DeepSeek 分析未完成。"); error.suggestions = event.suggestions; throw error; }
    if (event.type === "stage") addStage(event.message);
    if (["tool_start", "tool_end"].includes(event.type)) {
      const ending = event.type === "tool_end";
      let step = ending ? [...state.liveTools].reverse().find((item) => item.status === "running" && (event.call_id ? item.call_id === event.call_id : item.tool === event.tool)) : null;
      const fields = Object.fromEntries(Object.entries(event).filter(([, value]) => value !== undefined));
      if (!step) { step = { ...fields }; state.liveTools.push(step); } else Object.assign(step, fields);
      step.status = ending ? event.status || "success" : "running";
      renderTrace(state.liveTools, "live-trace-list");
      const running = state.liveTools.filter((item) => item.status === "running").length;
      text("process-status", `${state.liveTools.length} 次工具调用${running ? ` · ${running} 项执行中` : " · 已返回观测"}`);
    }
    if (event.type === "answer") {
      state.streamingAnswer = event.delta === true ? state.streamingAnswer + String(event.content || "") : String(event.content || "");
      $("stream-answer").hidden = !state.streamingAnswer;
      text("stream-answer-content", state.streamingAnswer);
    }
  }
  async function analyzeStream(query) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 300000);
    try {
      const body = JSON.stringify({ query, session_id: state.sessionId });
      const response = await fetch("/api/analyze/stream", { method: "POST", body, signal: controller.signal, headers: { "Content-Type": "application/json", "Accept": "application/x-ndjson" } });
      if ([404, 405, 501].includes(response.status)) {
        addStage("当前服务提供同步接口，等待模型与工具完成后返回真实结果。");
        return await api("/api/analyze", { method: "POST", body });
      }
      if (!response.ok) {
        let detail = {}; try { detail = await response.json(); } catch (_) { /* An HTTP error may have no JSON body. */ }
        const error = new Error(detail.error || `分析请求失败（HTTP ${response.status}）。`); error.suggestions = detail.suggestions; throw error;
      }
      let result = null; let buffer = "";
      const consumeLine = (line) => {
        if (!line.trim()) return;
        let event; try { event = JSON.parse(line); } catch (_) { throw new Error("服务返回的流式数据格式无效，未能读取完整结果。"); }
        receiveEvent(event); if (event.type === "result") result = event.result;
      };
      if (response.body?.getReader) {
        const reader = response.body.getReader(); const decoder = new TextDecoder();
        try {
          while (true) {
            const { value, done } = await reader.read();
            buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
            let newline;
            while ((newline = buffer.indexOf("\n")) >= 0) { consumeLine(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1); }
            if (done) break;
          }
          if (buffer.trim()) consumeLine(buffer);
        } catch (error) { try { await reader.cancel(); } catch (_) { /* Preserve the original stream failure. */ } throw error; }
        finally { reader.releaseLock(); }
      } else {
        (await response.text()).split(/\r?\n/).forEach(consumeLine);
      }
      if (!result || typeof result !== "object") throw new Error("连接已结束，但尚未收到完整分析结果。已返回的工具过程保留在下方。");
      if (!result.answer && state.streamingAnswer) result.answer = state.streamingAnswer;
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("等待 DeepSeek 响应超时。已返回的工具过程已保留，请检查连接后重试。");
      if (error instanceof TypeError) throw new Error("无法连接分析服务或流式连接中断，请确认服务正在运行。");
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function runAnalysis(query) {
    if (state.busy) return;
    query = String(query || "").trim();
    if (!query) { notice("先写下一个想了解的空气质量问题。"); $("query-input").focus(); return; }
    if (!canAnalyze()) { notice(state.status?.api_configured === false ? "DeepSeek API 尚未配置，请先按项目说明配置服务。" : "本地数据尚未就绪，请先在「数据与口径」确认数据已导入。", "error"); return; }
    notice(); hideTooltip(); $("empty-state").hidden = true; $("results").hidden = true;
    state.liveTools = []; state.stages = []; state.streamingAnswer = "";
    $("stage-list").replaceChildren(); $("live-trace-list").replaceChildren(); $("stream-answer").hidden = true;
    $("live-process").hidden = false; $("live-process").open = true; text("process-status", "等待服务返回");
    appendMessage("user", query); setBusy(true, "DeepSeek 正在理解你的问题");
    try {
      const result = await analyzeStream(query);
      if (result.ok === false) { const error = new Error(result.error || "这次问题尚未完成分析。"); error.suggestions = result.suggestions; throw error; }
      renderResult(result);
      appendMessage("assistant", result.answer || result.summary || "分析已完成，请查看下方本地计算结果。", { model: result.model || state.status?.model || "DeepSeek", resultId: result.id });
      $("conversation").open = false;
      text("session-label", "当前会话已保留 · 可以继续追问");
      text("process-status", state.liveTools.length ? `已完成 · ${state.liveTools.length} 次工具调用` : "回答已完成");
      $("stream-answer").hidden = true; $("live-process").open = false;
      loadHistory(false);
    } catch (error) {
      state.liveTools.filter((step) => step.status === "running").forEach((step) => { step.status = "unconfirmed"; });
      renderTrace(state.liveTools, "live-trace-list"); text("process-status", "请求未完成 · 已收到的过程已保留");
      notice(error.message, "error", error.suggestions);
      appendMessage("assistant", `这次请求未完成：${error.message}`, { failed: true });
    } finally { setBusy(false); }
  }
  function renderResult(result) {
    state.result = result;
    $("empty-state").hidden = true; $("results").hidden = false;
    text("result-run-label", result.id ? String(result.id).slice(0, 16).toUpperCase() : "DEEPSEEK ANSWER");
    text("result-title", result.title || "空气质量问答");
    text("result-duration", numeric(result.elapsed_ms) ? `总用时 ${ms(result.elapsed_ms)}` : "回答已保存");
    const answer = typeof result.answer === "string" ? result.answer : "";
    $("answer-panel").hidden = !answer; renderAnswer("answer-content", answer);
    text("answer-model", [result.provider || state.status?.provider || "deepseek", result.model || state.status?.model || "DeepSeek"].join(" / "));
    const usage = result.usage || {};
    const usageParts = [numeric(usage.prompt_tokens) ? `输入 ${number(usage.prompt_tokens, 0)}` : "", numeric(usage.completion_tokens) ? `输出 ${number(usage.completion_tokens, 0)}` : "", numeric(usage.total_tokens) ? `合计 ${number(usage.total_tokens, 0)} tokens` : ""].filter(Boolean);
    $("answer-usage").hidden = !usageParts.length; text("answer-usage", usageParts.join(" · "));
    state.analyses = arr(result.analyses).filter((analysis) => analysis && typeof analysis === "object");
    if (!state.analyses.length) state.analyses = [result];
    state.activeAnalysis = 0; renderAnalysisTabs(); renderAnalysis(0);
  }
  function renderAnalysisTabs() {
    const host = $("analysis-tabs"); host.replaceChildren(); host.hidden = state.analyses.length < 2;
    state.analyses.forEach((analysis, index) => {
      const button = document.createElement("button"); button.type = "button"; button.id = `analysis-tab-${index}`; button.setAttribute("role", "tab"); button.setAttribute("aria-controls", "analysis-evidence");
      button.textContent = `${String(index + 1).padStart(2, "0")}  ${analysis.title || [analysis.plan?.pollutant, intentNames[analysis.plan?.intent] || analysis.plan?.intent, analysis.plan?.year].filter(Boolean).join(" · ") || "数据分析"}`;
      button.addEventListener("click", () => renderAnalysis(index));
      button.addEventListener("keydown", (event) => {
        const total = state.analyses.length;
        const target = event.key === "ArrowRight" ? (index + 1) % total : event.key === "ArrowLeft" ? (index + total - 1) % total : event.key === "Home" ? 0 : event.key === "End" ? total - 1 : null;
        if (target !== null) { event.preventDefault(); renderAnalysis(target); host.children[target].focus(); }
      }); host.appendChild(button);
    });
  }
  function renderAnalysis(index) {
    const analysis = state.analyses[index] || {}; const root = state.result || {};
    state.activeAnalysis = index; hideTooltip();
    [...$("analysis-tabs").children].forEach((button, i) => { button.setAttribute("aria-selected", String(i === index)); button.tabIndex = i === index ? 0 : -1; });
    if (state.analyses.length > 1) $("analysis-evidence").setAttribute("aria-labelledby", `analysis-tab-${index}`); else $("analysis-evidence").removeAttribute("aria-labelledby");
    const plan = analysis.plan || {};
    const tags = [plan.pollutant, plan.year ? `${plan.year} 年` : null, intentNames[plan.intent] || plan.intent, ...arr(plan.cities), ...arr(plan.provinces)];
    $("plan-tags").innerHTML = tags.filter((v) => v !== null && v !== undefined && v !== "").map((v) => `<span class="plan-tag">${esc(v)}</span>`).join("");
    text("result-summary", analysis.summary || ""); $("result-summary").hidden = !analysis.summary;
    const metrics = arr(analysis.metrics).filter((metric) => metric && metric.value !== null && metric.value !== undefined);
    $("metrics").hidden = !metrics.length; $("metrics").style.gridTemplateColumns = metrics.length > 0 && metrics.length < 4 && window.innerWidth > 560 ? `repeat(${metrics.length},minmax(0,1fr))` : "";
    $("metrics").innerHTML = metrics.map((metric) => `<div class="metric"><div class="metric-label">${esc(metric.label || "指标")}</div><div class="metric-value">${esc(display(metric.value))}<small>${esc(metric.unit || "")}</small></div></div>`).join("");
    renderCharts(analysis.charts);
    $("insight").hidden = !analysis.insight;
    $("insight").innerHTML = analysis.insight ? `${icon("leaf")}<p><strong>数据观察</strong>${esc(analysis.insight)}</p>` : "";
    const hasTable = arr(analysis.table?.columns).length > 0;
    $("result-table-panel").hidden = !hasTable; renderTable(analysis.table, analysis.standard);
    const trace = arr(root.trace).length ? root.trace : arr(analysis.trace).length ? analysis.trace : state.liveTools;
    $("result-trace-panel").hidden = !trace.length; renderTrace(trace);
    const grid = $("result-table-panel").parentElement;
    grid.hidden = !hasTable && !trace.length; grid.classList.toggle("single-panel", !hasTable || !trace.length);
    renderWarnings(analysis.warnings, analysis.quality);
    $("analysis-evidence").hidden = !metrics.length && !arr(analysis.charts).length && !analysis.insight && !hasTable && !trace.length && !arr(analysis.warnings).length;
    renderExports({ ...root.downloads, ...analysis.downloads, html: root.downloads?.html || analysis.downloads?.html });
    const hasCities = arr(analysis.context?.cities).length || arr(plan.cities).length;
    $("followup-button").dataset.query = hasCities ? "再看这几个城市的PM10超标情况" : "请解释刚才回答的统计口径、依据与局限";
    $("followup-button").innerHTML = `${esc(hasCities ? "再看这几个城市的 PM10 超标情况" : "解释这个回答的依据与局限")} ${icon("arrow")}`;
  }
  function renderTable(table = {}, standard = {}) {
    table = table || {}; standard = standard || {};
    const columns = arr(table.columns); const rows = arr(table.rows);
    text("row-count", `${number(rows.length, 0)} 条结果`);
    const isNumericKey = (column) => rows.some((row) => numeric(row[column.key]));
    $("result-table").innerHTML = columns.length ? `<thead><tr>${columns.map((column) => `<th scope="col" class="${isNumericKey(column) ? "numeric-cell" : ""}">${esc(column.label || column.key)}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map((row) => `<tr>${columns.map((column) => {
      const value = row[column.key];
      let content = esc(display(value));
      if (["compliant", "meets_standard", "is_compliant", "达标"].includes(column.key) && typeof value === "boolean") content = `<span class="status-cell ${!value ? "exceeds" : ""}">${value ? "达标" : "超标"}</span>`;
      else if (typeof value === "string" && /^(达标|超标|未超标|不达标|满足|不满足)$/.test(value)) content = `<span class="status-cell ${/^(超标|不达标|不满足)$/.test(value) ? "exceeds" : ""}">${esc(value)}</span>`;
      return `<td class="${numeric(value) ? "numeric-cell" : ""}">${content}</td>`;
    }).join("")}</tr>`).join("") : `<tr><td colspan="${columns.length}">${esc(table.empty_message || "本次筛选范围内没有有效记录。")}</td></tr>`}</tbody>` : "<tbody><tr><td>本次分析未返回结果表。</td></tr></tbody>";
    const url = safeHref(standard.url);
    const parts = [standard.name, numeric(standard.limit) ? `${standard.basis || "参考限值"} ${number(standard.limit)} ${standard.unit || ""}` : standard.basis, standard.note].filter(Boolean);
    $("standard-note").hidden = !parts.length;
    $("standard-note").innerHTML = parts.map(esc).join(" · ") + (url ? ` <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">标准来源 ↗</a>` : "");
  }
  function renderTrace(trace, hostId = "trace-list") {
    const host = $(hostId); host.replaceChildren();
    const statusNames = { running: "执行中", success: "已完成", ok: "已完成", completed: "已完成", error: "失败", failed: "失败", unconfirmed: "未收到结束事件" };
    arr(trace).forEach((step, i) => {
      const li = document.createElement("li"); li.className = `trace-step ${["error", "failed"].includes(step.status) ? "error" : ""} ${step.status === "running" ? "running" : ""}`;
      const badge = document.createElement("span"); badge.className = "trace-number"; badge.textContent = String(i + 1).padStart(2, "0");
      const copy = document.createElement("div"); copy.className = "trace-copy";
      const title = document.createElement("div"); title.className = "trace-title";
      const label = document.createElement("span"); label.textContent = step.label || step.tool || "本地工具";
      const duration = document.createElement("span"); duration.textContent = ms(step.duration_ms); title.append(label, duration);
      const observation = document.createElement("p"); observation.className = "tool-observation";
      observation.textContent = step.observation !== undefined && step.observation !== null ? typeof step.observation === "object" ? JSON.stringify(step.observation, null, 2) : String(step.observation) : step.status === "running" ? "工具已开始，等待实际观测返回。" : step.status === "unconfirmed" ? "连接中断，工具完成状态尚未确认。" : "服务未返回观测文本。";
      const tool = document.createElement("span"); tool.className = "trace-tool"; tool.textContent = [step.tool, statusNames[step.status] || step.status].filter(Boolean).join(" / ");
      copy.append(title, observation, tool);
      if (step.arguments !== undefined && step.arguments !== null) {
        const details = document.createElement("details"); details.className = "tool-arguments";
        const summary = document.createElement("summary"); summary.textContent = "调用参数";
        const pre = document.createElement("pre"); pre.textContent = typeof step.arguments === "string" ? step.arguments : JSON.stringify(step.arguments, null, 2);
        details.append(summary, pre); copy.appendChild(details);
      }
      li.append(badge, copy); host.appendChild(li);
    });
  }
  function renderWarnings(warnings, quality) {
    const entries = arr(warnings).map((warning) => typeof warning === "string" ? warning : warning.message || warning.note || display(warning));
    $("warnings").hidden = !entries.length;
    $("warnings").innerHTML = entries.length ? `<details><summary>数据完整性说明 · ${entries.length} 项</summary>${entries.map((warning) => `<div class="warning-item">${icon("info")}<span>${esc(warning)}</span></div>`).join("")}</details>` : "";
  }
  function renderExports(downloads = {}) {
    const entries = [["csv", "CSV"], ["json", "JSON"], ["html", "HTML"], ["svg", "SVG"]];
    $("exports").innerHTML = entries.map(([key, label]) => { const href = safeHref(downloads?.[key], true); return href ? `<a class="export-link" href="${esc(href)}" download>${esc(label)} ${icon("download")}</a>` : ""; }).join("");
    $("exports").closest(".export-bar").hidden = !$("exports").children.length;
  }

  function renderCharts(charts) {
    const data = arr(charts).map((chart, index) => ({ ...chart, originalIndex: index })).sort((a, b) => (a.type === "line" ? 0 : 1) - (b.type === "line" ? 0 : 1));
    const host = $("charts"); host.hidden = !data.length; host.innerHTML = "";
    data.forEach((chart, i) => {
      const panel = document.createElement("section"); panel.className = "chart-panel";
      const typeNames = { line: "MONTHLY PATTERN", bar: "CITY COMPARISON", donut: "DATA COMPOSITION" };
      const legend = (chart.type === "donut" ? arr(chart.labels).map((label) => ({ name: label })) : arr(chart.series)).map((series, si) => `<span class="legend-item"><i class="legend-dot" style="background:${colors[si % colors.length]}"></i>${esc(series.name || "数值")}</span>`).join("");
      const hasThreshold = numeric(chart.threshold);
      panel.innerHTML = `<div class="chart-panel-header"><div><span class="section-kicker">${typeNames[chart.type] || "DATA VISUALIZATION"}</span><h3>${esc(chart.title || "分析图表")}<span class="chart-unit">${esc(chart.unit || "")}</span></h3></div><button type="button" class="icon-button" aria-label="下载${esc(chart.title || "图表")} SVG" title="下载此图 SVG">${icon("download")}</button></div><div class="chart-legend">${legend}${hasThreshold ? `<span class="legend-item threshold"><i class="legend-dot"></i>${esc(chart.threshold_label || "参考限值")} ${esc(number(chart.threshold))}</span>` : ""}</div><div class="chart-wrap"></div><div class="chart-foot"><span>本地数据计算 · ${chart.type === "line" ? "缺测值保留断点" : "悬停查看具体数值"}</span><span>${String(i + 1).padStart(2, "0")} / ${String(data.length).padStart(2, "0")}</span></div>`;
      host.appendChild(panel);
      const wrap = panel.querySelector(".chart-wrap");
      if (chart.type === "donut") renderDonut(wrap, chart, i); else if (["line", "bar"].includes(chart.type)) renderCartesian(wrap, chart, i); else wrap.innerHTML = `<div class="chart-empty">当前图表类型暂未提供可视化：${esc(chart.type)}</div>`;
      panel.querySelector("button").addEventListener("click", () => downloadChart(panel.querySelector("svg.chart-svg"), chart, chart.originalIndex));
    });
  }
  function renderCartesian(wrap, chart, index) {
    const labels = arr(chart.labels); const series = arr(chart.series); const values = series.flatMap((s) => arr(s.values)).filter(numeric);
    if (!labels.length || !values.length) { wrap.innerHTML = `<div class="chart-empty">当前筛选范围没有可绘制的有效数据。</div>`; return; }
    const line = chart.type === "line";
    const w = line ? 760 : 550; const h = line ? 320 : 320;
    const p = { left: 48, right: 23, top: 28, bottom: labels.length > 8 && !line ? 70 : 43 };
    const pw = w - p.left - p.right; const ph = h - p.top - p.bottom;
    const max = Math.max(...values, numeric(chart.threshold) ? chart.threshold : 0, 0);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const step = niceStep(span / 4);
    const maxY = Math.ceil((max + span * .13) / step) * step || 1;
    const minY = min < 0 ? Math.floor(min / step) * step : 0;
    const y = (value) => p.top + (maxY - value) / (maxY - minY) * ph;
    const x = (i) => p.left + (line ? (labels.length === 1 ? pw / 2 : i / (labels.length - 1) * pw) : (i + .5) / labels.length * pw);
    const tickCount = Math.round((maxY - minY) / step);
    let markup = `<svg class="chart-svg" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="chart-title-${index} chart-desc-${index}"><title id="chart-title-${index}">${esc(chart.title || "分析图表")}</title><desc id="chart-desc-${index}">${esc(series.map((s) => s.name).join("、"))}；单位 ${esc(chart.unit || "数值")}。可用 Tab 聚焦数据点查看详情。</desc><defs><linearGradient id="area-${index}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${colors[0]}" stop-opacity=".11"/><stop offset="100%" stop-color="${colors[0]}" stop-opacity=".005"/></linearGradient></defs>`;
    for (let t = 0; t <= tickCount && t < 15; t++) {
      const v = minY + t * step; const py = y(v);
      markup += `<line class="grid-line" x1="${p.left}" y1="${py}" x2="${w - p.right}" y2="${py}"/><text class="axis-label" x="${p.left - 10}" y="${py + 3}" text-anchor="end">${esc(number(v, step < 1 ? 2 : 1))}</text>`;
    }
    if (numeric(chart.threshold)) {
      const ty = y(chart.threshold);
      markup += `<line x1="${p.left}" y1="${ty}" x2="${w - p.right}" y2="${ty}" stroke="#c5ab76" stroke-width="1" stroke-dasharray="5 4"/><text x="${w - p.right}" y="${ty - 6}" fill="#b59c69" font-size="8" text-anchor="end">${esc(chart.threshold_label || "参考限值")} ${esc(number(chart.threshold))}</text>`;
    }
    const labelInterval = Math.max(1, Math.ceil(labels.length / (line ? 12 : 14)));
    labels.forEach((label, i) => {
      if (i % labelInterval !== 0 && i !== labels.length - 1) return;
      const px = x(i); const py = h - p.bottom + 22;
      const rotate = !line && labels.length > 8;
      markup += `<text class="axis-label" x="${px}" y="${py}" text-anchor="${rotate ? "end" : "middle"}"${rotate ? ` transform="rotate(-35 ${px} ${py})"` : ""}>${esc(label)}</text>`;
    });
    series.forEach((s, si) => {
      const c = colors[si % colors.length]; const vals = arr(s.values);
      if (line) {
        const segments = []; let current = [];
        labels.forEach((_, i) => { if (numeric(vals[i])) current.push([x(i), y(vals[i])]); else if (current.length) { segments.push(current); current = []; } });
        if (current.length) segments.push(current);
        segments.forEach((points) => {
          const d = points.map((point, pi) => `${pi ? "L" : "M"}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(" ");
          if (si === 0 && points.length > 1) markup += `<path d="${d} L${points[points.length - 1][0]},${y(minY)} L${points[0][0]},${y(minY)} Z" fill="url(#area-${index})"/>`;
          markup += `<path class="plot-line" d="${d}" stroke="${c}"/>`;
          points.forEach(([px, py]) => { markup += `<circle class="plot-dot" cx="${px}" cy="${py}" r="3.3" fill="${c}"/>`; });
        });
      } else {
        const groupWidth = pw / labels.length * .65; const barWidth = Math.min(40, groupWidth / Math.max(series.length, 1));
        labels.forEach((label, li) => {
          const value = vals[li]; if (!numeric(value)) return;
          const px = x(li) - barWidth * series.length / 2 + si * barWidth; const py = y(Math.max(value, 0)); const barHeight = Math.abs(y(value) - y(0));
          markup += `<rect x="${px}" y="${py}" width="${Math.max(1, barWidth - (series.length > 1 ? 2 : 0))}" height="${Math.max(.7, barHeight)}" rx="3" fill="${c}" fill-opacity="${si === 0 ? ".86" : ".8"}"/>`;
          if (labels.length <= 8 && series.length === 1) markup += `<text class="chart-value" x="${x(li)}" y="${py - 9}" text-anchor="middle">${esc(number(value, 1))}</text>`;
        });
      }
    });
    markup += `<line class="hover-line" x1="0" x2="0" y1="${p.top}" y2="${h - p.bottom}"/>`;
    labels.forEach((label, i) => {
      const unitWidth = line ? pw / Math.max(labels.length - 1, 1) : pw / labels.length;
      const start = line ? Math.max(p.left, x(i) - unitWidth / 2) : p.left + i / labels.length * pw;
      const end = line ? Math.min(w - p.right, x(i) + unitWidth / 2) : p.left + (i + 1) / labels.length * pw;
      const description = series.map((s) => `${s.name || "数值"} ${numeric(arr(s.values)[i]) ? number(s.values[i]) + (chart.unit || "") : "缺测"}`).join("，");
      markup += `<rect class="chart-hit" data-index="${i}" x="${start}" y="${p.top}" width="${Math.max(1, end - start)}" height="${ph}" fill="transparent" tabindex="0" aria-label="${esc(label)}：${esc(description)}"/>`;
    });
    markup += "</svg>"; wrap.innerHTML = markup;
    const svg = wrap.querySelector("svg"); const hover = svg.querySelector(".hover-line");
    svg.querySelectorAll(".chart-hit").forEach((hit) => {
      const i = Number(hit.dataset.index);
      const show = (event) => { hover.setAttribute("x1", x(i)); hover.setAttribute("x2", x(i)); hover.style.opacity = "1"; showTooltip(event, labels[i], series.map((s, si) => ({ name: s.name, value: arr(s.values)[i], color: colors[si % colors.length] })), chart.unit, hit); };
      hit.addEventListener("pointerenter", show); hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
      const hide = () => { hover.style.opacity = "0"; hideTooltip(); };
      hit.addEventListener("pointerleave", hide); hit.addEventListener("blur", hide); hit.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });
    });
  }
  function niceStep(value) {
    if (!Number.isFinite(value) || value <= 0) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(value))); const fraction = value / power;
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10) * power;
  }
  function renderDonut(wrap, chart, index) {
    const labels = arr(chart.labels); const s = arr(chart.series)[0] || {}; const values = arr(s.values);
    const total = values.reduce((sum, value) => sum + (numeric(value) && value > 0 ? value : 0), 0);
    if (!total) { wrap.innerHTML = `<div class="chart-empty">当前筛选范围没有可绘制的有效数据。</div>`; return; }
    const cx = 260, cy = 150, radius = 95, circumference = 2 * Math.PI * radius; let offset = 0;
    let markup = `<svg class="chart-svg" viewBox="0 0 520 300" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="chart-title-${index}"><title id="chart-title-${index}">${esc(chart.title || "数据分布")}</title><circle cx="${cx}" cy="${cy}" r="${radius}" fill="none" stroke="#eef3e6" stroke-width="35"/>`;
    values.forEach((value, i) => { if (!numeric(value) || value <= 0) return; const length = value / total * circumference; markup += `<circle class="chart-hit" data-index="${i}" cx="${cx}" cy="${cy}" r="${radius}" fill="none" stroke="${colors[i % colors.length]}" stroke-width="35" stroke-dasharray="${length} ${circumference - length}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})" tabindex="0" aria-label="${esc(labels[i] || s.name)} ${esc(number(value))}${esc(chart.unit || "")}"/>`; offset += length; });
    markup += `<text x="${cx}" y="${cy - 2}" text-anchor="middle" fill="#527047" font-size="30">${esc(number(total))}</text><text x="${cx}" y="${cy + 22}" text-anchor="middle" fill="#95a582" font-size="10">合计 ${esc(chart.unit || "")}</text></svg>`;
    wrap.innerHTML = markup;
    wrap.querySelectorAll(".chart-hit").forEach((hit) => { const i = Number(hit.dataset.index); const show = (event) => showTooltip(event, labels[i] || s.name, [{ name: labels[i] || s.name, value: values[i], color: colors[i % colors.length] }], chart.unit, hit); hit.addEventListener("pointerenter", show); hit.addEventListener("pointermove", show); hit.addEventListener("focus", show); hit.addEventListener("pointerleave", hideTooltip); hit.addEventListener("blur", hideTooltip); });
  }
  function showTooltip(event, label, entries, unit, target) {
    const tooltip = $("chart-tooltip");
    tooltip.innerHTML = `<div class="tooltip-title">${esc(label)}</div>${entries.map((entry) => `<div class="tooltip-row"><span><i style="background:${entry.color}"></i>${esc(entry.name || "数值")}</span><strong>${numeric(entry.value) ? `${esc(number(entry.value))} <small>${esc(unit || "")}</small>` : "缺测"}</strong></div>`).join("")}`;
    tooltip.hidden = false;
    const rect = target.getBoundingClientRect(); const x = Number.isFinite(event.clientX) && event.type !== "focus" ? event.clientX : rect.left + rect.width / 2; const y = Number.isFinite(event.clientY) && event.type !== "focus" ? event.clientY : rect.top + 40;
    const box = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(window.innerWidth - box.width - 8, x + 14))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(window.innerHeight - box.height - 8, y + 14))}px`;
  }
  function hideTooltip() { $("chart-tooltip").hidden = true; }
  function downloadChart(svg, chart, index) {
    if (!svg) return;
    const copy = svg.cloneNode(true);
    copy.querySelectorAll("rect.chart-hit,.hover-line").forEach((el) => el.remove());
    copy.querySelectorAll(".chart-hit").forEach((el) => { el.removeAttribute("tabindex"); el.removeAttribute("class"); });
    const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
    style.textContent = ".axis-label{font-size:9px;fill:#81906e}.grid-line{stroke:#e4eadb;stroke-width:1;stroke-dasharray:3 4}.plot-line{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.plot-dot{stroke:#fffffb;stroke-width:1.6}.chart-value{font-size:10px;fill:#567246}text{font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif}";
    copy.insertBefore(style, copy.firstChild);
    copy.setAttribute("style", "background:#fffffb");
    const content = new XMLSerializer().serializeToString(copy);
    const url = URL.createObjectURL(new Blob([content], { type: "image/svg+xml;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `清穹-${String(chart.title || `图表${index + 1}`).replace(/[\\/:*?"<>|]/g, "-")}.svg`; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function loadHistory(render = true) {
    try {
      const response = await api("/api/history"); state.history = arr(response.runs);
      text("history-count", number(state.history.length, 0));
      if (!render && location.hash !== "#history") return;
      $("history-list").innerHTML = state.history.length ? state.history.map((run, i) => `<button type="button" class="history-row" data-run="${esc(run.id)}"><span class="history-index">${String(i + 1).padStart(2, "0")}</span><span class="history-copy"><strong>${esc(run.title || "空气质量分析")}</strong><p>${esc(run.query || "")}</p><small>${esc(formatDate(run.created_at))}${numeric(run.elapsed_ms) ? ` · ${esc(ms(run.elapsed_ms))}` : ""}</small></span>${icon("chevron")}</button>`).join("") : `<div class="empty-state">${icon("clock")}<h2>下一次观察，从这里留下。</h2><p>完成第一次分析后，可在此重新打开结果与完整计算轨迹。</p><a class="secondary-button" href="#workspace">开始分析 ${icon("arrow")}</a></div>`;
      $("history-list").querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", () => loadResult(button.dataset.run)));
    } catch (error) { if (render) $("history-list").innerHTML = `<div class="notice error">${esc(error.message)}</div>`; }
  }
  async function loadResult(id) {
    if (state.busy) return;
    location.hash = "workspace"; notice(); state.liveTools = []; $("live-process").hidden = true; setBusy(true, "正在打开已保存的分析");
    try {
      const result = await api(`/api/result?id=${encodeURIComponent(id)}&session_id=${encodeURIComponent(state.sessionId)}`);
      if (result.ok === false) throw new Error(result.error || "未找到这条分析记录。");
      renderResult(result);
      $("query-input").value = result.query || "";
      if (!state.conversation.some((message) => message.resultId === result.id)) {
        if (result.query) appendMessage("user", result.query);
        appendMessage("assistant", result.answer || result.summary || "已打开保存的分析结果。", { model: result.model || "DeepSeek", resultId: result.id });
      }
      text("session-label", "历史结果已载入当前会话 · 可以继续追问");
    } catch (error) { notice(error.message, "error"); } finally { setBusy(false); }
  }
  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value); if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  }
  function factList(object, skip = []) {
    if (!object || typeof object !== "object") return "";
    return `<dl class="fact-list">${Object.entries(object).filter(([key, value]) => !skip.includes(key) && value !== undefined && value !== null).map(([key, value]) => `<div><dt>${esc(fieldNames[key] || key)}</dt><dd>${esc(Array.isArray(value) ? value.map(display).join("、") || "—" : display(value))}</dd></div>`).join("")}</dl>`;
  }
  function renderDatasetError(message) { $("dataset-content").innerHTML = `<div class="notice error">${esc(message)}</div>`; }
  function renderDataset() {
    const d = state.dataset || {}; const sourceUrl = safeHref(d.source_url);
    const years = arr(d.years); const cities = arr(d.cities); const provinces = arr(d.provinces);
    const facts = [ ["有效记录", numeric(d.rows) ? d.rows : null, "条"], ["覆盖城市", cities.length ? cities.length : null, "座"], ["覆盖年份", years.length ? years.length : null, "年"], ["覆盖省份", provinces.length ? provinces.length : null, "个"] ];
    const quality = d.quality && typeof d.quality === "object" ? d.quality : {};
    $("dataset-content").innerHTML = `<div class="dataset-banner"><div><span class="section-kicker">DATA PROVENANCE</span><h2>${esc(d.name || "本地空气质量数据")}</h2>${sourceUrl ? `<a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">${esc(d.source_url)} ↗</a>` : `<span class="data-caption">${esc(d.source_url || "尚未提供来源地址")}</span>`}</div><span class="source-badge">${state.status?.ready ? "已就绪" : "等待导入"}</span></div><div class="dataset-facts">${facts.map(([label, value, unit]) => `<div class="dataset-fact"><span>${label}</span><strong>${value === null ? "—" : esc(number(value, 0))}<small>${value === null ? "" : unit}</small></strong></div>`).join("")}</div><div class="data-detail-grid"><section class="panel"><div class="panel-header"><div><span class="section-kicker">COVERAGE</span><h3>时间与城市范围</h3></div></div><div class="data-body">${factList({ date_min: d.date_min || "—", date_max: d.date_max || "—", years, provinces })}<div class="tag-list" style="margin-top:15px">${cities.map((city) => `<span>${esc(typeof city === "string" ? city : display(city))}</span>`).join("") || "暂未读取到可用城市"}</div></div></section><section class="panel"><div class="panel-header"><div><span class="section-kicker">DATA QUALITY</span><h3>清洗与质量记录</h3></div></div><div class="data-body">${Object.keys(quality).length ? factList(quality) : "接口尚未提供清洗统计。"}<div class="data-caption">质量统计来自本地数据接口。缺测不补零，具体分析的筛选与覆盖警告随结果展示。</div></div></section></div><section class="panel"><div class="panel-header"><div><span class="section-kicker">REPRODUCIBILITY</span><h3>来源文件与校验</h3></div></div><div class="data-body">${arr(d.files).length ? `<div class="tag-list">${d.files.map((file) => `<span>${esc(typeof file === "string" ? file : file.name || file.path || display(file))}</span>`).join("")}</div>` : "尚未读取到来源文件。"}${d.sha256 ? `<div class="data-caption">SHA-256<br><span class="hash-text">${esc(d.sha256)}</span></div>` : ""}<details style="margin-top:16px"><summary>数据解释与使用范围</summary><p class="data-caption">统计范围由实际文件、日期和有效观测决定。月度均值与年均值按本地工具返回的统计口径解释；城市或监测站覆盖不一致时，应结合有效样本量阅读。标准适用说明见下方及每次分析结果。DeepSeek 选择工具并解读观测，本地工具执行实际计算；统计结论应以工具返回的数值与适用口径为依据。</p></details></div></section>`;
  }
  async function loadDataset() {
    const button = $("refresh-data"); button.disabled = true;
    try { const dataset = await api("/api/dataset"); state.dataset = dataset; if (state.status) state.status.dataset = dataset; updateStatus(); renderDataset(); } catch (error) { renderDatasetError(error.message); } finally { button.disabled = false; }
  }
  async function loadStandards() {
    try {
      const response = await api("/api/standards"); state.standards = response;
      const standards = arr(response.standards);
      $("standards-content").innerHTML = (response.note ? `<p class="standards-disclaimer">${esc(response.note)}</p>` : "") + (standards.length ? `<div class="standards-grid">${standards.map((standard) => {
        if (typeof standard !== "object" || standard === null) return `<article class="standard-card"><h4>${esc(standard)}</h4></article>`;
        const url = safeHref(standard.url || standard.source_url);
        return `<article class="standard-card"><h4>${esc(standard.pollutant || standard.name || "标准条目")}</h4>${factList(standard, ["url", "source_url", standard.pollutant ? "pollutant" : "name"])}${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看标准来源 ↗</a>` : ""}</article>`;
      }).join("")}</div>` : `<div class="data-body">接口尚未返回标准条目。各次分析的适用标准会在结果明细中展示。</div>`);
    } catch (error) { $("standards-content").innerHTML = `<div class="data-body">${esc(error.message)}</div>`; }
  }

  $("query-form").addEventListener("submit", (event) => { event.preventDefault(); runAnalysis($("query-input").value); });
  $("query-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) { event.preventDefault(); runAnalysis($("query-input").value); } });
  $("followup-button").addEventListener("click", () => useQuery($("followup-button").dataset.query));
  function newSession() {
    if (state.busy) return;
    state.sessionId = createSessionId(); state.result = null; state.analyses = []; state.conversation = []; state.liveTools = [];
    $("results").hidden = true; $("live-process").hidden = true; $("query-input").value = "";
    renderConversation(); text("session-label", "新的分析会话"); notice("新的分析会话已就绪，写下下一个想了解的问题。"); $("query-input").focus();
  }
  $("new-session").addEventListener("click", newSession);
  $("new-conversation").addEventListener("click", newSession);
  $("refresh-history").addEventListener("click", () => loadHistory());
  $("refresh-data").addEventListener("click", async () => { await loadDataset(); await loadStatus(); await loadStandards(); });
  window.addEventListener("hashchange", route);
  window.addEventListener("scroll", hideTooltip, { passive: true });
  window.addEventListener("resize", () => { hideTooltip(); if (state.result) { const length = arr(state.analyses[state.activeAnalysis]?.metrics).length; $("metrics").style.gridTemplateColumns = window.innerWidth > 560 && length > 0 && length < 4 ? `repeat(${length},minmax(0,1fr))` : ""; } });
  text("today", new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).replace(/\//g, "."));
  $("run-button").disabled = true;
  loadConversation();
  if (state.conversation.length) text("session-label", "已恢复当前会话 · 可以继续追问");
  route();
  Promise.allSettled([loadStatus(), loadStandards()]);
})();
