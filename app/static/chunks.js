const state = {
  versions: [],
  versionId: null,
  caseId: new URLSearchParams(window.location.search).get("case_id"),
  annotator: new URLSearchParams(window.location.search).get("annotator") || localStorage.getItem("fintrace_annotator") || "",
  page: 1,
  pageSize: 20,
  total: 0,
  current: null,
  case: null,
};

const $ = (id) => document.getElementById(id);

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

async function loadVersions() {
  state.versions = await request("/api/chunk-versions");
  $("versionSelect").innerHTML =
    state.versions.length === 0
      ? `<option value="">尚未导入 chunk</option>`
      : state.versions
          .map((item) => {
            const active = item.is_active ? " active" : "";
            return `<option value="${escapeAttr(item.version_id)}">${escapeHtml(item.name || item.version_id)}${active}</option>`;
          })
          .join("");
  const active = state.versions.find((item) => item.is_active) || state.versions[0];
  if (active) {
    state.versionId = active.version_id;
    $("versionSelect").value = active.version_id;
  }
}

async function search(resetPage = true) {
  if (resetPage) state.page = 1;
  const params = new URLSearchParams();
  if (state.versionId) params.set("version_id", state.versionId);
  if ($("chunkQuery").value.trim()) params.set("q", $("chunkQuery").value.trim());
  if ($("companyIdFilter").value.trim()) params.set("company_id", $("companyIdFilter").value.trim());
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));

  $("resultSummary").textContent = "搜索中...";
  $("searchBtn").disabled = true;
  $("searchBtn").textContent = "搜索中...";
  setChunkState("搜索中...", "pending");
  try {
    const data = await request(`/api/chunks?${params.toString()}`);
    state.total = data.total;
    state.versionId = data.version_id || state.versionId;
    renderResults(data.items);
    renderPaging();
    setChunkState("");
  } catch (err) {
    $("chunkResults").innerHTML = `<div class="empty">搜索失败：${escapeHtml(err.message)}</div>`;
    $("resultSummary").textContent = "搜索失败";
    setChunkState(`搜索失败：${err.message}`, "error");
  } finally {
    $("searchBtn").disabled = false;
    $("searchBtn").textContent = "搜索";
  }
}
function renderResults(items) {
  const terms = searchTerms();
  if (items.length === 0) {
    $("chunkResults").innerHTML = `<div class="empty">没有匹配结果。</div>`;
    return;
  }
  $("chunkResults").innerHTML = items
    .map(
      (item) => `
        <article class="chunk-card ${state.current?.chunk_id === item.chunk_id ? "active" : ""}">
          <strong>${highlight(item.chunk_id, terms)}</strong>
          <button type="button" class="chunk-open" data-version-id="${escapeAttr(item.version_id)}" data-chunk-id="${escapeAttr(item.chunk_id)}">
            <small>${highlight([item.document_id || "", item.company_id || "", item.published_date || "", `#${item.chunk_index || ""}`].filter(Boolean).join(" · "), terms)}</small>
            <em>${highlight(item.title || item.section_title || "无标题", terms)}</em>
            <p>${highlight(item.snippet || "", terms)}</p>
          </button>
        </article>
      `
    )
    .join("");
  document.querySelectorAll(".chunk-open").forEach((button) => {
    button.addEventListener("click", () => selectChunk(button.dataset.versionId, button.dataset.chunkId));
  });
}

function renderPaging() {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const keywords = searchTerms();
  const companyCode = companyCodeFilter();
  const filters = [];
  if (companyCode) filters.push(`证券代码=${companyCode}`);
  if (keywords.length > 0) filters.push(`关键词=${keywords.join(" / ")}`);
  const mode = filters.length > 0 ? `筛选结果（${filters.join(" · ")}）` : "当前版本全部 chunk";
  $("resultSummary").textContent = `${mode}：共 ${state.total} 条 · 第 ${state.page}/${pages} 页`;
  $("prevPageBtn").disabled = state.page <= 1;
  $("nextPageBtn").disabled = state.page >= pages;
}
async function selectChunk(versionId, chunkId) {
  const item = await request(`/api/chunks/${encodeURIComponent(versionId)}/${encodeURIComponent(chunkId)}`);
  const terms = searchTerms();
  state.current = item;
  $("detailTitle").innerHTML = highlight(item.chunk_id, terms);
  const doc = item.document || {};
  $("detailMeta").innerHTML = highlight([
    item.document_id || "",
    doc.company_id || "",
    doc.published_date || "",
    doc.title || "",
    `#${item.chunk_index || ""}`,
    item.section_title || "无标题",
  ].filter(Boolean).join(" · "), terms);
  $("embeddingText").innerHTML = highlight(item.embedding_display || item.text, terms);
  $("detailText").innerHTML = highlight(item.text, terms);
  setChunkState("");
  updateAddButtonState();
  await loadDocumentChunks(item.version_id, item.document_id);
}

async function loadDocumentChunks(versionId, documentId) {
  if (!documentId) {
    $("documentChunks").innerHTML = "";
    return;
  }
  const items = await request(`/api/documents/${encodeURIComponent(versionId)}/${encodeURIComponent(documentId)}/chunks`);
  $("documentChunks").innerHTML = items
    .map(
      (item) => `
        <button type="button" class="doc-chunk ${state.current?.chunk_id === item.chunk_id ? "active" : ""}" data-version-id="${escapeAttr(item.version_id)}" data-chunk-id="${escapeAttr(item.chunk_id)}">
          #${item.chunk_index} ${escapeHtml(item.chunk_id)}
        </button>
      `
    )
    .join("");
  document.querySelectorAll(".doc-chunk").forEach((button) => {
    button.addEventListener("click", () => selectChunk(button.dataset.versionId, button.dataset.chunkId));
  });
}

