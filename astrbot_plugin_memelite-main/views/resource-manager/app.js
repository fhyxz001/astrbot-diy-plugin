const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);

const PHASE_LABEL = {
  idle: "就绪",
  manifest: "获取资源清单",
  scan: "校验本地文件",
  download: "下载中",
  done: "完成",
  error: "失败",
  cancelled: "已停止",
};

const MAX_ROWS = 200;
const PREVIEW_LIMIT = 24;

let overview = null;
let status = null;
let filter = "all";
let lastRunning = false;
let polling = false;
let preview = { offset: 0, total: 0, keyword: "", loading: false };
let lightboxSeq = 0;

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("is-error", isError);
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

function errorText(error) {
  if (!error) return "未知错误";
  const message = error.message || String(error);
  return message.replace(/^Error:\s*/, "");
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    units.length - 1,
    Math.floor(Math.log(value) / Math.log(1024))
  );
  const size = value / Math.pow(1024, index);
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function fillStat(kind, countId, sizeId) {
  const stat = overview?.stats?.[kind];
  if (!stat) return;
  $(countId).textContent = `${stat.files} 个文件`;
  $(sizeId).textContent = `占用 ${formatBytes(stat.size)}`;
}

function updatePills() {
  const kinds = status?.missing_kinds || null;
  const scanned = Boolean(status?.scanned);

  const setPill = (id, kind) => {
    const pill = $(id);
    pill.classList.remove("ok", "bad");
    if (!scanned) {
      pill.textContent = "未扫描";
      return;
    }
    const missing = Number(kinds?.[kind] || 0);
    if (missing > 0) {
      pill.textContent = `缺失 ${missing}`;
      pill.classList.add("bad");
    } else {
      pill.textContent = "完整";
      pill.classList.add("ok");
    }
  };

  setPill("imgState", "images");
  setPill("fontState", "fonts");
}

function renderOverview() {
  if (!overview) return;

  $("flavorBadge").textContent = overview.available
    ? overview.flavor === "rust"
      ? "meme_generator · Rust"
      : "meme_generator · Python"
    : "meme_generator 未安装";
  $("versionBadge").textContent = `v${overview.version || "–"}`;

  fillStat("images", "imgCount", "imgSize");
  fillStat("fonts", "fontCount", "fontSize");

  $("resourceRoot").textContent = overview.resource_root || "–";
  $("startCheck").textContent = `启动检查：${
    overview.check_on_start ? "开启" : "关闭"
  }`;

  syncMirrorSelect();
  updatePills();
}

function syncMirrorSelect() {
  const select = $("mirrorSelect");
  const mirrors = overview?.mirrors || [];
  const current = overview?.mirror || "";

  select.innerHTML = "";

  const auto = document.createElement("option");
  auto.value = "__auto__";
  auto.textContent = "自动（依次尝试）";
  select.appendChild(auto);

  for (const mirror of mirrors) {
    const option = document.createElement("option");
    option.value = mirror.base;
    option.textContent = mirror.label;
    select.appendChild(option);
  }

  const custom = document.createElement("option");
  custom.value = "__custom__";
  custom.textContent = "自定义…";
  select.appendChild(custom);

  if (!current) {
    select.value = "__auto__";
    $("mirrorInput").value = "";
  } else if (mirrors.some((mirror) => mirror.base === current)) {
    select.value = current;
    $("mirrorInput").value = "";
  } else {
    select.value = "__custom__";
    $("mirrorInput").value = current;
  }

  updateCustomInput();
}

function updateCustomInput() {
  const isCustom = $("mirrorSelect").value === "__custom__";
  $("mirrorInput").hidden = !isCustom;
  if (isCustom) $("mirrorInput").focus();
}

function phaseClass(phase) {
  if (phase === "error") return "phase is-error";
  if (phase === "done") return "phase is-done";
  return "phase";
}

