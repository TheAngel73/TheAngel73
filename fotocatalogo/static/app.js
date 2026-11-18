"use strict";
// ================================================================ utilità
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const LABELS = {red: "Rossa", yellow: "Gialla", green: "Verde", blue: "Blu", purple: "Viola"};
const PAGE = 300;

async function api(url, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(url, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw new Error(data.error || `Errore ${r.status}`);
  return data;
}
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = isError ? "error" : ""; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, isError ? 6000 : 2500);
}
async function safe(fn) { try { return await fn(); } catch (e) { toast(e.message, true); } }
const store = {
  get(k, d) { try { const v = localStorage.getItem("fc:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("fc:" + k, JSON.stringify(v)); } catch {} },
};
function fmtDate(t) {
  if (!t) return "—";
  const [d, h] = t.split("T");
  const [y, m, g] = d.split("-");
  return `${g}/${m}/${y}${h ? " " + h.slice(0, 5) : ""}`;
}
function fmtSize(b) {
  if (!b) return "—";
  return b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " KB";
}

// ================================================================ stato
const S = {
  tree: null, scope: store.get("scope", "all"),
  total: 0, pages: new Map(), loading: new Set(), version: 0,
  sel: new Set(), anchor: -1, cursor: -1,
  expanded: new Set(store.get("expanded", [])),
  cols: 1, cw: 180, ch: 200,
  detail: null,
};

function params(extra = {}) {
  const [sort, dir] = $("#sort").value.split(":");
  const p = {scope: S.scope, deep: $("#deep").checked ? "1" : "0", q: $("#search").value,
    rating: $("#f-rating").value, label: $("#f-label").value, flag: $("#f-flag").value,
    year: $("#f-year").value, ext: $("#f-ext").value, sort, dir, ...extra};
  return new URLSearchParams(Object.entries(p).filter(([, v]) => v !== "" && v != null)).toString();
}

// ================================================================ griglia virtuale
const scroller = $("#grid-scroll"), space = $("#grid-space");
const cells = new Map();   // indice -> elemento

function itemAt(i) { const pg = S.pages.get(Math.floor(i / PAGE)); return pg ? pg[i % PAGE] : undefined; }

async function loadPage(p) {
  if (S.pages.has(p) || S.loading.has(p)) return;
  S.loading.add(p);
  const v = S.version;
  try {
    const data = await api(`/api/photos?${params({offset: p * PAGE, limit: PAGE})}`);
    if (v !== S.version) return;
    S.total = data.total;
    S.pages.set(p, data.items);
    $("#view-count").textContent = `· ${data.total.toLocaleString("it-IT")} foto`;
    layout();
  } catch (e) { toast(e.message, true); }
  finally { S.loading.delete(p); }
}

async function reload(keepScroll = false) {
  S.version++;
  S.pages.clear(); S.loading.clear();
  for (const el of cells.values()) el.remove();
  cells.clear();
  if (!keepScroll) { S.sel.clear(); S.cursor = S.anchor = -1; scroller.scrollTop = 0; }
  await loadPage(0);
  if (!keepScroll) showInfo();
  if (S.total === 0) space.innerHTML = `<div class="empty">${emptyMessage()}</div>`;
}

function emptyMessage() {
  if (S.tree && S.tree.stats.total === 0)
    return "Il catalogo è vuoto.<br>Dal menu <b>Libreria</b> puoi <b>importare il catalogo di Lightroom</b><br>oppure <b>aggiungere una cartella</b> di foto dal NAS.";
  return "Nessuna foto in questa vista.";
}

function layout() {
  const w = scroller.clientWidth - 8;
  const want = +$("#zoom").value;
  S.cols = Math.max(1, Math.floor(w / want));
  S.cw = w / S.cols; S.ch = Math.round(S.cw * 0.82 + 20);
  const empty = space.querySelector(".empty");
  if (empty && S.total) empty.remove();
  space.style.height = Math.ceil(S.total / S.cols) * S.ch + 8 + "px";
  render();
}

function render() {
  const top = scroller.scrollTop, h = scroller.clientHeight;
  const r0 = Math.max(0, Math.floor(top / S.ch) - 1), r1 = Math.ceil((top + h) / S.ch) + 1;
  const i0 = r0 * S.cols, i1 = Math.min(S.total, r1 * S.cols);
  for (const [i, el] of cells) if (i < i0 || i >= i1) { el.remove(); cells.delete(i); }
  for (let i = i0; i < i1; i++) {
    const it = itemAt(i);
    if (!it) loadPage(Math.floor(i / PAGE));
    let el = cells.get(i);
    const key = it ? `${it.id}|${it.r}|${it.l}|${it.f}|${it.m}` : "?";
    if (!el || el._key !== key) {
      if (el) el.remove();
      el = makeCell(it, i); el._key = key;
      cells.set(i, el); space.appendChild(el);
    }
    el.style.cssText = `left:${4 + (i % S.cols) * S.cw + 3}px;top:${4 + Math.floor(i / S.cols) * S.ch + 3}px;width:${S.cw - 6}px;height:${S.ch - 6}px`;
    el.classList.toggle("sel", !!it && S.sel.has(it.id));
    el.classList.toggle("cursor", i === S.cursor);
  }
}

function makeCell(it, i) {
  const el = document.createElement("div");
  el.className = "cell"; el.dataset.i = i;
  if (!it) { el.innerHTML = `<div class="im"></div>`; return el; }
  el.draggable = true;
  if (it.f === -1) { el.classList.add("rejected"); el.title = "Scartata"; }
  const stars = it.r ? "★".repeat(it.r) : "";
  el.innerHTML = `<div class="im"><img loading="lazy" decoding="async" alt="" src="/thumb/${it.id}"></div>
    ${it.f === 1 ? '<span class="flag" title="Scelta">⚑</span>' : ""}
    ${it.m ? '<span class="miss" title="File non trovato">⚠ mancante</span>' : ""}
    <div class="cap">${it.l ? `<span class="dot lbl-${it.l}"></span>` : ""}<span class="fn" title="${esc(it.n)}">${esc(it.n)}</span><span class="stars">${stars}</span></div>`;
  const img = el.querySelector("img");
  img.onerror = () => { img.replaceWith(Object.assign(document.createElement("span"), {className: "ph", textContent: it.x || "?"})); };
  return el;
}

scroller.addEventListener("scroll", () => requestAnimationFrame(render));
new ResizeObserver(() => layout()).observe(scroller);
$("#zoom").value = store.get("zoom", 180);
$("#zoom").addEventListener("input", () => { store.set("zoom", +$("#zoom").value); layout(); });

// ---------------------------------------------------------------- selezione
async function idsRange(a, b) {
  const lo = Math.min(a, b), hi = Math.max(a, b);
  const out = [];
  let complete = true;
  for (let i = lo; i <= hi; i++) { const it = itemAt(i); if (!it) { complete = false; break; } out.push(it.id); }
  if (complete) return out;
  return (await api(`/api/photos?${params({offset: lo, limit: hi - lo + 1, ids_only: 1})}`)).ids;
}

async function clickCell(i, e) {
  const it = itemAt(i); if (!it) return;
  if (e.shiftKey && S.anchor >= 0) {
    const ids = await idsRange(S.anchor, i);
    if (!(e.metaKey || e.ctrlKey)) S.sel.clear();
    ids.forEach(id => S.sel.add(id));
  } else if (e.metaKey || e.ctrlKey) {
    S.sel.has(it.id) ? S.sel.delete(it.id) : S.sel.add(it.id);
    S.anchor = i;
  } else {
    S.sel.clear(); S.sel.add(it.id); S.anchor = i;
  }
  S.cursor = i;
  selChanged();
}

function selChanged() {
  $("#sel-count").textContent = S.sel.size > 1 ? `${S.sel.size.toLocaleString("it-IT")} selezionate` : "";
  render(); showInfo();
}

space.addEventListener("click", e => {
  const c = e.target.closest(".cell"); if (!c) { if (!e.metaKey && !e.shiftKey) { S.sel.clear(); selChanged(); } return; }
  clickCell(+c.dataset.i, e);
});
space.addEventListener("dblclick", e => { const c = e.target.closest(".cell"); if (c) openLoupe(+c.dataset.i); });
space.addEventListener("dragstart", e => {
  const c = e.target.closest(".cell"); const it = c && itemAt(+c.dataset.i);
  if (!it) return e.preventDefault();
  if (!S.sel.has(it.id)) { S.sel.clear(); S.sel.add(it.id); S.anchor = S.cursor = +c.dataset.i; selChanged(); }
  e.dataTransfer.setData("application/x-fc-photos", JSON.stringify([...S.sel]));
  e.dataTransfer.effectAllowed = "copy";
  const n = S.sel.size;
  if (n > 1) {
    const g = Object.assign(document.createElement("div"), {textContent: `${n} foto`});
    g.style.cssText = "position:absolute;top:-100px;padding:6px 12px;background:#4a90e2;color:#fff;border-radius:6px;font:13px sans-serif";
    document.body.appendChild(g); e.dataTransfer.setDragImage(g, 0, 0); setTimeout(() => g.remove());
  }
});

function ensureVisible(i) {
  const y = Math.floor(i / S.cols) * S.ch;
  if (y < scroller.scrollTop) scroller.scrollTop = y;
  else if (y + S.ch > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = y + S.ch - scroller.clientHeight;
}

// ---------------------------------------------------------------- metadati
function patchItems(ids, patch) {
  const set = new Set(ids);
  for (const pg of S.pages.values()) for (const it of pg) if (set.has(it.id)) Object.assign(it, patch);
}
async function setMeta(patch, ids = [...S.sel]) {
  if (!ids.length) return;
  await safe(async () => {
    await api("/api/meta", {ids, ...patch});
    patchItems(ids, {...("rating" in patch ? {r: patch.rating} : {}), ...("label" in patch ? {l: patch.label} : {}),
      ...("flag" in patch ? {f: patch.flag} : {})});
    render(); showInfo(true);
  });
}

// ================================================================ barra laterale
function nodeHTML({icon, name, count, active, tw, acts = "", dot = ""}) {
  return `<div class="node${active ? " active" : ""}"><span class="tw">${tw ?? ""}</span>${dot}<span class="ic">${icon}</span>` +
    `<span class="nm" title="${esc(name)}">${esc(name)}</span><span class="acts">${acts}</span>` +
    `<span class="ct">${count != null ? count.toLocaleString("it-IT") : ""}</span></div>`;
}
function toggleExpand(key) {
  S.expanded.has(key) ? S.expanded.delete(key) : S.expanded.add(key);
  store.set("expanded", [...S.expanded]); renderSidebar();
}

async function loadTree() {
  S.tree = await api("/api/tree");
  renderSidebar();
  const years = await api("/api/years");
  const fy = $("#f-year"), cur = fy.value;
  fy.innerHTML = `<option value="">Tutti gli anni</option>` + years.map(y => `<option>${esc(y)}</option>`).join("");
  fy.value = cur;
}

function setScope(scope) {
  S.scope = scope; store.set("scope", scope);
  $("#view-title").textContent = scopeTitle(scope);
  renderSidebar(); reload();
}
function scopeTitle(scope) {
  const [k, v] = scope.split(":"); const t = S.tree; if (!t) return "";
  const fixed = {all: "Tutte le foto", nocoll: "Foto senza raccolta", missing: "File mancanti", recent: "Importazione più recente"};
  if (fixed[k]) return fixed[k];
  if (k === "collection") { const c = t.collections.find(c => c.id == v); return c ? (c.set ? "Set: " : "Raccolta: ") + c.name : "?"; }
  if (k === "keyword") { const c = t.keywords.find(c => c.id == v); return c ? "Parola chiave: " + c.name : "?"; }
  if (k === "folder") {
    const f = t.folders.find(f => f.id == v); if (!f) return "?";
    const r = t.roots.find(r => r.id === f.root);
    const path = []; let x = f;
    while (x && x.name) { path.unshift(x.name); x = t.folders.find(y => y.id === x.parent); }
    return "Cartella: " + [r ? r.name : "", ...path].join(" / ");
  }
  return scope;
}

function renderSidebar() {
  const t = S.tree; if (!t) return;
  // catalogo
  const cat = [["all", "▦", "Tutte le foto", t.stats.total], ["recent", "◷", "Importazione più recente", null],
    ["nocoll", "◌", "Senza raccolta", t.stats.nocoll]];
  if (t.stats.missing) cat.push(["missing", "⚠", "File mancanti", t.stats.missing]);
  $("#catalog-list").innerHTML = cat.map(([s, icon, name, count]) =>
    `<li data-scope="${s}">${nodeHTML({icon, name, count, active: S.scope === s})}</li>`).join("");

  // raccolte
  const cf = $("#coll-filter").value.trim().toLowerCase();
  const kids = groupBy(t.collections, "parent");
  let visible = null;
  if (cf) {
    visible = new Set();
    const byId = new Map(t.collections.map(c => [c.id, c]));
    for (const c of t.collections) if (c.name.toLowerCase().includes(cf)) {
      for (let x = c; x; x = byId.get(x.parent)) visible.add(x.id);
    }
  }
  const collHTML = list => list.filter(c => !visible || visible.has(c.id)).map(c => {
    const ch = kids.get(c.id) || [];
    const open = cf || S.expanded.has("c" + c.id);
    const acts = `<button data-edit-coll="${c.id}" title="Rinomina / sposta">✎</button><button data-del-coll="${c.id}" title="Elimina">🗑</button>`;
    return `<li data-scope="collection:${c.id}" data-coll="${c.id}" data-isset="${c.set}">` +
      nodeHTML({icon: c.set ? "▤" : "▢", name: c.name, count: c.set ? null : c.count, active: S.scope === "collection:" + c.id,
        tw: c.set ? (ch.length ? (open ? "▼" : "▶") : "") : "", acts}) +
      (c.set && open && ch.length ? `<ul>${collHTML(ch)}</ul>` : "") + "</li>";
  }).join("");
  $("#coll-tree").innerHTML = collHTML(kids.get(null) || []) ||
    `<li class="small">Nessuna raccolta. Usa ＋ per crearne una.</li>`;

  // cartelle
  const fk = groupBy(t.folders, "parent");
  const deep = new Map();
  const deepCount = f => { if (deep.has(f.id)) return deep.get(f.id); const n = f.count + (fk.get(f.id) || []).reduce((a, k) => a + deepCount(k), 0); deep.set(f.id, n); return n; };
  const folderHTML = list => list.map(f => {
    const ch = fk.get(f.id) || []; const open = S.expanded.has("f" + f.id);
    return `<li data-scope="folder:${f.id}" data-folder="${f.id}">` +
      nodeHTML({icon: "▱", name: f.name, count: deepCount(f), active: S.scope === "folder:" + f.id, tw: ch.length ? (open ? "▼" : "▶") : ""}) +
      (open && ch.length ? `<ul>${folderHTML(ch)}</ul>` : "") + "</li>";
  }).join("");
  $("#folder-tree").innerHTML = t.roots.map(r => {
    const top = t.folders.find(f => f.root === r.id && f.parent == null);
    if (!top) return "";
    const ch = fk.get(top.id) || []; const open = S.expanded.has("f" + top.id);
    const acts = `<button data-rescan="${r.id}" title="Sincronizza (cerca foto nuove o spostate)">⟳</button><button data-del-root="${r.id}" title="Rimuovi dal catalogo">✕</button>`;
    return `<li data-scope="folder:${top.id}" data-folder="${top.id}" title="${esc(r.path)}">` +
      nodeHTML({icon: "", name: r.name, count: deepCount(top), active: S.scope === "folder:" + top.id,
        tw: ch.length ? (open ? "▼" : "▶") : "", acts, dot: `<span class="dot ${r.online ? "on" : "off"}" title="${r.online ? "Raggiungibile" : "Non raggiungibile: NAS scollegato?"}"></span>`}) +
      (open && ch.length ? `<ul>${folderHTML(ch)}</ul>` : "") + "</li>";
  }).join("") || `<li class="small">Nessuna cartella.</li>`;

  // parole chiave
  const kf = $("#kw-filter").value.trim().toLowerCase();
  const kk = groupBy(t.keywords, "parent");
  const kwHTML = list => list.map(k => {
    const ch = kk.get(k.id) || []; const open = S.expanded.has("k" + k.id);
    return `<li data-scope="keyword:${k.id}">` + nodeHTML({icon: "#", name: k.name, count: k.count,
      active: S.scope === "keyword:" + k.id, tw: ch.length ? (open ? "▼" : "▶") : ""}) +
      (open && ch.length ? `<ul>${kwHTML(ch)}</ul>` : "") + "</li>";
  }).join("");
  $("#kw-tree").innerHTML = kf
    ? t.keywords.filter(k => k.name.toLowerCase().includes(kf)).slice(0, 300).map(k =>
      `<li data-scope="keyword:${k.id}">${nodeHTML({icon: "#", name: k.name, count: k.count, active: S.scope === "keyword:" + k.id})}</li>`).join("")
    : kwHTML(kk.get(0) || []) || `<li class="small">Nessuna parola chiave.</li>`;
  $("#kw-list")?.remove();
  document.body.insertAdjacentHTML("beforeend", `<datalist id="kw-list">${t.keywords.map(k => `<option value="${esc(k.name)}">`).join("")}</datalist>`);
}

function groupBy(list, key) {
  const m = new Map();
  for (const x of list) { const k = x[key] ?? null; (m.get(k) || m.set(k, []).get(k)).push(x); }
  return m;
}

$("#sidebar").addEventListener("click", async e => {
  const b = e.target.closest("button");
  if (b) {
    e.stopPropagation();
    if (b.dataset.act === "new-coll") return newCollection(false);
    if (b.dataset.act === "new-set") return newCollection(true);
    if (b.dataset.editColl) return editCollection(+b.dataset.editColl);
    if (b.dataset.delColl) return deleteCollection(+b.dataset.delColl);
    if (b.dataset.rescan) return safe(async () => { await api(`/api/roots/${b.dataset.rescan}/rescan`, {}); pollJobs(); });
    if (b.dataset.delRoot) return removeRoot(+b.dataset.delRoot);
    return;
  }
  const li = e.target.closest("li[data-scope]"); if (!li) return;
  if (e.target.classList.contains("tw") && e.target.textContent) {
    const key = li.dataset.coll ? "c" + li.dataset.coll : li.dataset.folder ? "f" + li.dataset.folder : "k" + li.dataset.scope.split(":")[1];
    return toggleExpand(key);
  }
  setScope(li.dataset.scope);
});
$("#coll-filter").addEventListener("input", renderSidebar);
$("#kw-filter").addEventListener("input", renderSidebar);

// trascinamento: foto -> raccolta, raccolta -> set
$("#coll-tree").addEventListener("dragstart", e => {
  const li = e.target.closest("li[data-coll]"); if (!li) return;
  e.dataTransfer.setData("application/x-fc-coll", li.dataset.coll);
});
$("#coll-tree").setAttribute("draggable", "false");
const collTree = $("#coll-tree");
collTree.addEventListener("mousedown", e => {
  collTree.querySelectorAll("li[data-coll]").forEach(li => li.draggable = false);
  const li = e.target.closest("li[data-coll]"); if (li) li.draggable = true;
});
function dropTarget(e) {
  const li = e.target.closest("li[data-coll]"); if (!li) return null;
  const types = e.dataTransfer.types;
  if (types.includes("application/x-fc-photos") && li.dataset.isset === "0") return li;
  if (types.includes("application/x-fc-coll") && li.dataset.isset === "1") return li;
  return null;
}
collTree.addEventListener("dragover", e => {
  collTree.querySelectorAll(".drop").forEach(n => n.classList.remove("drop"));
  const li = dropTarget(e); if (!li) return;
  e.preventDefault(); li.querySelector(".node").classList.add("drop");
});
collTree.addEventListener("dragleave", () => collTree.querySelectorAll(".drop").forEach(n => n.classList.remove("drop")));
collTree.addEventListener("drop", e => {
  collTree.querySelectorAll(".drop").forEach(n => n.classList.remove("drop"));
  const li = dropTarget(e); if (!li) return;
  e.preventDefault();
  const photos = e.dataTransfer.getData("application/x-fc-photos");
  if (photos) return addToCollection(+li.dataset.coll, JSON.parse(photos));
  const coll = e.dataTransfer.getData("application/x-fc-coll");
  if (coll && coll !== li.dataset.coll) safe(async () => {
    await api(`/api/collections/${coll}/update`, {parent: +li.dataset.coll});
    S.expanded.add("c" + li.dataset.coll); await loadTree();
  });
});
// trascina una raccolta sul titolo "Raccolte" per portarla al primo livello
const collHeader = collTree.parentElement.querySelector("h3");
collHeader.addEventListener("dragover", e => { if (e.dataTransfer.types.includes("application/x-fc-coll")) e.preventDefault(); });
collHeader.addEventListener("drop", e => {
  const coll = e.dataTransfer.getData("application/x-fc-coll");
  if (coll) safe(async () => { await api(`/api/collections/${coll}/update`, {parent: null}); await loadTree(); });
});

// ================================================================ raccolte
function collPath(c) {
  const byId = new Map(S.tree.collections.map(x => [x.id, x]));
  const parts = []; for (let x = byId.get(c.parent); x; x = byId.get(x.parent)) parts.unshift(x.name);
  return parts.join(" › ");
}
function setOptions(selected, excludeId) {
  const sets = S.tree.collections.filter(c => c.set && c.id !== excludeId);
  return `<option value="">— Nessun set (primo livello) —</option>` + sets.map(s =>
    `<option value="${s.id}"${s.id === selected ? " selected" : ""}>${esc((collPath(s) ? collPath(s) + " › " : "") + s.name)}</option>`).join("");
}

async function addToCollection(cid, ids) {
  await safe(async () => {
    await api(`/api/collections/${cid}/add`, {ids});
    const c = S.tree.collections.find(c => c.id === cid);
    rememberColl(cid);
    toast(`${ids.length} foto aggiunte a «${c ? c.name : ""}»`);
    await loadTree(); showInfo(true);
  });
}
function rememberColl(cid) { store.set("recentColl", [cid, ...store.get("recentColl", []).filter(x => x !== cid)].slice(0, 8)); }

async function newCollection(isSet, ids = []) {
  const parent = (() => { const [k, v] = S.scope.split(":"); const c = k === "collection" && S.tree.collections.find(c => c.id == v); return c ? (c.set ? c.id : c.parent) : null; })();
  const f = await dialog(isSet ? "Nuovo set di raccolte" : "Nuova raccolta", `
    <label>Nome</label><input type="text" name="name" required autofocus>
    <label>Dentro il set</label><select name="parent">${setOptions(parent)}</select>
    ${!isSet && ids.length ? `<p>Verranno aggiunte le ${ids.length} foto selezionate.</p>` : ""}`, "Crea");
  if (!f) return;
  await safe(async () => {
    const r = await api("/api/collections", {name: f.name.value, parent: +f.parent.value || null, set: isSet, ids});
    if (f.parent.value) S.expanded.add("c" + f.parent.value);
    if (!isSet) rememberColl(r.id);
    await loadTree(); showInfo(true);
    toast(isSet ? "Set creato" : "Raccolta creata");
  });
}
async function editCollection(cid) {
  const c = S.tree.collections.find(c => c.id === cid);
  const f = await dialog(c.set ? "Modifica set" : "Modifica raccolta", `
    <label>Nome</label><input type="text" name="name" value="${esc(c.name)}" required>
    <label>Dentro il set</label><select name="parent">${setOptions(c.parent, c.id)}</select>`, "Salva");
  if (!f) return;
  await safe(async () => { await api(`/api/collections/${cid}/update`, {name: f.name.value, parent: +f.parent.value || null}); await loadTree(); $("#view-title").textContent = scopeTitle(S.scope); });
}
async function deleteCollection(cid) {
  const c = S.tree.collections.find(c => c.id === cid);
  const f = await dialog("Elimina", `<p>Eliminare ${c.set ? "il set" : "la raccolta"} «${esc(c.name)}»${c.set ? " e tutte le raccolte che contiene" : ""}?</p>
    <p>Le foto <b>non</b> vengono cancellate: restano nel catalogo e nelle altre raccolte.</p>`, "Elimina");
  if (!f) return;
  await safe(async () => { await api(`/api/collections/${cid}/delete`, {}); if (S.scope === "collection:" + cid) setScope("all"); await loadTree(); });
}

async function pickCollection(ids) {
  if (!ids.length) return;
  const recent = store.get("recentColl", []);
  const colls = S.tree.collections.filter(c => !c.set).map(c => ({...c, path: collPath(c)}));
  colls.sort((a, b) => (recent.includes(b.id) - recent.includes(a.id)) || (recent.indexOf(a.id) - recent.indexOf(b.id)) || a.name.localeCompare(b.name));
  let chosen = null;
  const p = dialog(`Aggiungi ${ids.length} foto a una raccolta`, `
    <input type="text" name="q" placeholder="Cerca raccolta… (Invio per scegliere)" autocomplete="off">
    <div class="list" id="pick-list"></div>
    <p>Puoi anche trascinare le foto direttamente su una raccolta nella barra a sinistra.</p>`, "Aggiungi", (form) => {
    const draw = () => {
      const q = form.q.value.trim().toLowerCase();
      const list = colls.filter(c => (c.name + " " + c.path).toLowerCase().includes(q)).slice(0, 200);
      if (!list.find(c => c.id === chosen)) chosen = list[0]?.id ?? null;
      $("#pick-list").innerHTML = list.map(c => `<div data-id="${c.id}" class="${c.id === chosen ? "on" : ""}">${esc(c.name)}<span class="setname">${esc(c.path)}</span></div>`).join("") +
        (q ? `<div data-new="1">＋ Nuova raccolta «${esc(form.q.value.trim())}»</div>` : "");
    };
    form.q.addEventListener("input", draw);
    $("#pick-list").addEventListener("click", e => {
      const d = e.target.closest("div[data-id],div[data-new]"); if (!d) return;
      chosen = d.dataset.new ? "new" : +d.dataset.id; form.requestSubmit();
    });
    form.q.addEventListener("keydown", e => {
      const items = [...document.querySelectorAll("#pick-list div[data-id]")];
      const idx = items.findIndex(d => +d.dataset.id === chosen);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const n = items[Math.max(0, Math.min(items.length - 1, idx + (e.key === "ArrowDown" ? 1 : -1)))];
        if (n) { chosen = +n.dataset.id; draw(); n.scrollIntoView({block: "nearest"}); }
      }
    });
    draw();
  });
  const f = await p;
  if (!f) return;
  if (chosen === "new" || (chosen == null && f.q.value.trim())) {
    return safe(async () => { const r = await api("/api/collections", {name: f.q.value.trim(), ids}); rememberColl(r.id); await loadTree(); showInfo(true); toast("Raccolta creata"); });
  }
  if (chosen != null) addToCollection(chosen, ids);
}

async function removeFromCurrentCollection() {
  const [k, v] = S.scope.split(":");
  const c = k === "collection" && S.tree.collections.find(c => c.id == v);
  if (!c || c.set || !S.sel.size) return;
  const f = await dialog("Togli dalla raccolta", `<p>Togliere ${S.sel.size} foto dalla raccolta «${esc(c.name)}»?</p><p>I file non vengono toccati.</p>`, "Togli");
  if (!f) return;
  await safe(async () => { await api(`/api/collections/${c.id}/remove`, {ids: [...S.sel]}); await loadTree(); reload(true); S.sel.clear(); selChanged(); });
}

// ================================================================ pannello info
async function showInfo(refresh = false) {
  const box = $("#info");
  if (S.sel.size === 0) {
    S.detail = null;
    box.innerHTML = `<div class="empty">Seleziona una foto per vederne i dettagli,<br>le raccolte a cui appartiene e le parole chiave.<br><br>
      Doppio clic: vista ingrandita.<br>Trascina le foto su una raccolta per aggiungerle.</div>`;
    return;
  }
  if (S.sel.size > 1) return showMulti(box);
  const id = [...S.sel][0];
  if (!refresh || !S.detail || S.detail.id !== id) box.innerHTML = `<div class="prev"><img src="/thumb/${id}" alt=""></div>`;
  const d = await api(`/api/photo/${id}`).catch(() => null);
  if (!d || !S.sel.has(id) || S.sel.size !== 1) return;
  S.detail = d;
  const folder = d.rel_path ? d.rel_path : "(radice)";
  box.innerHTML = `
    <div class="prev"><img src="/thumb/${id}" alt="" onerror="this.remove()"></div>
    <div class="fname">${esc(d.filename)}</div>
    <div class="path">${esc(d.path)}</div>
    ${rateBars(d.rating, d.label, d.flag)}
    <div class="row">
      <button data-open="photoshop" title="⌘E">Apri in Photoshop</button>
      <button data-open="finder" title="⌘R">Mostra nel Finder</button>
    </div>
    <h4>Raccolte (${d.collections.length})</h4>
    <div class="chips">${d.collections.map(c => `<span class="chip" data-goto="collection:${c.id}" title="${esc(collPath(S.tree.collections.find(x => x.id === c.id) || c))}">${esc(c.name)}<button class="x" data-uncoll="${c.id}" title="Togli da questa raccolta">×</button></span>`).join("") || '<span class="small">In nessuna raccolta</span>'}</div>
    <div class="row"><button data-act="add-coll">＋ Aggiungi a raccolta…</button></div>
    <h4>Parole chiave</h4>
    <div class="chips">${d.keywords.map(k => `<span class="chip" data-goto="keyword:${k.id}">${esc(k.name)}<button class="x" data-unkw="${k.id}" title="Rimuovi">×</button></span>`).join("") || '<span class="small">Nessuna</span>'}</div>
    <div class="row"><input id="kw-add" list="kw-list" placeholder="Aggiungi (separa con virgola)…"><button data-act="kw-add">＋</button></div>
    <h4>Informazioni</h4>
    <table>
      <tr><td>Data scatto</td><td>${fmtDate(d.capture_time)}</td></tr>
      <tr><td>Formato</td><td>${esc((d.ext || "").toUpperCase())}</td></tr>
      ${d.width ? `<tr><td>Dimensioni</td><td>${d.width} × ${d.height}</td></tr>` : ""}
      <tr><td>Peso</td><td>${fmtSize(d.size)}</td></tr>
      <tr><td>Cartella</td><td><a href="#" data-goto="folder:${d.folder_id}">${esc(folder)}</a></td></tr>
      <tr><td>In catalogo dal</td><td>${fmtDate(d.added_at)}</td></tr>
      ${d.missing ? `<tr><td>Stato</td><td style="color:var(--red)">File non trovato</td></tr>` : ""}
    </table>`;
}

function rateBars(r, l, f) {
  return `<div class="row" style="justify-content:space-between;align-items:center">
    <div class="ratebar">${[1, 2, 3, 4, 5].map(n => `<button data-rate="${n}" class="${n <= (r || 0) ? "on" : ""}" title="${n} stelle (tasto ${n})">★</button>`).join("")}
      <button data-rate="0" title="Nessuna stella (tasto 0)" style="font-size:12px">⊘</button></div>
    <div class="ratebar"><button data-flag="1" class="${f === 1 ? "on" : ""}" title="Scelta (P)">⚑</button><button data-flag="-1" class="${f === -1 ? "on" : ""}" title="Scartata (X)">✕</button></div>
  </div>
  <div class="labelbar">${Object.entries(LABELS).map(([k, n]) => `<button data-label="${k}" class="lbl-${k}${l === k ? " on" : ""}" title="${n}"></button>`).join("")}
    <button data-label="" class="none${!l && l !== undefined ? "" : ""}" title="Nessuna etichetta"></button></div>`;
}

function showMulti(box) {
  box.innerHTML = `<div class="fname">${S.sel.size.toLocaleString("it-IT")} foto selezionate</div>
    <p class="small">Le modifiche si applicano a tutte le foto selezionate.</p>
    ${rateBars(-1, undefined, 2)}
    <h4>Raccolte</h4>
    <div class="row"><button data-act="add-coll">＋ Aggiungi a raccolta…</button><button data-act="new-coll-sel">Nuova raccolta con queste</button></div>
    ${S.scope.startsWith("collection:") ? `<div class="row"><button data-act="uncoll-sel">Togli dalla raccolta corrente</button></div>` : ""}
    <h4>Parole chiave</h4>
    <div class="row"><input id="kw-add" list="kw-list" placeholder="Aggiungi a tutte…"><button data-act="kw-add">＋</button></div>
    <h4>Apri</h4>
    <div class="row"><button data-open="photoshop">Apri in Photoshop${S.sel.size > 50 ? " (prime 50)" : ""}</button></div>`;
}

$("#info").addEventListener("click", async e => {
  const t = e.target.closest("[data-rate],[data-flag],[data-label],[data-open],[data-act],[data-uncoll],[data-unkw],[data-goto]");
  if (!t) return;
  e.preventDefault();
  const ids = [...S.sel];
  if (t.dataset.uncoll) { e.stopPropagation(); return safe(async () => { await api(`/api/collections/${t.dataset.uncoll}/remove`, {ids}); await loadTree(); if (S.scope === "collection:" + t.dataset.uncoll) reload(true); showInfo(true); }); }
  if (t.dataset.unkw) { e.stopPropagation(); return safe(async () => { await api("/api/keywords", {ids, remove: [+t.dataset.unkw]}); await loadTree(); showInfo(true); }); }
  if (t.dataset.goto) return setScope(t.dataset.goto);
  if (t.dataset.rate) return setMeta({rating: +t.dataset.rate});
  if (t.dataset.flag) { const cur = S.detail && S.sel.size === 1 ? S.detail.flag : null; return setMeta({flag: cur === +t.dataset.flag ? 0 : +t.dataset.flag}); }
  if (t.dataset.label !== undefined) return setMeta({label: t.dataset.label || null});
  if (t.dataset.open) return safe(() => api("/api/open", {ids, how: t.dataset.open}));
  const act = t.dataset.act;
  if (act === "add-coll") return pickCollection(ids);
  if (act === "new-coll-sel") return newCollection(false, ids);
  if (act === "uncoll-sel") return removeFromCurrentCollection();
  if (act === "kw-add") return addKeywords();
});
$("#info").addEventListener("keydown", e => { if (e.target.id === "kw-add" && e.key === "Enter") addKeywords(); });
async function addKeywords() {
  const inp = $("#kw-add"); const names = inp.value.split(",").map(s => s.trim()).filter(Boolean);
  if (!names.length) return;
  await safe(async () => { await api("/api/keywords", {ids: [...S.sel], add: names}); inp.value = ""; await loadTree(); showInfo(true); toast("Parole chiave aggiunte"); });
}

// ================================================================ vista ingrandita
let loupeIdx = -1;
function openLoupe(i) {
  const it = itemAt(i); if (!it) return;
  loupeIdx = i; S.cursor = i; S.sel.clear(); S.sel.add(it.id); selChanged();
  const img = $("#loupe-img");
  img.src = `/thumb/${it.id}`;
  const big = new Image();
  big.onload = () => { if (loupeIdx === i) img.src = big.src; };
  big.src = `/preview/${it.id}`;
  $("#loupe-name").textContent = `${it.n} · ${fmtDate(it.t)} ${it.r ? "· " + "★".repeat(it.r) : ""}`;
  $("#loupe").hidden = false;
}
async function loupeStep(d) {
  const i = Math.max(0, Math.min(S.total - 1, loupeIdx + d));
  if (!itemAt(i)) await loadPage(Math.floor(i / PAGE));
  ensureVisible(i); openLoupe(i);
}
$("#loupe-prev").onclick = () => loupeStep(-1);
$("#loupe-next").onclick = () => loupeStep(1);
$("#loupe").addEventListener("click", e => { if (e.target.id === "loupe") $("#loupe").hidden = true; });

// ================================================================ tastiera
document.addEventListener("keydown", async e => {
  if (e.target.matches("input, select, textarea") || $("#dlg").open) return;
  const loupe = !$("#loupe").hidden;
  const mod = e.metaKey || e.ctrlKey;
  if (e.key === "Escape") { if (loupe) $("#loupe").hidden = true; else { S.sel.clear(); selChanged(); } return; }
  if (loupe && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { e.preventDefault(); return loupeStep(e.key === "ArrowRight" ? 1 : -1); }
  if (mod && e.key.toLowerCase() === "a") {
    e.preventDefault();
    return safe(async () => { const r = await api(`/api/photos?${params({offset: 0, limit: 500000, ids_only: 1})}`); S.sel = new Set(r.ids); selChanged(); });
  }
  if (mod && e.key.toLowerCase() === "e") { e.preventDefault(); return safe(() => api("/api/open", {ids: [...S.sel], how: "photoshop"})); }
  if (mod && e.key.toLowerCase() === "r") { e.preventDefault(); return safe(() => api("/api/open", {ids: [...S.sel], how: "finder"})); }
  if (mod) return;
  if (/^[0-5]$/.test(e.key)) return setMeta({rating: +e.key});
  const lab = {"6": "red", "7": "yellow", "8": "green", "9": "blue"}[e.key];
  if (lab) { const cur = S.sel.size === 1 && S.detail ? S.detail.label : null; return setMeta({label: cur === lab ? null : lab}); }
  const k = e.key.toLowerCase();
  if (k === "p") return setMeta({flag: 1});
  if (k === "x") return setMeta({flag: -1});
  if (k === "u") return setMeta({flag: 0});
  if (k === "c" || k === "b") { e.preventDefault(); return pickCollection([...S.sel]); }
  if (e.key === "Enter" || e.key === " ") { if (S.cursor >= 0) { e.preventDefault(); loupe ? ($("#loupe").hidden = true) : openLoupe(S.cursor); } return; }
  if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); return removeFromCurrentCollection(); }
  const moves = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: S.cols, ArrowUp: -S.cols};
  if (moves[e.key] && S.total) {
    e.preventDefault();
    const i = Math.max(0, Math.min(S.total - 1, (S.cursor < 0 ? 0 : S.cursor + moves[e.key])));
    if (!itemAt(i)) await loadPage(Math.floor(i / PAGE));
    ensureVisible(i);
    if (e.shiftKey) await clickCell(i, {shiftKey: true}); else { S.anchor = i; await clickCell(i, {}); }
  }
});

// ================================================================ finestre di dialogo
function dialog(title, html, okLabel = "OK", setup) {
  const dlg = $("#dlg"), form = $("#dlg-form");
  form.innerHTML = `<h2>${esc(title)}</h2>${html}<div class="btns"><button type="button" value="cancel" data-cancel>Annulla</button>${okLabel ? `<button class="primary" value="ok">${esc(okLabel)}</button>` : ""}</div>`;
  return new Promise(resolve => {
    let done = false;
    const finish = v => { if (done) return; done = true; resolve(v); };
    form.querySelector("[data-cancel]").onclick = () => { dlg.close(); finish(null); };
    form.onsubmit = e => { e.preventDefault(); dlg.close(); finish(form); };
    dlg.onclose = () => finish(null);
    dlg.showModal();
    if (setup) setup(form);
    (form.querySelector("[autofocus]") || form.querySelector("input"))?.focus();
  });
}

async function pickInto(input, kind) {
  const r = await safe(() => api("/api/pick", {kind}));
  if (r && r.path) input.value = r.path;
  else if (r) toast("Scrivi il percorso a mano (la finestra di scelta funziona solo su Mac)");
}

async function addFolderDialog() {
  const f = await dialog("Aggiungi cartella di foto", `
    <p>Scegli una cartella sul NAS (es. <code>/Volumes/Foto/2024</code>). Verranno catalogate tutte le foto contenute, comprese le sottocartelle. I file non vengono modificati né spostati.</p>
    <label>Cartella</label><div class="pickrow"><input type="text" name="path" required placeholder="/Volumes/…"><button type="button" data-pick>Scegli…</button></div>`,
    "Aggiungi e scansiona", form => { form.querySelector("[data-pick]").onclick = () => pickInto(form.path, "folder"); });
  if (!f) return;
  await safe(async () => { await api("/api/roots/add", {path: f.path.value}); toast("Scansione avviata"); pollJobs(); loadTree(); });
}

async function importLrDialog() {
  const f = await dialog("Importa catalogo Lightroom", `
    <p>Importa da un catalogo Lightroom Classic (<code>.lrcat</code>): cartelle, foto, <b>raccolte e set di raccolte</b>, parole chiave, stelle, etichette colore e bandierine.</p>
    <p><b>Chiudi Lightroom</b> prima di importare. Il catalogo di Lightroom viene solo letto, mai modificato.
    Le raccolte smart non vengono importate.</p>
    <label>File catalogo</label><div class="pickrow"><input type="text" name="path" required placeholder="/Users/…/Lightroom Catalog.lrcat"><button type="button" data-pick>Scegli…</button></div>`,
    "Importa", form => { form.querySelector("[data-pick]").onclick = () => pickInto(form.path, "file"); });
  if (!f) return;
  await safe(async () => { await api("/api/import/lightroom", {path: f.path.value}); toast("Importazione avviata"); pollJobs(); });
}

async function relocateDialog() {
  const roots = S.tree.roots;
  const f = await dialog("Cambia percorso del NAS", `
    <p>Se il NAS viene montato con un nome diverso (es. da <code>/Volumes/Foto</code> a <code>/Volumes/Foto-1</code>), o le foto vengono spostate su un altro disco, sostituisci qui l'inizio del percorso. Raccolte e parole chiave restano intatte.</p>
    <p>Cartelle attuali:<br>${roots.map(r => `<span class="dot ${r.online ? "on" : "off"}"></span> <code>${esc(r.path)}</code>`).join("<br>") || "nessuna"}</p>
    <label>Sostituisci</label><input type="text" name="from" required value="${esc(roots[0]?.path || "")}">
    <label>Con</label><div class="pickrow"><input type="text" name="to" required><button type="button" data-pick>Scegli…</button></div>`,
    "Sostituisci", form => { form.querySelector("[data-pick]").onclick = () => pickInto(form.to, "folder"); });
  if (!f) return;
  await safe(async () => { const r = await api("/api/roots/relocate", {from: f.from.value, to: f.to.value}); toast(`Percorsi aggiornati: ${r.changed}`); loadTree(); });
}

async function removeRoot(rid) {
  const r = S.tree.roots.find(r => r.id === rid);
  const f = await dialog("Rimuovi cartella dal catalogo", `<p>Rimuovere «${esc(r.name)}» (<code>${esc(r.path)}</code>) dal catalogo?</p>
    <p>Le foto verranno tolte anche dalle raccolte. <b>I file sul NAS non vengono cancellati.</b></p>`, "Rimuovi");
  if (!f) return;
  await safe(async () => { await api(`/api/roots/${rid}/delete`, {}); setScope("all"); await loadTree(); });
}

function helpDialog() {
  const rows = [["Clic / ⌘-clic / ⇧-clic", "Seleziona / aggiungi alla selezione / intervallo"], ["⌘A", "Seleziona tutte le foto della vista"],
    ["Frecce", "Spostati tra le foto"], ["Invio, Spazio, doppio clic", "Vista ingrandita"], ["0 – 5", "Stelle"],
    ["6 7 8 9", "Etichetta rossa, gialla, verde, blu"], ["P / X / U", "Scelta / Scartata / Togli bandierina"],
    ["C oppure B", "Aggiungi a raccolta…"], ["⌫ Backspace", "Togli dalla raccolta corrente"], ["⌘E", "Apri in Photoshop"],
    ["⌘R", "Mostra nel Finder"], ["Esc", "Chiudi vista / deseleziona"]];
  dialog("Scorciatoie da tastiera", `<table>${rows.map(([k, v]) => `<tr><td><kbd>${k}</kbd></td><td>${v}</td></tr>`).join("")}</table>
    <p>Trascina le foto su una raccolta per aggiungerle. Trascina una raccolta su un set per spostarla.</p>`, null);
}

// ================================================================ menu
$("#btn-library").onclick = e => { e.stopPropagation(); $("#menu-library").hidden = !$("#menu-library").hidden; };
document.addEventListener("click", () => $("#menu-library").hidden = true);
$("#menu-library").addEventListener("click", e => {
  const a = e.target.closest("[data-act]")?.dataset.act;
  if (a === "add-folder") addFolderDialog();
  if (a === "import-lr") importLrDialog();
  if (a === "relocate") relocateDialog();
  if (a === "help") helpDialog();
  if (a === "pregen") safe(async () => {
    await api("/api/thumbs/pregenerate", Object.fromEntries(new URLSearchParams(params())));
    toast("Generazione anteprime avviata in background"); pollJobs();
  });
});

// ================================================================ filtri
let searchT;
$("#search").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(() => reload(), 300); });
for (const id of ["#f-rating", "#f-label", "#f-flag", "#f-year", "#f-ext", "#sort", "#deep"])
  $(id).addEventListener("change", () => { if (id === "#sort") store.set("sort", $("#sort").value); reload(); });
