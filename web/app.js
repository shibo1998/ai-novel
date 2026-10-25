/* AI 网文工作台 前端（免构建，功能优先） */
"use strict";

let currentBook = localStorage.getItem("currentBook") || "";
let wbNo = 1;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " error" : "");
  t.style.display = "block";
  setTimeout(() => t.style.display = "none", isErr ? 8000 : 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res;
}

/* ---------------- 导航 ---------------- */
document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => showPage(b.dataset.page)));
function showPage(name) {
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.page === name));
  document.querySelectorAll(".page").forEach(p => p.classList.toggle("active", p.id === "page-" + name));
  if (name === "shelf") loadShelf();
  if (name === "overview" && currentBook) loadOverview();
  if (name === "workbench" && currentBook) loadWorkbench(wbNo);
  if (name === "conflicts") loadConflicts();
  if (name === "settings") loadSettings();
}

/* ---------------- 书架 ---------------- */
async function loadShelf() {
  const books = await (await api("/api/books")).json();
  $("shelf-list").innerHTML = books.map(b => `
    <div class="panel book-card">
      <div style="font-weight:bold;font-size:15px">${esc(b.title || b.name)}</div>
      <div class="dim small">${esc(b.genre)} · ${(b.target_words / 10000).toFixed(0)}万字 · ${esc(b.platform)} · ${esc(b.status)}</div>
      <div class="row" style="margin-top:8px">
        <button class="btn primary" onclick="openBook('${esc(b.name)}')">打开</button>
        <button class="btn danger" onclick="delBook('${esc(b.name)}')">删除</button>
      </div>
    </div>`).join("") || '<span class="dim">（空，先建书）</span>';
}

async function createBook() {
  const body = {
    name: $("nb-name").value.trim(), genre_template: $("nb-genre").value,
    target_words: +$("nb-words").value || 1000000, author: $("nb-author").value,
    platform: $("nb-platform").value,
  };
  if (!body.name) return toast("书名必填", true);
  try { await api("/api/books", { method: "POST", body }); toast("已创建"); $("nb-name").value = ""; loadShelf(); openBook(body.name); }
  catch (e) { toast("创建失败: " + e.message, true); }
}

async function delBook(name) {
  if (!confirm(`删除《${name}》将删除 books/${name}/ 整个目录（含全部章节与档案），不可恢复。确认？`)) return;
  if (!confirm(`再次确认：真的删除《${name}》？`)) return;
  try { await api("/api/books/" + encodeURIComponent(name), { method: "DELETE" }); toast("已删除"); if (currentBook === name) { currentBook = ""; localStorage.removeItem("currentBook"); } loadShelf(); }
  catch (e) { toast(e.message, true); }
}

function openBook(name) {
  currentBook = name; localStorage.setItem("currentBook", name);
  $("global-book").textContent = "当前书：" + name;
  showPage("overview");
}

/* ---------------- 书总览 ---------------- */
const G_TARGETS = { G1: "bible", G2: "bible", G3: "characters", G4: "characters", G5: "outline" };
let aiTarget = null;