function renderStatus() {
  if (!status) return;

  const phase = status.phase || "idle";
  const running = Boolean(status.running);

  $("phaseText").className = phaseClass(phase);
  $("phaseText").textContent =
    status.message && phase !== "idle"
      ? `${PHASE_LABEL[phase] || phase} · ${status.message}`
      : PHASE_LABEL[phase] || phase;

  let counter = "–";
  if (
    (phase === "scan" || phase === "download") &&
    Number(status.total) > 0
  ) {
    counter = `${status.completed} / ${status.total}`;
  } else if (status.missing_total > 0) {
    counter = `缺失 ${status.missing_total} 个`;
  } else if (phase === "done") {
    counter = "已完成";
  }
  $("progressCount").textContent = counter;

  const bar = $("barWrap");
  const fill = $("barFill");
  const indeterminate =
    phase === "manifest" || (phase === "download" && Number(status.total) === 0);
  bar.classList.toggle("is-indeterminate", indeterminate);
  if (!indeterminate) {
    const total = Number(status.total) || 0;
    const completed = Number(status.completed) || 0;
    const percent =
      total > 0
        ? Math.min(100, Math.round((completed / total) * 100))
        : phase === "done"
          ? 100
          : 0;
    fill.style.width = `${percent}%`;
  }

  $("currentFile").textContent = status.current || "";

  renderErrors();

  const idle = !running;
  $("scanBtn").disabled = !idle;
  $("downloadBtn").disabled = !idle;
  $("mirrorSaveBtn").disabled = !idle;
  $("cancelBtn").hidden = idle;

  renderMissing();
  updatePills();
}

function renderErrors() {
  const errors = status?.errors || [];
  const box = $("errorBox");
  const list = $("errorList");
  list.innerHTML = "";

  if (!errors.length) {
    box.hidden = true;
    return;
  }

  for (const error of errors) {
    const item = document.createElement("li");
    item.textContent = `${error.file} — ${error.reason}`;
    list.appendChild(item);
  }
  box.hidden = false;
}

