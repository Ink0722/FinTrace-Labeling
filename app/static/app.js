const state = {
  sessions: [],
  cases: [],
  current: null,
  chunkVersion: null,
  isRendering: false,
  autoSaveTimer: null,
  saveInFlight: false,
  pendingSave: false,
  initialCaseId: new URLSearchParams(window.location.search).get("case_id"),
  initialAnnotator: new URLSearchParams(window.location.search).get("annotator"),
};

const $ = (id) => document.getElementById(id);

function daysInMonth(year, month) {
  return new Date(Number(year), Number(month), 0).getDate();
}

function listPlaceholder(listId) {
  if (listId === "required_entities") return "如 600519.SH";
  if (listId === "required_chunk_ids") return "如 ANN-205442600-C001";
  return "";
}

function addListInput(listId, value = "") {
  const list = $(listId);
  const row = document.createElement("div");
  row.className = "multi-row";

  const input = document.createElement("input");
  input.value = value;
  input.placeholder = listPlaceholder(listId);
  input.addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text");
    if (!text || !/[\r\n]/.test(text)) return;
    event.preventDefault();
    input.value = text.split(/\r?\n/)[0].trim();
    scheduleAutoSave();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addListInput(listId);
    const inputs = $(listId).querySelectorAll("input");
    inputs[inputs.length - 1].focus();
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-item";
  remove.textContent = "-";
  remove.addEventListener("click", () => {
    row.remove();
    ensureListInput(listId);
    scheduleAutoSave();
  });

  row.append(input, remove);
  list.append(row);
}

function ensureListInput(listId) {
  if ($(listId).querySelectorAll("input").length === 0) {
    addListInput(listId);
  }
}

function setListValues(listId, values) {
  $(listId).innerHTML = "";
  const items = Array.isArray(values) && values.length > 0 ? values : [""];
  items.forEach((value) => addListInput(listId, value));
}

function getListValues(listId) {
  return [...$(listId).querySelectorAll("input")]
    .map((input) => input.value.trim())
    .filter(Boolean);
}

function refreshDayOptions(selected = "") {
  if (!$("date_year") || !$("date_month") || !$("date_day")) return;
  const year = $("date_year").value.trim();
  const month = $("date_month").value;
  const maxDay = year && month ? daysInMonth(year, month) : 31;
  $("date_day").innerHTML =
    `<option value="">日</option>` +
    Array.from({ length: maxDay }, (_, index) => {
      const day = String(index + 1).padStart(2, "0");
      return `<option value="${day}">${day}</option>`;
    }).join("");
  if (selected && Number(selected) <= maxDay) {
    $("date_day").value = selected;
  }
}

function setDateParts(value) {
  if (!$("date_year") || !$("date_month") || !$("date_day")) return;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  $("date_year").value = match ? match[1] : "";
  $("date_month").value = match ? match[2] : "";
  refreshDayOptions(match ? match[3] : "");
}

function buildRequiredDate() {
  if (!$("date_year") || !$("date_month") || !$("date_day")) return null;
  const year = $("date_year").value.trim();
  const month = $("date_month").value;
  const day = $("date_day").value;
  if (!year && !month && !day) return null;
  if (!/^\d{4}$/.test(year)) {
    throw new Error("Required Date 的年份需要填写 4 位数字");
  }
  if (!month) return `${year}-12-31`;
  if (!day) {
    return `${year}-${month}-${String(daysInMonth(year, month)).padStart(2, "0")}`;
  }
  return `${year}-${month}-${day}`;
}

function formatBeijingTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day} ${map.hour}:${map.minute}:${map.second}`;
}

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败：${res.status}`);
  }
  return res.json();
}

async function loadStats() {
  const stats = await request("/api/stats");
  const done = stats.by_status.completed || 0;
  const review = stats.by_status.review_required || 0;
  const pending = stats.by_status.pending || 0;
  $("stats").textContent = `共 ${stats.total} 条，完成 ${done}，复核 ${review}，待标 ${pending}`;
}

async function loadSessions() {
  state.sessions = await request("/api/sessions");
  $("sessionSelect").innerHTML =
    `<option value="">全部 Session</option>` +
    state.sessions
      .map(
        (s) =>
          `<option value="${s.session_id}">Session ${s.session_id} (${s.completed}/${s.total})</option>`
      )
      .join("");
}

async function loadCases() {
  const params = new URLSearchParams();
  if ($("sessionSelect").value) params.set("session_id", $("sessionSelect").value);
  if ($("statusSelect").value) params.set("status", $("statusSelect").value);
  if ($("searchInput").value.trim()) params.set("q", $("searchInput").value.trim());
  state.cases = await request(`/api/cases?${params.toString()}`);
  renderCaseList();
  updateNavState();
}