async function copyChunkId() {
  if (!state.current) return;
  await navigator.clipboard.writeText(state.current.chunk_id);
  setChunkState("已复制 chunk_id", "success");
}

async function addChunkToCase() {
  if (!state.current || !state.caseId) return;
  if (!state.annotator.trim()) {
    setChunkState("保存失败：缺少标注员 ID，请从主标注页重新打开 Dashboard", "error");
    return;
  }
  setChunkState("保存中...", "pending");
  try {
    const item = await request(
      `/api/cases/${encodeURIComponent(state.caseId)}/chunks/${encodeURIComponent(state.current.version_id)}/${encodeURIComponent(state.current.chunk_id)}`,
      {
        method: "POST",
        body: JSON.stringify({ annotator: state.annotator.trim() }),
      }
    );
    state.case = item;
    renderCaseChunks();
    updateAddButtonState();
    setChunkState(`保存成功：已加入 ${state.current.chunk_id}`, "success");
  } catch (err) {
    setChunkState(`保存失败：${err.message}`, "error");
  }
}

async function loadCaseChunks() {
  if (!state.caseId) {
    state.case = null;
    renderCaseChunks();
    return;
  }
  try {
    const detail = await request(`/api/cases/${encodeURIComponent(state.caseId)}`);
    state.case = detail.case;
    renderCaseChunks();
    updateAddButtonState();
  } catch (err) {
    setChunkState(`读取当前标注失败：${err.message}`, "error");
  }
}

function renderCaseChunks() {
  if (!state.caseId) {
    $("caseChunksPanel").style.display = "none";
    return;
  }
  $("caseChunksPanel").style.display = "block";
  const ids = state.case?.required_chunk_ids || [];
  const version = state.case?.chunk_version || "未选择版本";
  const annotator = state.case?.annotator || state.annotator || "未指定";
  $("caseChunks").innerHTML =
    ids.length === 0
      ? `<div class="empty">尚未加入 chunk。版本：${escapeHtml(version)} · by ${escapeHtml(annotator)}</div>`
      : `<div class="case-chunk-version">版本：${escapeHtml(version)} · by ${escapeHtml(annotator)}</div>` +
        ids.map((id) => `<code class="case-chunk-id">${escapeHtml(id)}</code>`).join("");
}

function updateAddButtonState() {
  if (!state.caseId) return;
  const ids = state.case?.required_chunk_ids || [];
  const alreadyAdded = state.current && ids.includes(state.current.chunk_id);
  $("addChunkBtn").disabled = !state.current || alreadyAdded || !state.annotator.trim();
  $("addChunkBtn").textContent = alreadyAdded ? "已加入当前标注" : "加入当前标注";
}

function setChunkState(message, kind = "") {
  $("chunkState").textContent = message;
  $("chunkState").className = kind ? `status-text ${kind}` : "status-text";
}

function bindEvents() {
  $("versionSelect").addEventListener("change", () => {
    state.versionId = $("versionSelect").value;
    search(true);
  });
  $("searchBtn").addEventListener("click", () => search(true));
  $("chunkQuery").addEventListener("keydown", (event) => {
    if (event.key === "Enter") search(true);
  });
  $("companyIdFilter").addEventListener("keydown", (event) => {
    if (event.key === "Enter") search(true);
  });
  $("prevPageBtn").addEventListener("click", () => {
    state.page -= 1;
    search(false);
  });
  $("nextPageBtn").addEventListener("click", () => {
    state.page += 1;
    search(false);
  });
  $("copyChunkBtn").addEventListener("click", copyChunkId);
  $("addChunkBtn").addEventListener("click", addChunkToCase);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function searchTerms() {
  return $("chunkQuery").value
    .trim()
    .split(/[\s,，;；]+/)
    .filter(Boolean);
}

function companyCodeFilter() {
  const match = /^\s*(\d{6})/.exec($("companyIdFilter").value.trim());
  return match ? match[1] : "";
}
function highlight(text, terms) {
  let html = escapeHtml(text || "");
  terms.forEach((term) => {
    const escaped = escapeRegExp(escapeHtml(term));
    if (!escaped) return;
    html = html.replace(new RegExp(escaped, "gi"), (match) => `<mark>${match}</mark>`);
  });
  return html;
}

function escapeRegExp(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function escapeAttr(text) {
  return escapeHtml(text).replaceAll("'", "&#39;");
}

function updateBackLink() {
  const params = new URLSearchParams();
  if (state.caseId) params.set("case_id", state.caseId);
  if (state.annotator) params.set("annotator", state.annotator);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  document.querySelector(".back-link").href = `/${suffix}`;
}
async function init() {
  if (state.annotator) localStorage.setItem("fintrace_annotator", state.annotator);
  updateBackLink();
  $("caseInfo").textContent = state.caseId
    ? `当前 case：${state.caseId} · by ${state.annotator || "未指定"}`
    : "未绑定 case，可复制 chunk_id";
  $("addChunkBtn").style.display = state.caseId ? "inline-block" : "none";
  bindEvents();
  await loadVersions();
  await loadCaseChunks();
  await search(true);
}
init().catch((err) => {
  $("chunkState").textContent = err.message;
});