async function loadOverview() {
  if (!currentBook) return toast("先从书架打开一本书", true);
  $("global-book").textContent = "当前书：" + currentBook;
  try {
    const ov = await (await api(`/api/books/${enc(currentBook)}/overview`)).json();
    $("ov-title").textContent = ov.meta.title || currentBook;
    $("ov-meta-line").textContent = `${ov.meta.genre} · 目标${(ov.meta.target_words / 10000).toFixed(0)}万字 · ${ov.meta.platform} · ${ov.meta.status}`;
    $("ov-cost").textContent = `全书成本 ${(ov.cost_total.cost_cents / 100).toFixed(2)} 元 / ${ov.cost_total.calls} 次调用`;
    $("ov-conflicts").innerHTML = ov.open_conflicts ? `<span class="badge bad">待裁决 ${ov.open_conflicts} 条</span>` : '<span class="badge ok">裁决队列空</span>';
    $("ov-chapters").querySelector("tbody").innerHTML = ov.chapters.map(c => `
      <tr><td>${c.no}</td><td><span class="badge ${c.status}">${c.status}</span></td>
      <td>v${c.current_version}</td><td>${c.fix_rounds}</td>
      <td class="${c.cost_over_yuan ? "cost-warn" : ""}">${(c.cost_cents / 100).toFixed(2)}元</td>
      <td><button class="btn" onclick="wbNo=${c.no};showPage('workbench')">工作台</button></td></tr>`).join("");
    $("ov-outline").innerHTML = "<tr><th>章</th><th>目标</th><th>冲突</th><th>爽点</th><th>钩子</th><th>★</th></tr>" +
      ov.outline_rows.map(r => `<tr><td>${esc(r["章"])}</td><td>${esc(r["本章目标"])}</td><td>${esc(r["冲突来源"])}</td><td>${esc(r["爽点/揭露"])}</td><td>${esc(r["钩子类型"])}</td><td>${esc(r["重要度★"])}</td></tr>`).join("");
    // bible 编辑器
    const bible = await (await api(`/api/books/${enc(currentBook)}/bible`)).json();
    $("bible-editor").innerHTML = Object.entries(bible).map(([k, v]) =>
      `<label>## ${esc(k)}</label><textarea data-bible="${esc(k)}" rows="4">${esc(v)}</textarea>`).join("");
    // 台账
    const pp = await (await api(`/api/books/${enc(currentBook)}/plot_points`)).json();
    const cols = ["编号", "内容", "埋设章", "推进章", "预计回收章", "状态", "备注"];
    $("pp-editor").value = ["| " + cols.join(" | ") + " |", "| " + cols.map(c => "----").join(" | ") + " |",
      ...pp.map(r => "| " + cols.map(c => r[c] ?? "").join(" | ") + " |")].join("\n");
    // 大纲行编辑
    const oc = ["章", "卷", "本章目标", "冲突来源", "爽点/揭露", "钩子类型", "重要度★"];
    $("outline-rows").value = ["| " + oc.join(" | ") + " |", "| " + oc.map(c => "----").join(" | ") + " |",
      ...ov.outline_rows.map(r => "| " + oc.map(c => r[c] ?? "").join(" | ") + " |")].join("\n");
    renderCharList(ov.characters);
    // 文风
    const st = await (await api(`/api/books/${enc(currentBook)}/style`)).json();
    $("style-corpus").value = st.corpus;
    $("style-card").value = st.card;
    $("style-enabled").checked = st.enabled;
  } catch (e) { toast(e.message, true); }
}

async function saveCorpus() {
  try { await api(`/api/books/${enc(currentBook)}/style/corpus`, { method: "PUT", body: { text: $("style-corpus").value } }); toast("语料已保存"); }
  catch (e) { toast(e.message, true); }
}

