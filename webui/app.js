"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  projects: [],
  current: null,
};

// ---------------- 基础 ----------------

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  let data = {};
  try { data = await resp.json(); } catch (_) {}
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function setStatus(text, kind = "") {
  const el = $("#status");
  el.textContent = text;
  el.className = "status " + kind;
}

function toast(text, kind = "info") {
  const el = $("#toast");
  el.textContent = text;
  el.className = `toast show ${kind}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "toast"; }, 3500);
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-body").forEach((b) => b.classList.toggle("active", b.id === "tab-" + name));
}

// ---------------- 配置 ----------------

async function loadConfig() {
  try {
    state.cfg = await api("GET", "/api/config");
  } catch (e) {
    state.cfg = {};
  }
  const c = state.cfg;
  $("#badge-llm").textContent = `LLM ${c.llm_key_set ? "✓" : "✗"}`;
  $("#badge-llm").className = "badge " + (c.llm_key_set ? "ok" : "warn");
  $("#badge-gh").textContent = `GitHub ${c.github_token_set ? "✓" : "✗"}`;
  $("#badge-gh").className = "badge " + (c.github_token_set ? "ok" : "warn");
  $("#ver").textContent = "v" + (c.version || "?");
  $("#cfg-model").value = c.llm_model || "";
  $("#cfg-base-url").value = c.llm_base_url || "";
  $("#cfg-lang").value = c.readme_lang || "auto";
  $("#cfg-verify-ssl").checked = c.verify_ssl === false;
  $("#cfg-proxy").value = c.proxy || "";
}

async function saveSettings() {
  try {
    await api("POST", "/api/config", {
      model: $("#cfg-model").value.trim(),
      base_url: $("#cfg-base-url").value.trim(),
      readme_lang: $("#cfg-lang").value,
      verify_ssl: !$("#cfg-verify-ssl").checked,
      proxy: $("#cfg-proxy").value.trim(),
    });
    $("#settings-modal").classList.add("hidden");
    toast("设置已保存（本次会话生效）", "ok");
  } catch (e) {
    toast("保存失败: " + e.message, "error");
  }
}

// ---------------- 扫描 ----------------

async function scan() {
  const root = $("#root-path").value.trim();
  if (!root) {
    setStatus("请先填写项目根目录", "err");
    return;
  }
  setStatus("正在扫描…");
  try {
    const data = await api("POST", "/api/scan", { root });
    state.projects = data.projects;
    renderProjects();
    setStatus(`发现 ${data.projects.length} 个项目`, "ok");
    if (data.projects.length) toast(`发现 ${data.projects.length} 个项目，点击左侧项目查看分析`, "ok");
  } catch (e) {
    setStatus("扫描失败: " + e.message, "err");
    toast(e.message, "error");
  }
}

function renderProjects() {
  const list = $("#project-list");
  list.innerHTML = "";
  $("#project-count").textContent = state.projects.length ? `(${state.projects.length})` : "";
  if (!state.projects.length) {
    list.innerHTML = '<div class="empty">未发现项目</div>';
    return;
  }
  state.projects.forEach((p) => {
    const el = document.createElement("div");
    el.className = "project-item";
    el.innerHTML = `<div class="p-name">${esc(p.name)}</div><div class="p-path">${esc(p.path)}</div>`;
    el.addEventListener("click", (ev) => selectProject(p, ev));
    list.appendChild(el);
  });
}

// ---------------- 分析 ----------------

async function selectProject(p, ev) {
  state.current = p;
  document.querySelectorAll(".project-item").forEach((e) => e.classList.remove("active"));
  ev.currentTarget.classList.add("active");
  setStatus(`正在分析 ${p.name} …`);
  try {
    const a = await api("GET", "/api/analyze?path=" + encodeURIComponent(p.path));
    p.analysis = a;
    renderAnalysis(a);
    setStatus(`已加载: ${p.name}`, "ok");
  } catch (e) {
    setStatus("分析失败: " + e.message, "err");
  }
}

function renderAnalysis(a) {
  $("#a-name").textContent = a.name || "—";
  $("#a-path").textContent = a.root || "";
  $("#a-files").textContent = a.total_files;
  $("#a-lines").textContent = (a.total_lines || 0).toLocaleString();
  $("#a-license").textContent = a.license || "未检测";
  $("#a-wf").textContent = (a.workflows || []).length ? a.workflows.join(", ") : "无";

  const langs = Object.entries(a.languages || {});
  const maxFiles = Math.max(1, ...langs.map(([, v]) => v.files));
  const bars = $("#lang-bars");
  bars.innerHTML = "";
  if (!langs.length) {
    bars.innerHTML = '<div class="muted">未检测到编程语言</div>';
  }
  langs.forEach(([lang, v]) => {
    const row = document.createElement("div");
    row.className = "lang-row";
    row.innerHTML =
      `<span class="lang-name">${esc(lang)}</span>` +
      `<div class="lang-bar"><div class="lang-fill" style="width:${(v.files / maxFiles) * 100}%"></div></div>` +
      `<span class="lang-count">${v.files} 文件 / ${v.lines.toLocaleString()} 行</span>`;
    bars.appendChild(row);
  });

  const md = a.metadata || {};
  const rows = [
    ["类型", md.type || "—"],
    ["名称", md.name || "—"],
    ["版本", md.version || "—"],
    ["描述", md.description || "—"],
    ["脚本", (md.scripts || []).join("；") || "—"],
    ["依赖", (md.dependencies || []).join(", ") || "—"],
  ];
  $("#md-table").innerHTML =
    "<table>" + rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("") + "</table>";

  $("#a-tree").textContent = a.tree || "—";
  const git = a.git || {};
  const gitLines = [];
  if (git.remote) gitLines.push(`remote: ${git.remote}`);
  if (git.branch) gitLines.push(`branch: ${git.branch}`);
  if (git.recent_commits) gitLines.push("commits:", ...git.recent_commits.map((c) => "  " + c));
  $("#a-git").textContent = gitLines.length ? gitLines.join("\n") : "项目不是 git 仓库";
}

// ---------------- 任务与日志 ----------------

function appendLog(line) {
  const el = document.createElement("div");
  el.className = "log-line " + (line.level || "INFO");
  el.innerHTML =
    `<span class="ts">${esc(line.ts || "")}</span>` +
    `<span class="lvl">${esc(line.level || "")}</span>` +
    `<span class="msg">${esc(line.msg)}</span>`;
  const console = $("#log-console");
  console.appendChild(el);
  console.scrollTop = console.scrollHeight;
}

function runJob(jobId) {
  return new Promise((resolve, reject) => {
    let seen = 0;
    const poll = async () => {
      try {
        const job = await api("GET", "/api/jobs/" + jobId);
        (job.logs || []).slice(seen).forEach(appendLog);
        seen = job.logs ? job.logs.length : 0;
        if (job.status === "running") {
          setTimeout(poll, 600);
          return;
        }
        if (job.status === "error") reject(new Error(job.error || "任务失败"));
        else resolve(job.result || {});
      } catch (e) {
        reject(e);
      }
    };
    poll();
  });
}

// ---------------- README ----------------

async function doGenerate() {
  if (!state.current) { toast("请先选择项目", "error"); return; }
  const body = {
    path: state.current.path,
    lang: $("#readme-lang").value,
    force: $("#readme-force").checked,
    no_ai: $("#readme-noai").checked,
  };
  setStatus("正在生成 README…");
  switchTab("logs");
  try {
    const { job_id } = await api("POST", "/api/generate", body);
    await runJob(job_id);
    const data = await api("GET", "/api/readme?path=" + encodeURIComponent(state.current.path));
    $("#readme-editor").value = data.content;
    await refreshPreview();
    switchTab("readme");
    toast("README 已生成", "ok");
    setStatus("README 已生成", "ok");
  } catch (e) {
    toast("生成失败: " + e.message, "error");
    setStatus("生成失败", "err");
  }
}

async function refreshPreview() {
  try {
    const data = await api("POST", "/api/preview", { markdown: $("#readme-editor").value });
    $("#readme-preview").innerHTML = data.html;
  } catch (e) {
    $("#readme-preview").innerHTML = `<p class="muted">预览失败: ${esc(e.message)}</p>`;
  }
}

async function doSave() {
  if (!state.current) { toast("请先选择项目", "error"); return; }
  try {
    await api("POST", "/api/save", {
      path: state.current.path,
      content: $("#readme-editor").value,
    });
    toast("README 已保存", "ok");
  } catch (e) {
    toast("保存失败: " + e.message, "error");
  }
}

// ---------------- 推送 ----------------

async function doPush() {
  if (!state.current) { toast("请先选择项目", "error"); return; }
  const body = {
    path: state.current.path,
    private: $("#push-private").checked,
    commit_message: $("#push-message").value.trim(),
  };
  setStatus("正在推送…");
  switchTab("logs");
  try {
    const { job_id } = await api("POST", "/api/push", body);
    const result = await runJob(job_id);
    $("#push-status").textContent = result.status === "pushed" ? "✅ 已推送到 GitHub" : JSON.stringify(result);
    toast("推送完成 🎉", "ok");
    setStatus("已推送", "ok");
  } catch (e) {
    toast("推送失败: " + e.message, "error");
    setStatus("推送失败", "err");
  }
}

// ---------------- 文件夹选择 ----------------

let pickerPath = "";

async function loadDirs(path) {
  pickerPath = path;
  try {
    const data = await api("GET", "/api/fs?path=" + encodeURIComponent(path));
    $("#picker-path").textContent = data.path || "选择磁盘";
    const list = $("#dir-list");
    list.innerHTML = "";
    if (data.parent) {
      const up = document.createElement("div");
      up.className = "dir-item";
      up.textContent = "⬆ 上级目录";
      up.addEventListener("click", () => loadDirs(data.parent));
      list.appendChild(up);
    }
    data.dirs.forEach((d) => {
      const item = document.createElement("div");
      item.className = "dir-item";
      item.textContent = "📁 " + d;
      item.addEventListener("click", () => {
        const sep = /[\\/]$/.test(path) ? "" : "\\";
        loadDirs(path ? path + sep + d : d);
      });
      list.appendChild(item);
    });
  } catch (e) {
    toast(e.message, "error");
  }
}

// ---------------- 事件绑定 ----------------

$("#btn-scan").addEventListener("click", scan);
$("#root-path").addEventListener("keydown", (e) => { if (e.key === "Enter") scan(); });
$("#btn-browse").addEventListener("click", () => {
  $("#picker-modal").classList.remove("hidden");
  loadDirs("");
});
$("#btn-close-picker").addEventListener("click", () => $("#picker-modal").classList.add("hidden"));
$("#btn-pick-here").addEventListener("click", () => {
  if (pickerPath) $("#root-path").value = pickerPath;
  $("#picker-modal").classList.add("hidden");
});
$("#btn-settings").addEventListener("click", () => $("#settings-modal").classList.remove("hidden"));
$("#btn-close-settings").addEventListener("click", () => $("#settings-modal").classList.add("hidden"));
$("#btn-settings-save").addEventListener("click", saveSettings);
$("#btn-generate").addEventListener("click", doGenerate);
$("#btn-save").addEventListener("click", doSave);
$("#btn-preview").addEventListener("click", refreshPreview);
$("#btn-push").addEventListener("click", doPush);
$("#btn-clear-logs").addEventListener("click", () => { $("#log-console").innerHTML = ""; });

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

loadConfig();