$("#sort").value = store.get("sort", "date:asc");

// ================================================================ lavori in background
let pollTimer = null;
const seenDone = new Set();
async function pollJobs() {
  clearTimeout(pollTimer);
  const jobs = await api("/api/jobs").catch(() => []);
  const bar = $("#jobs");
  let running = false;
  bar.innerHTML = jobs.map(j => {
    if (j.status === "running") {
      running = true;
      return `<span class="job">⏳ ${esc(j.label)}: ${esc(j.message)} ${j.total ? `<progress value="${j.done}" max="${j.total}"></progress>` : ""}</span>`;
    }
    if (!seenDone.has(j.id)) {
      seenDone.add(j.id);
      onJobDone(j);
    }
    return j.status === "error" ? `<span class="job err" title="${esc(j.message)}">✕ ${esc(j.label)}: ${esc(j.message)}</span>`
      : `<span class="job">✓ ${esc(j.label)}</span>`;
  }).join("") + (jobs.some(j => j.status !== "running") ? ` <button class="mini" id="jobs-clear">pulisci</button>` : "");
  $("#jobs-clear")?.addEventListener("click", async () => { await api("/api/jobs/clear", {}); pollJobs(); });
  if (running) pollTimer = setTimeout(pollJobs, 1000);
}
async function onJobDone(j) {
  if (j.status !== "done" || !j.result) return;
  await loadTree(); reload(true);
  const r = j.result;
  if ("raccolte" in r) {
    dialog("Importazione completata", `<table>
      <tr><td>Foto</td><td>${r.foto.toLocaleString("it-IT")}</td></tr><tr><td>Cartelle</td><td>${r.cartelle.toLocaleString("it-IT")}</td></tr>
      <tr><td>Raccolte</td><td>${r.raccolte}</td></tr><tr><td>Set di raccolte</td><td>${r.set}</td></tr>
      <tr><td>Parole chiave</td><td>${r.parole_chiave}</td></tr><tr><td>Raccolte smart (non importate)</td><td>${r.raccolte_smart_saltate}</td></tr></table>
      <p>Consiglio: con il NAS collegato, usa ⟳ accanto a ogni cartella per leggere dimensioni e date dei file,
      poi <b>Libreria › Genera anteprime</b> per creare in anticipo le miniature (anche di notte).</p>`, "OK");
  } else if ("new" in r) {
    toast(`Sincronizzazione: ${r.total} foto, ${r.new} nuove, ${r.missing} mancanti`);
  }
}

// ================================================================ avvio
(async () => {
  for (const j of await api("/api/jobs").catch(() => [])) if (j.status !== "running") seenDone.add(j.id);
  await safe(loadTree);
  if (!scopeTitle(S.scope) || scopeTitle(S.scope) === "?") S.scope = "all";
  $("#view-title").textContent = scopeTitle(S.scope);
  renderSidebar();
  await reload();
  pollJobs();
  scroller.focus();
})();