async function analyzeStyle() {
  if (!$("style-corpus").value.trim()) return toast("先填语料", true);
  toast("F1 分析中…");
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/style/analyze`, { method: "POST" })).json();
    $("style-card").value = r.card;
    toast("特征卡已生成，请人工改定后保存并启用");
  } catch (e) { toast("分析失败: " + e.message, true); }
}

async function saveCard() {
  try { await api(`/api/books/${enc(currentBook)}/style/card`, { method: "PUT", body: { text: $("style-card").value } }); toast("特征卡已保存"); }
  catch (e) { toast(e.message, true); }
}

async function toggleStyle() {
  const on = $("style-enabled").checked;
  try { await api(`/api/books/${enc(currentBook)}/style/enabled`, { method: "PUT", body: { enabled: on } }); toast(on ? "文风注入已启用（下次生成生效）" : "已停用"); }
  catch (e) { toast(e.message, true); $("style-enabled").checked = !on; }
}

const enc = encodeURIComponent;

async function saveBible() {
  const data = {};
  document.querySelectorAll("[data-bible]").forEach(t => data[t.dataset.bible] = t.value);
  try { await api(`/api/books/${enc(currentBook)}/bible`, { method: "PUT", body: data }); toast("圣经已保存"); }
  catch (e) { toast(e.message, true); }
}

function parseMdTable(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim().startsWith("|"));
  if (lines.length < 2) throw new Error("表格格式错误");
  const cells = (l) => l.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  const cols = cells(lines[0]);
  return lines.slice(1).filter(l => !/^[\s|:-]+$/.test(l)).map(l => {
    const cs = cells(l), r = {}; cols.forEach((c, i) => r[c] = cs[i] ?? ""); return r;
  });
}

async function savePP() {
  try {
    const rows = parseMdTable($("pp-editor").value);
    await api(`/api/books/${enc(currentBook)}/plot_points`, { method: "PUT", body: { rows } });
    toast("台账已保存");
  } catch (e) { toast(e.message, true); }
}

async function saveOutlineRows() {
  try {
    const rows = parseMdTable($("outline-rows").value);
    const od = await (await api(`/api/books/${enc(currentBook)}/outline`)).json();
    od.chapter_rows = rows;
    await api(`/api/books/${enc(currentBook)}/outline`, { method: "PUT", body: od });
    toast("大纲已保存"); loadOverview();
  } catch (e) { toast(e.message, true); }
}

/* ----- 档案 AI 生成 → 采纳 ----- */
async function aiArchive(pid) {
  if (!currentBook) return;
  if (!confirm(`用 ${pid} 生成档案？结果只预览不落盘，采纳前可自由修改。`)) return;
  toast(pid + " 生成中…");
  const vars = {};
  if (pid === "G5") {
    vars["目标总章数"] = prompt("目标总章数", "120") || "120";
    vars["单章字数区间"] = "2200-2800";
    vars["logline"] = prompt("一句话故事（主角为了X必须做Y但障碍是Z）", "") || "";
  }
  if (pid === "G1" || pid === "G2") {
    try { const b = await (await api(`/api/books/${enc(currentBook)}/bible`)).json(); vars["既有设定摘要"] = b["基础设定"] || ""; vars["基础设定"] = b["基础设定"] || ""; vars["既有力量体系"] = b["力量体系"] || ""; } catch (e) { /* */ }
    vars["题材"] = "都市";
    vars["故事核"] = prompt("故事核（一句话）", "") || "";
    vars["对标书"] = "";
  }
  if (pid === "G3" || pid === "G4") {
    try { const cs = await (await api(`/api/books/${enc(currentBook)}/characters`)).json(); vars["角色卡合集"] = cs.map(c => c._body).join("\n\n"); vars["世界观摘要"] = "见圣经"; } catch (e) { /* */ }
    vars["角色需求清单"] = prompt("角色需求清单（每行：名字+定位+要求）", "林寒+主角+底层逆袭") || "";
  }
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/ai/generate`, { method: "POST", body: { prompt_id: pid, variables: vars } })).json();
    aiTarget = G_TARGETS[pid];
    $("ai-target").textContent = `目标：${aiTarget}（来源 ${pid} digest ${r.digest.slice(0, 12)}）`;
    $("ai-text").value = r.text;
    $("ai-preview-box").style.display = "block";
    $("ai-preview-box").open = true;
    toast("已生成，请检查后采纳");
  } catch (e) { toast("生成失败: " + e.message, true); }
}

async function adoptAI() {
  const content = $("ai-text").value;
  const url = `/api/books/${enc(currentBook)}/adopt/${aiTarget}`;
  try {
    const r = await (await api(url, { method: "POST", body: { content } })).json();
    toast("已采纳落盘：" + JSON.stringify(r));
    $("ai-preview-box").style.display = "none";
    loadOverview();
  } catch (e) { toast("采纳失败: " + e.message, true); }
}

/* ----- 角色 ----- */
function renderCharList(names) {
  $("char-list").innerHTML = names.map(n =>
    `<button class="btn" onclick="editChar('${esc(n)}')">${esc(n)}</button>`).join("") || '<span class="dim small">（无角色）</span>';
}

async function editChar(name) {
  const c = await (await api(`/api/books/${enc(currentBook)}/characters/${enc(name)}`)).json();
  $("char-edit-name").textContent = name;
  const fm = { ...c }; delete fm._body; delete fm._file;
  $("char-edit-fm").value = JSON.stringify(fm, null, 2);
  $("char-edit-body").value = c._body;
  $("char-edit-box").style.display = "block";
  $("char-edit-box").open = true;
}

async function saveChar() {
  const name = $("char-edit-name").textContent;
  try {
    const fm = JSON.parse($("char-edit-fm").value);
    fm._body = $("char-edit-body").value;
    await api(`/api/books/${enc(currentBook)}/characters/${enc(name)}`, { method: "PUT", body: fm });
    toast("角色卡已保存");
  } catch (e) { toast(e.message, true); }
}