function renderMissing() {
  const card = $("missingCard");
  const rows = (status?.missing || []).filter(
    (item) => filter === "all" || item.kind === filter
  );
  const total = Number(status?.missing_total) || 0;

  if (total <= 0) {
    card.hidden = true;
    return;
  }

  card.hidden = false;
  $("missingTotal").textContent = total;

  const body = $("missingBody");
  body.innerHTML = "";

  for (const item of rows.slice(0, MAX_ROWS)) {
    const tr = document.createElement("tr");
    const kindCell = document.createElement("td");
    kindCell.className = "kind-cell";
    kindCell.textContent = item.kind === "images" ? "图片" : "字体";
    const fileCell = document.createElement("td");
    fileCell.textContent = item.file;
    tr.append(kindCell, fileCell);
    body.appendChild(tr);
  }

  const note = $("missingMore");
  if (rows.length > MAX_ROWS) {
    note.textContent = `共 ${rows.length} 条，仅展示前 ${MAX_ROWS} 条`;
    note.hidden = false;
  } else if (status.missing_truncated) {
    note.textContent = `共 ${total} 条，仅展示前 ${status.missing.length} 条`;
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

function renderPreview(data) {
  const items = data?.items || [];
  const total = Number(data?.total) || 0;
  const offset = Number(data?.offset) || 0;
  const limit = Number(data?.limit) || PREVIEW_LIMIT;
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const pageIndex = Math.min(pageCount, Math.floor(offset / limit) + 1);

  $("previewTotal").textContent = total;
  $("previewPage").textContent = `${pageIndex} / ${pageCount}`;
  $("previewPrev").disabled = offset <= 0;
  $("previewNext").disabled = offset + limit >= total;

  const grid = $("previewGrid");
  grid.innerHTML = "";

  const empty = $("previewEmpty");
  empty.textContent = preview.keyword ? "没有匹配的底图" : "暂无底图，请先下载资源";
  empty.hidden = items.length > 0;

  for (const item of items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "thumb";
    card.title = `${item.file} · ${formatBytes(item.size)}`;

    const img = document.createElement("img");
    img.src = item.thumb;
    img.alt = item.file;
    img.loading = "lazy";

    const label = document.createElement("span");
    label.className = "thumb-label";
    label.textContent = item.file;

    card.append(img, label);
    card.addEventListener("click", () => openLightbox(item));
    grid.appendChild(card);
  }
}

async function loadPreview(resetOffset = false) {
  if (preview.loading) return;
  if (resetOffset) preview.offset = 0;

  preview.loading = true;
  try {
    const data = await bridge.apiGet("images", {
      offset: preview.offset,
      limit: PREVIEW_LIMIT,
      keyword: preview.keyword,
    });
    preview.total = Number(data?.total) || 0;
    preview.offset = Number(data?.offset) || 0;
    renderPreview(data);
  } catch (error) {
    toast(`加载底图失败：${errorText(error)}`, true);
  } finally {
    preview.loading = false;
  }
}

async function openLightbox(item) {
  const seq = ++lightboxSeq;
  $("lightboxName").textContent = item.file;
  $("lightboxSize").textContent = formatBytes(item.size);
  $("lightboxImg").src = item.thumb;
  $("lightbox").hidden = false;
  document.body.classList.add("no-scroll");

  try {
    const detail = await bridge.apiGet("image", { file: item.file });
    if (seq === lightboxSeq && detail?.data) {
      $("lightboxImg").src = detail.data;
    }
  } catch (error) {
    toast(errorText(error), true);
  }
}

function closeLightbox() {
  lightboxSeq += 1;
  $("lightbox").hidden = true;
  $("lightboxImg").src = "";
  document.body.classList.remove("no-scroll");
}

async function loadOverview() {
  overview = await bridge.apiGet("overview");
  renderOverview();
}

async function refreshStatus() {
  const data = await bridge.apiGet("progress");
  status = data.task || data;
  renderStatus();

  if (lastRunning && !status.running) {
    lastRunning = false;
    try {
      await loadOverview();
      await loadPreview(true);
    } catch (error) {
      console.error(error);
    }
    return;
  }
  lastRunning = Boolean(status.running);
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    await refreshStatus();
  } catch (error) {
    console.error(error);
  } finally {
    polling = false;
  }
}

async function startJob(endpoint) {
  try {
    const result = await bridge.apiPost(endpoint, {
      mirror: overview?.mirror || "",
    });
    if (result?.task) {
      status = result.task;
      lastRunning = true;
      renderStatus();
    }
  } catch (error) {
    toast(errorText(error), true);
  }
}

async function cancelJob() {
  try {
    await bridge.apiPost("cancel", {});
    await refreshStatus();
  } catch (error) {
    toast(errorText(error), true);
  }
}

async function saveMirror() {
  const select = $("mirrorSelect");
  const value =
    select.value === "__custom__"
      ? $("mirrorInput").value.trim()
      : select.value === "__auto__"
        ? ""
        : select.value;

  try {
    const result = await bridge.apiPost("mirror", { mirror: value });
    overview.mirror = result.mirror || "";
    toast("镜像设置已保存");
  } catch (error) {
    toast(errorText(error), true);
  }
}

function bindEvents() {
  $("scanBtn").addEventListener("click", () => startJob("scan"));
  $("downloadBtn").addEventListener("click", () => startJob("download"));
  $("cancelBtn").addEventListener("click", cancelJob);
  $("mirrorSaveBtn").addEventListener("click", saveMirror);
  $("mirrorSelect").addEventListener("change", updateCustomInput);
  $("mirrorInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") saveMirror();
  });

  $("missingTabs").addEventListener("click", (event) => {
    const button = event.target.closest(".tab");
    if (!button) return;
    filter = button.dataset.filter;
    for (const tab of $("missingTabs").querySelectorAll(".tab")) {
      tab.classList.toggle("is-active", tab === button);
    }
    renderMissing();
  });

  $("previewPrev").addEventListener("click", () => {
    preview.offset = Math.max(0, preview.offset - PREVIEW_LIMIT);
    loadPreview();
  });
  $("previewNext").addEventListener("click", () => {
    preview.offset += PREVIEW_LIMIT;
    loadPreview();
  });

  let searchTimer;
  $("previewSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      preview.keyword = $("previewSearch").value.trim();
      loadPreview(true);
    }, 260);
  });

  $("lightboxClose").addEventListener("click", closeLightbox);
  $("lightbox").addEventListener("click", (event) => {
    if (event.target === $("lightbox")) closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("lightbox").hidden) closeLightbox();
  });
}

async function init() {
  if (!bridge) {
    toast("未检测到面板桥接，请升级 AstrBot 后重试", true);
    return;
  }

  try {
    const context = await bridge.ready();
    if (context?.isDark) document.documentElement.dataset.theme = "dark";
  } catch (error) {
    console.error(error);
  }

  bindEvents();

  try {
    await loadOverview();
  } catch (error) {
    toast(`加载资源信息失败：${errorText(error)}`, true);
  }

  await loadPreview(true);

  await poll();
  setInterval(poll, 1000);
}

init();