function renderCaseList() {
  $("caseList").innerHTML = state.cases
    .map(
      (item) => `
        <button type="button" class="case-item ${state.current?.case_id === item.case_id ? "active" : ""}" data-case-id="${item.case_id}">
          <strong>${item.case_id}</strong>
          <p>${escapeHtml(item.question)}</p>
          <span class="badge ${item.annotation_status}">${item.annotation_status}</span>
        </button>
      `
    )
    .join("");
  document.querySelectorAll(".case-item").forEach((btn) => {
    btn.addEventListener("click", () => selectCase(btn.dataset.caseId));
  });
}

function currentIndex() {
  if (!state.current) return -1;
  return state.cases.findIndex((item) => item.case_id === state.current.case_id);
}

function updateNavState() {
  const index = currentIndex();
  $("prevBtn").disabled = state.cases.length === 0 || index <= 0;
  $("nextBtn").disabled = state.cases.length === 0 || index === state.cases.length - 1;
  $("nextPendingBtn").disabled = !state.cases.some((item, itemIndex) => {
    return item.annotation_status === "pending" && itemIndex > Math.max(index, -1);
  });
}

async function selectCase(caseId) {
  if (state.current && state.current.case_id !== caseId && state.autoSaveTimer) {
    clearTimeout(state.autoSaveTimer);
    await persistCurrent({ auto: true });
  } else {
    clearTimeout(state.autoSaveTimer);
  }
  const detail = await request(`/api/cases/${caseId}`);
  state.current = detail.case;
  renderCaseList();
  state.isRendering = true;
  renderDetail(detail);
  state.isRendering = false;
  updateNavState();
}
function renderDetail(detail) {
  const item = detail.case;
  $("caseId").textContent = item.case_id;
  const by = item.annotator ? ` · by ${item.annotator}` : "";
  $("caseMeta").textContent = `Session ${item.session_id} · Turn ${item.turn_id} · 更新于 ${formatBeijingTime(item.updated_at)}（北京时间）${by}`;
  $("thinkFlag").textContent = `think_flag: ${item.think_flag}`;
  $("question").textContent = item.question;
  $("annotation_status").value = item.annotation_status;
  $("answerability").value = item.answerability || "";
  setDateParts(item.required_date);
  setListValues("required_entities", item.required_entities);
  setListValues("required_chunk_ids", item.required_chunk_ids);
  state.chunkVersion = item.chunk_version || null;
  $("chunkVersionLabel").textContent = state.chunkVersion ? `版本：${state.chunkVersion}` : "未选择版本";
  $("notes").value = item.notes || "";
  if (item.annotator && !$("annotator").value) $("annotator").value = item.annotator;

  document.querySelectorAll("input[name=tool]").forEach((input) => {
    input.checked = item.valid_tools.includes(input.value);
  });

  $("previousTurns").innerHTML =
    detail.previous.length === 0
      ? `<div class="empty">当前 Turn 之前没有上下文。</div>`
      : detail.previous
          .map(
            (turn) => `
              <article class="turn">
                <small>Turn ${turn.turn_id} · ${turn.annotation_status}</small>
                <p>${escapeHtml(turn.question)}</p>
              </article>
            `
          )
          .join("");
}

function buildAnnotationPayload() {
  const validTools = [...document.querySelectorAll("input[name=tool]:checked")].map((input) => input.value);
  const chunkIds = getListValues("required_chunk_ids");
  return {
    annotation_status: $("annotation_status").value,
    answerability: $("answerability").value || null,
    required_entities: getListValues("required_entities"),
    required_date: buildRequiredDate(),
    valid_tools: validTools,
    required_chunk_ids: chunkIds,
    chunk_version: chunkIds.length > 0 ? state.chunkVersion : null,
    annotator: $("annotator").value.trim(),
    notes: $("notes").value.trim() || null,
  };
}