async function createChar() {
  try {
    await api(`/api/books/${enc(currentBook)}/characters`, { method: "POST", body: { name: $("char-new-name").value.trim(), role: $("char-new-role").value } });
    toast("已建档"); loadOverview();
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 章节工作台 ---------------- */
function wbNav(d) { wbNo = Math.max(1, wbNo + d); loadWorkbench(wbNo); }

async function loadWorkbench(no) {
  wbNo = no;
  if (!currentBook) return toast("先打开一本书", true);
  const d = await (await api(`/api/books/${enc(currentBook)}/chapters/${no}`)).json();
  $("wb-no").textContent = `第 ${no} 章`;
  $("wb-status").textContent = d.status;
  $("wb-status").className = "badge " + d.status;
  $("wb-version").textContent = d.row ? "v" + d.row.current_version : "-";
  $("wb-cost").textContent = d.cost ? (d.cost.cost_cents / 100).toFixed(2) + "元" : "0元";
  $("wb-golden").style.display = d.golden3 ? "inline-block" : "none";

  // 细纲视图（结构化下拉 + 只读信息）
  if (d.outline) {
    const o = d.outline;
    const editable = ["awaiting_outline", "outline_draft", "error"].includes(d.status);
    const sel = (key, opts) => `<select ${editable ? "" : "disabled"} onchange="saveOutlineField('${key}', this.value)">` +
      opts.map(v => `<option value="${v}" ${o[key] === v ? "selected" : ""}>${v}</option>`).join("") + "</select>";
    const hookSel = `<select ${editable ? "" : "disabled"} onchange="saveOutlineHook(this.value)">` +
      ["悬念", "选择", "反转"].map(v => `<option ${o.hook?.type === v ? "selected" : ""}>${v}</option>`).join("") + "</select>";
    $("wb-outline-view").innerHTML = `
      <div class="small"><b>核心事件：</b>${esc(o.core_event)}</div>
      <div class="row small"><b>stage</b> ${sel("stage", ["引入", "发展", "转折", "高潮", "收束"])}
        <b>情绪</b> ${sel("emotion_task", ["压", "小扬", "爆"])}
        <b>pov</b> ${esc(o.pov)}
        <b>钩子类型</b> ${hookSel}（${esc(o.hook?.content || "")}）</div>
      <div class="small"><b>伏笔操作：</b>${esc(JSON.stringify(o.foreshadow_ops || []))}</div>
      <div class="small"><b>确认锁：</b>${o.confirmed ? "✅ 已锁定（改字段请先「改细纲重写」）" : "未确认"}</div>
      <pre>${esc(o._body)}</pre>`;
  } else {
    $("wb-outline-view").innerHTML = '<span class="dim">（未生成细纲）</span>';
  }

  // 正文
  if (d.prose) $("wb-stream").textContent = d.prose._body;
  else $("wb-stream").textContent = "（生成区）";

  // 机检
  renderMech(d.mech);
  renderReview(d.review);
  await loadVersions(no);
}

function renderMech(mech) {
  if (!mech) return $("wb-mech").innerHTML = '<span class="dim">（未跑）</span>';
  $("wb-mech").innerHTML =
    `<div>结论：<span class="badge ${mech.verdict}">${mech.verdict}</span>（${mech.issues.length} 条）</div>` +
    mech.issues.map(i => `<div class="issue ${i.level === "block" ? "blocking" : ""}">
      <b>${esc(i.rule)}</b> <span class="badge ${i.level}">${i.level}</span> 行${i.line} · ${esc(i.word)}<br>
      <span class="dim">${esc(i.message)}</span></div>`).join("");
}

function renderReview(rev) {
  if (!rev) return $("wb-review").innerHTML = '<span class="dim">（未跑）</span>';
  const dims = [["ai_flavor", "AI味"], ["plot", "剧情一致性"], ["pacing", "节奏爽点"], ["prose", "文笔画面"], ["hook", "开篇钩子"]];
  const bars = dims.map(([k, t]) => {
    const v = rev.scores?.[k] ?? 0;
    const cls = v >= 90 ? "" : v >= 70 ? "low" : "bad";
    return `<div class="scorebar"><span style="width:80px" class="small">${t}</span>
      <div class="track"><div class="fill ${cls}" style="width:${v}%"></div></div><span class="small mono">${v}</span></div>`;
  }).join("");
  $("wb-review").innerHTML =
    `<div>verdict：<span class="badge ${rev.verdict === "pass" ? "pass" : rev.verdict === "fix" ? "warn" : "bad"}">${rev.verdict}</span></div>${bars}` +
    (rev.issues || []).map(i => `<div class="issue ${i.severity === "blocking" ? "blocking" : ""}">
      <b>${esc(i.category)}</b> <span class="badge ${i.severity}">${esc(i.severity)}</span> ${esc(i.location)}<br>
      引用：<span class="mono">${esc(i.quote)}</span><br>
      <span class="dim">${esc(i.description)}</span><br>建议：${esc(i.suggestion)}</div>`).join("");
}

async function wbGenerateOutline() {
  try { await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/outline/generate`, { method: "POST" }); toast("细纲已生成（未确认）"); loadWorkbench(wbNo); }
  catch (e) { toast(e.message, true); }
}

async function wbConfirmOutline() {
  if (!confirm("确认细纲后将锁定，正文阶段不再接受细纲变更。确认？")) return;
  try { await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/outline/confirm`, { method: "POST" }); toast("已锁定"); loadWorkbench(wbNo); }
  catch (e) { toast(e.message, true); }
}

async function wbEditOutlineRaw() {
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/outline/raw`)).json();
    $("wb-outline-raw").value = r.content;
    $("wb-outline-edit").style.display = "block"; $("wb-outline-edit").open = true;
  } catch (e) { toast(e.message, true); }
}

async function wbSaveOutlineRaw() {
  const text = $("wb-outline-raw").value;
  const m = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return toast("格式：--- frontmatter --- 正文", true);
  try {
    await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/outline`, {
      method: "PUT", body: { fm_raw: m[1], body: m[2] },
    });
    toast("细纲已保存"); loadWorkbench(wbNo);
  } catch (e) { toast("保存失败：" + e.message, true); }
}

async function wbGenerate() {
  const btn = $("btn-generate"); btn.disabled = true;
  $("wb-stream").textContent = "";
  const pid = $("wb-prompt-id").value;
  try {
    const res = await fetch(`/api/books/${enc(currentBook)}/chapters/${wbNo}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pid ? { prompt_id: pid } : {}),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "", done = false;
    while (!done) {
      const { value, done: d } = await reader.read();
      if (d) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        let evt = "message", data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) evt = line.slice(7).trim();
          if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (evt === "done") { toast("生成完成：" + JSON.stringify(JSON.parse(data))); done = true; }
        else if (evt === "error") { toast("生成失败: " + data, true); done = true; }
        else { try { $("wb-stream").textContent += JSON.parse(data).text; } catch (e) { /* */ } }
        $("wb-stream").scrollTop = 1e9;
      }
    }
    loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
}

async function wbPreview() {
  try {
    const p = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/preview`)).json();
    $("wb-preview").innerHTML =
      `<div class="small dim">总估算 ${p.total_est_tokens} tokens / 预算 ${p.budget_tokens} · 已存 ${esc(p.saved_to || "")}</div>` +
      p.slots.map(s => `<div class="slot"><details>
        <summary>${esc(s.title)} · ${s.est_tokens}t ${s.truncated ? "· <span class='badge warn'>已截断</span>" : ""}</summary>
        <pre>${esc(s.content || "（空）")}</pre></details></div>`).join("");
  } catch (e) { toast(e.message, true); }
}

async function wbMech() {
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/mechcheck`, { method: "POST" })).json();
    renderMech({ verdict: r.hits.some(h => h.level === "block") ? "block" : "pass", issues: r.hits });
    toast("机械检查完成，状态 → " + r.status);
    loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); }
}

async function wbReview() {
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/review`, { method: "POST" })).json();
    renderReview(r); toast("审校 verdict=" + r.verdict + "，状态 → " + r.status);
    loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); }
}

async function wbFix() {
  try { const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/fix`, { method: "POST", body: {} })).json(); toast("修复完成 " + r.word_count + " 字"); loadWorkbench(wbNo); }
  catch (e) { toast(e.message, true); }
}

async function wbPolish() {
  if (!confirm("整章润色（V2 去AI味 + V3 复核）：不改剧情人设，字数浮动>10%或机检阻断会自动丢弃。继续？")) return;
  toast("润色中（两次 LLM 调用）…");
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/polish`, { method: "POST" })).json();
    if (r.ok) toast(`润色完成：${r.word_count.before}→${r.word_count.after} 字，V3=${r.v3?.verdict}，改动 ${r.changes.length} 处`);
    else toast(`润色被丢弃：${r.reason}`, true);
    loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); }
}