async function persistCurrent({ auto = false } = {}) {
  if (!state.current) return;
  const annotator = $("annotator").value.trim();
  if (!annotator) {
    $("saveState").textContent = auto ? "未自动保存：请先填写标注员 ID" : "保存失败：请先填写标注员 ID";
    if (!auto) $("annotator").focus();
    return;
  }
  if (state.saveInFlight) {
    state.pendingSave = true;
    return;
  }

  state.saveInFlight = true;
  state.pendingSave = false;
  localStorage.setItem("fintrace_annotator", annotator);
  $("saveState").textContent = auto ? "自动保存中..." : "保存中...";

  try {
    const caseId = state.current.case_id;
    const payload = buildAnnotationPayload();
    state.current = await request(`/api/cases/${caseId}/annotation`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    $("saveState").textContent = auto ? "已自动保存" : "已保存";
    await loadStats();
    await loadCases();
  } catch (err) {
    $("saveState").textContent = auto ? `自动保存失败：${err.message}` : err.message;
  } finally {
    state.saveInFlight = false;
    if (state.pendingSave) {
      state.pendingSave = false;
      scheduleAutoSave();
    }
  }
}

function scheduleAutoSave() {
  if (state.isRendering || !state.current) return;
  clearTimeout(state.autoSaveTimer);
  $("saveState").textContent = "有修改，准备自动保存...";
  state.autoSaveTimer = setTimeout(() => {
    state.autoSaveTimer = null;
    persistCurrent({ auto: true });
  }, 700);
}

async function saveCurrent(event) {
  event.preventDefault();
  clearTimeout(state.autoSaveTimer);
  await persistCurrent({ auto: false });
}
function move(offset) {
  if (state.cases.length === 0) {
    $("saveState").textContent = "当前筛选条件下没有题目";
    return;
  }
  if (!state.current) {
    const fallback = offset > 0 ? state.cases[0] : state.cases[state.cases.length - 1];
    selectCase(fallback.case_id);
    return;
  }
  const index = currentIndex();
  if (index === -1) {
    selectCase(state.cases[0].case_id);
    return;
  }
  const next = state.cases[index + offset];
  if (next) {
    selectCase(next.case_id);
  } else {
    $("saveState").textContent = offset > 0 ? "已经是当前列表最后一条" : "已经是当前列表第一条";
    updateNavState();
  }
}

function nextPending() {
  if (state.cases.length === 0) {
    $("saveState").textContent = "当前筛选条件下没有题目";
    return;
  }
  const from = state.current
    ? Math.max(0, currentIndex() + 1)
    : 0;
  const next = state.cases.slice(from).find((item) => item.annotation_status === "pending");
  if (next) {
    selectCase(next.case_id);
  } else {
    $("saveState").textContent = "后面没有 pending 题目";
    updateNavState();
  }
}

async function exportJsonl() {
  $("saveState").textContent = "导出中...";
  const data = await request("/api/export/jsonl", { method: "POST" });
  $("saveState").textContent = `已导出：${data.path}`;
}

function openChunksDashboard() {
  const annotator = $("annotator").value.trim();
  if (!annotator) {
    $("saveState").textContent = "请先填写标注员 ID，再打开 Chunk Dashboard";
    $("annotator").focus();
    return;
  }
  localStorage.setItem("fintrace_annotator", annotator);
  const target = state.current
    ? `/chunks.html?case_id=${encodeURIComponent(state.current.case_id)}&annotator=${encodeURIComponent(annotator)}`
    : "/chunks.html";
  window.location.href = target;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function bindEvents() {
  $("annotationForm").addEventListener("submit", saveCurrent);
  $("annotationForm").addEventListener("input", scheduleAutoSave);
  $("annotationForm").addEventListener("change", scheduleAutoSave);
  $("sessionSelect").addEventListener("change", loadCases);
  $("statusSelect").addEventListener("change", loadCases);
  $("searchInput").addEventListener("input", () => {
    clearTimeout(window.searchTimer);
    window.searchTimer = setTimeout(loadCases, 250);
  });
  $("prevBtn").addEventListener("click", () => move(-1));
  $("nextBtn").addEventListener("click", () => move(1));
  $("nextPendingBtn").addEventListener("click", nextPending);
  $("openChunksBtn").addEventListener("click", openChunksDashboard);
  $("exportBtn").addEventListener("click", exportJsonl);
  if ($("date_year") && $("date_month") && $("date_day")) {
    $("date_year").addEventListener("input", () => {
      refreshDayOptions($("date_day").value);
      scheduleAutoSave();
    });
    $("date_month").addEventListener("change", () => {
      refreshDayOptions($("date_day").value);
      scheduleAutoSave();
    });
  }
  document.querySelectorAll(".add-item").forEach((button) => {
    button.addEventListener("click", () => {
      addListInput(button.dataset.target);
      scheduleAutoSave();
    });
  });
}

async function init() {
  bindEvents();
  const savedAnnotator = state.initialAnnotator || localStorage.getItem("fintrace_annotator") || "";
  $("annotator").value = savedAnnotator;
  if (state.initialAnnotator) localStorage.setItem("fintrace_annotator", state.initialAnnotator);
  refreshDayOptions();
  setListValues("required_entities", []);
  setListValues("required_chunk_ids", []);
  await loadStats();
  await loadSessions();
  await loadCases();
  if (state.initialCaseId) {
    await selectCase(state.initialCaseId);
  } else if (state.cases[0]) {
    await selectCase(state.cases[0].case_id);
  }
}

init().catch((err) => {
  $("saveState").textContent = err.message;
});