async function saveOutlineField(key, value) {
  const d = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}`)).json();
  const fm = { ...d.outline }; delete fm._body;
  fm[key] = value;
  try {
    await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/outline`, { method: "PUT", body: { fm, body: d.outline._body } });
    toast(`已改 ${key} = ${value}`); loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); loadWorkbench(wbNo); }
}

async function saveOutlineHook(type) {
  const d = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}`)).json();
  const fm = { ...d.outline }; delete fm._body;
  fm.hook = { ...(fm.hook || {}), type };
  try {
    await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/outline`, { method: "PUT", body: { fm, body: d.outline._body } });
    toast(`钩子类型 → ${type}`); loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); loadWorkbench(wbNo); }
}

async function runAudit() {
  const n = +$("audit-no").value;
  if (!n) return toast("填审计截止章号", true);
  toast(`卷末审计中（本卷全部正文 + 台账 + Bible，一次长上下文调用）…`);
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/audit/${n}`, { method: "POST" })).json();
    toast(`审计完成：伏笔回收率 ${r.foreshadow_pay_rate}，报告已存 ${r.report_path}`);
  } catch (e) { toast(e.message, true); }
}

async function wbExempt() {
  const reason = prompt("豁免理由（必填，将写入 generation_log 作为合规证据）：");
  if (!reason) return;
  try { await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/exempt`, { method: "POST", body: { reason } }); toast("已豁免 → 待定稿"); loadWorkbench(wbNo); }
  catch (e) { toast(e.message, true); }
}

async function wbBackOutline() {
  if (!confirm("回到 outline_draft 修改细纲？（正文保留，改完需重新确认+生成）")) return;
  try { await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/edit-outline`, { method: "POST" }); toast("已回到 outline_draft"); loadWorkbench(wbNo); }
  catch (e) { toast(e.message, true); }
}

async function loadVersions(no) {
  try {
    const vs = await (await api(`/api/books/${enc(currentBook)}/chapters/${no}/versions`)).json();
    $("wb-rewrite-ver").innerHTML = vs.map(v => `<option value="${v.version}">v${v.version} ${esc(v.label)} ${v.created_at.slice(0, 16)}</option>`).join("") || '<option value="">（无快照）</option>';
    $("wb-versions").textContent = vs.length ? `快照 ${vs.length} 个（定稿历史全保留）` : "（暂无快照）";
  } catch (e) { /* */ }
}

async function wbFinalize() {
  if (!confirm("第一次确认：定稿将写快照、置 status=final 并触发记忆卡回写。继续？")) return;
  if (!confirm("第二次确认：真的定稿？")) return;
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/finalize`, { method: "POST" })).json();
    toast("已定稿，快照 v" + r.snapshot_version + "；回写：" + JSON.stringify(r.writeback).slice(0, 200));
    loadWorkbench(wbNo); refreshConflictBadge();
  } catch (e) { toast(e.message, true); }
}

async function wbRewrite() {
  const v = $("wb-rewrite-ver").value;
  if (!confirm(`从 v${v || "当前"} 重写？将开新版本分支，旧版本保留。`)) return;
  try {
    const r = await (await api(`/api/books/${enc(currentBook)}/chapters/${wbNo}/rewrite`, { method: "POST", body: { base_version: v ? +v : null } })).json();
    toast("新分支 v" + r.new_version); loadWorkbench(wbNo);
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 裁决队列 ---------------- */
async function refreshConflictBadge() {
  try {
    const items = await (await api("/api/conflicts")).json();
    $("conflict-badge").textContent = items.length ? items.length : "";
  } catch (e) { /* */ }
}

async function loadConflicts() {
  const items = await (await api("/api/conflicts")).json();
  $("conflict-list").innerHTML = items.length ? items.map(it => `
    <div class="panel">
      <div><span class="badge bad">${esc(it.kind)}</span> 第${it.chapter_no}章 · ${esc(it.book)} ${it.char ? "· " + esc(it.char) + (it.field ? "." + esc(it.field) : "") : ""}</div>
      <div style="margin:6px 0">${esc(it.detail)}</div>
      <div class="small dim">档案现值：${esc(JSON.stringify(it.old))} → AI 新值：${esc(JSON.stringify(it.new))}</div>
      <div class="row" style="margin-top:8px">
        <button class="btn primary" onclick="resolveConflict(${it.id},'adopt_ai')">采纳AI</button>
        <button class="btn" onclick="resolveConflict(${it.id},'keep_archive')">保留档案</button>
        <input id="manual-${it.id}" placeholder="手改值（JSON）" style="width:200px">
        <button class="btn" onclick="resolveManual(${it.id})">手改采纳</button>
      </div>
    </div>`).join("") : '<div class="panel dim">队列已清零 ✅</div>';
  refreshConflictBadge();
}

async function resolveConflict(id, action, manual) {
  try { await api(`/api/conflicts/${id}/resolve`, { method: "POST", body: { action, manual_value: manual } }); toast("已裁决"); loadConflicts(); }
  catch (e) { toast(e.message, true); }
}

async function resolveManual(id) {
  let v = $("manual-" + id).value.trim();
  if (!v) return toast("填手改值", true);
  try { v = JSON.parse(v); } catch (e) { /* 保留字符串 */ }
  resolveConflict(id, "manual", v);
}

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  const s = await (await api("/api/settings")).json();
  const p = (s.providers || {}).openai || {};
  $("prov-openai-key").placeholder = p.api_key || "未配置";
  $("prov-openai-key").value = "";
  $("prov-openai-url").value = p.base_url || "";
  $("default-model").value = s.default_model || "";
  $("price-editor").value = JSON.stringify(s.prices || {}, null, 2);
  $("fts-mode").textContent = (await (await api("/api/fts/mode")).json()).mode;
}

async function saveProviders() {
  const cur = await (await api("/api/settings")).json();
  const key = $("prov-openai-key").value.trim();
  const patch = {
    providers: {
      openai: {
        // 输入框留空或仍是掩码占位则保留原 key
        api_key: (!key || key.includes("…")) ? (cur.providers.openai || {}).api_key : key,
        base_url: $("prov-openai-url").value.trim(),
      },
    },
    default_model: $("default-model").value.trim(),
  };
  try { await api("/api/settings", { method: "PUT", body: { settings: patch } }); toast("已保存"); loadSettings(); }
  catch (e) { toast(e.message, true); }
}

async function saveModelProviders() {}  // 已移除模型映射（单一 OpenAI 端点）

async function savePrices() {
  try { await api("/api/settings", { method: "PUT", body: { settings: { prices: JSON.parse($("price-editor").value) } } }); toast("已保存"); }
  catch (e) { toast(e.message, true); }
}

async function testConn() {
  $("conn-result").textContent = "测试中…";
  const r = await (await api("/api/settings/test", { method: "POST", body: {} })).json();
  $("conn-result").innerHTML = r.ok ? `<span class="badge ok">OK</span> ${esc(r.model)}：${esc(r.reply)}` : `<span class="badge bad">失败</span> ${esc(r.error)}`;
}

async function loadLint() {
  const r = await (await api("/api/lint/" + $("lint-which").value)).json();
  $("lint-editor").value = r.content;
}

async function saveLint() {
  try { await api("/api/lint/" + $("lint-which").value, { method: "PUT", body: { which: $("lint-which").value, content: $("lint-editor").value } }); toast("词库已保存"); }
  catch (e) { toast(e.message, true); }
}

async function rebuildDB() {
  try { const r = await (await api("/api/db/rebuild", { method: "POST" })).json(); $("rebuild-result").textContent = JSON.stringify(r); toast("重建完成"); }
  catch (e) { toast(e.message, true); }
}

function exportTxt() { window.open(`/api/books/${enc(currentBook)}/export/txt`, "_blank"); }
function exportZip() { window.open(`/api/books/${enc(currentBook)}/export/zip`, "_blank"); }

/* ---------------- 启动 ---------------- */
$("global-book").textContent = currentBook ? "当前书：" + currentBook : "";
showPage("shelf");
refreshConflictBadge();
setInterval(refreshConflictBadge, 15000);
