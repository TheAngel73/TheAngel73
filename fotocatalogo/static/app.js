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
  if (data.nocatalog && CAT) { location.reload(); throw new Error("Catalogo chiuso"); }
  if (!r.ok || data.error) throw new Error(data.error || `Errore ${r.status}`);
  return data;
}
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = isError ? "error" : ""; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, isError ? 6000 : 2500);
}
async function safe(fn) { try { return await fn(); } catch (e) { toast(e.message, true); } }
// catalogo aperto: {path, name, protected, uid}. Le preferenze di vista sono per catalogo.
let CAT = null;
const GLOBAL_KEYS = new Set(["zoom", "sort"]);
const skey = k => "fc:" + (GLOBAL_KEYS.has(k) || !CAT ? "" : CAT.uid + ":") + k;
const thumbURL = id => `/thumb/${id}?c=${CAT ? CAT.uid : ""}`;
const previewURL = id => `/preview/${id}?c=${CAT ? CAT.uid : ""}`;
const store = {
  get(k, d) { try { const v = localStorage.getItem(skey(k)); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(skey(k), JSON.stringify(v)); } catch {} },
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
  tree: null, scope: "all",
  total: 0, pages: new Map(), loading: new Set(), version: 0,
  sel: new Set(), anchor: -1, cursor: -1,
  expanded: new Set(),
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

// tipo di file -> triangolino nell'angolo
const RAW_PROP = new Set(["nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "raf", "orf", "rw2", "pef", "srw",
  "x3f", "3fr", "iiq", "erf", "mos", "mrw", "raw", "rwl", "kdc", "dcr", "mef"]);
function fileType(x) {
  x = (x || "").toLowerCase();
  if (x === "jpg" || x === "jpeg" || x === "jpe") return ["jpg", "JPEG"];
  if (x === "psd" || x === "psb") return ["psd", "Photoshop (" + x.toUpperCase() + ")"];
  if (RAW_PROP.has(x)) return ["raw", "RAW proprietario (" + x.toUpperCase() + ") da convertire"];
  return null;
}

function makeCell(it, i) {
  const el = document.createElement("div");
  el.className = "cell"; el.dataset.i = i;
  if (!it) { el.innerHTML = `<div class="im"></div>`; return el; }
  el.draggable = true;
  if (it.f === -1) { el.classList.add("rejected"); el.title = "Scartata"; }
  if (it.l) el.classList.add("lab", "lab-" + it.l);
  const ft = fileType(it.x);
  const stars = it.r ? "★".repeat(it.r) : "";
  el.innerHTML = `<div class="im"><img loading="lazy" decoding="async" alt="" src="${thumbURL(it.id)}"></div>
    ${ft ? `<span class="ftype ft-${ft[0]}" title="${ft[1]}"></span>` : ""}
    ${it.f === 1 ? '<span class="flag" title="Scelta">⚑</span>' : ""}
    ${it.m ? '<span class="miss" title="File non trovato">⚠ mancante</span>' : ""}
    <div class="cap"${it.l ? ` title="Etichetta ${LABELS[it.l] || ""}"` : ""}><span class="fn" title="${esc(it.n)}">${esc(it.n)}</span><span class="stars">${stars}</span></div>`;
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
  const fixed = {all: "Tutte le foto", nocoll: "Foto senza raccolta", toconvert: "Da convertire (non DNG, JPEG o PSD)", missing: "File mancanti", recent: "Importazione più recente"};
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
    ["nocoll", "◌", "Senza raccolta", t.stats.nocoll],
    ["toconvert", "◭", "Da convertire", t.stats.toconvert]];
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

async function newCollection(isSet, ids = [], parentOverride) {
  const parent = parentOverride !== undefined ? parentOverride : (() => { const [k, v] = S.scope.split(":"); const c = k === "collection" && S.tree.collections.find(c => c.id == v); return c ? (c.set ? c.id : c.parent) : null; })();
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
  if (!refresh || !S.detail || S.detail.id !== id) box.innerHTML = `<div class="prev"><img src="${thumbURL(id)}" alt=""></div>`;
  const d = await api(`/api/photo/${id}`).catch(() => null);
  if (!d || !S.sel.has(id) || S.sel.size !== 1) return;
  S.detail = d;
  const folder = d.rel_path ? d.rel_path : "(radice)";
  box.innerHTML = `
    <div class="prev"><img src="${thumbURL(id)}" alt="" onerror="this.remove()"></div>
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
  img.src = thumbURL(it.id);
  const big = new Image();
  big.onload = () => { if (loupeIdx === i) img.src = big.src; };
  big.src = previewURL(it.id);
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
  const shortcut = mod && {a: "selectAll", e: "photoshop", r: "finder", k: "keywords", d: "selectNone"}[e.key.toLowerCase()];
  // nell'app per Mac queste scorciatoie arrivano dalla barra dei menu
  if (shortcut && !NATIVE) { e.preventDefault(); return COMMANDS[shortcut](); }
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
  if (NATIVE) { const path = await nativePick(kind); if (path) input.value = path; return; }
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
    ["⌘R", "Mostra nel Finder"], ["⌘K", "Aggiungi parole chiave"], ["⌘D / Esc", "Deseleziona"],
    ["Tasto destro", "Menu con tutte le azioni"]];
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
  if (a === "pregen") COMMANDS.pregen();
});

// ================================================================ comandi (tastiera, menu dell'app, tasto destro)
const NATIVE = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.fc);
if (NATIVE) document.documentElement.classList.add("native");
const selIds = () => [...S.sel];
const needSel = fn => () => S.sel.size ? fn() : toast("Seleziona prima una o più foto");
const openWith = how => needSel(() => safe(() => api("/api/open", {ids: selIds(), how})));

const COMMANDS = {
  addFolder: addFolderDialog, importLr: importLrDialog, relocate: relocateDialog, help: helpDialog,
  pregen: () => safe(async () => {
    await api("/api/thumbs/pregenerate", Object.fromEntries(new URLSearchParams(params())));
    toast("Generazione anteprime avviata in background"); pollJobs();
  }),
  newColl: () => newCollection(false, selIds()), newSet: () => newCollection(true),
  selectAll: () => {
    const a = document.activeElement;
    if (a && a.matches("input, textarea")) return a.select();
    return safe(async () => { const r = await api(`/api/photos?${params({offset: 0, limit: 1000000, ids_only: 1})}`); S.sel = new Set(r.ids); selChanged(); });
  },
  selectNone: () => { S.sel.clear(); selChanged(); },
  find: () => $("#search").focus(),
  photoshop: openWith("photoshop"), finder: openWith("finder"), openDefault: openWith("default"),
  loupe: needSel(() => { if (S.cursor >= 0) openLoupe(S.cursor); }),
  addToColl: needSel(() => pickCollection(selIds())),
  removeFromColl: () => removeFromCurrentCollection(),
  keywords: needSel(() => keywordDialog()),
  switchCatalog: () => showCatalogScreen(null, false),
  lockCatalog: () => lockCatalog(),
  zoomIn: () => { $("#zoom").value = +$("#zoom").value + 40; $("#zoom").dispatchEvent(new Event("input")); },
  zoomOut: () => { $("#zoom").value = +$("#zoom").value - 40; $("#zoom").dispatchEvent(new Event("input")); },
};

// ponte con l'app per Mac (FotoCatalogo.app): i menu nativi chiamano fc.cmd("...")
const pickWaiters = new Map();
let pickSeq = 0;
function nativePick(kind) {
  return new Promise(res => {
    const id = ++pickSeq; pickWaiters.set(id, res);
    window.webkit.messageHandlers.fc.postMessage({cmd: "pick", kind, id});
  });
}
window.fc = {
  cmd(name) {
    if ($("#dlg").open && !["selectAll"].includes(name)) return;
    if (!CAT && !["selectAll", "help"].includes(name)) return;   // c'è già la schermata dei cataloghi
    COMMANDS[name]?.();
  },
  pickResult(id, path) { pickWaiters.get(id)?.(path); pickWaiters.delete(id); },
};
// file trascinati dal Finder: non devono far "navigare" la finestra
for (const ev of ["dragover", "drop"]) document.addEventListener(ev, e => {
  if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault();
});

async function keywordDialog() {
  const n = S.sel.size;
  const f = await dialog(`Parole chiave per ${n === 1 ? "la foto" : n + " foto"}`, `
    <label>Aggiungi (separa con la virgola)</label><input type="text" name="kw" list="kw-list" autocomplete="off" required>`, "Aggiungi");
  if (!f) return;
  const names = f.kw.value.split(",").map(s => s.trim()).filter(Boolean);
  await safe(async () => { await api("/api/keywords", {ids: selIds(), add: names}); await loadTree(); showInfo(true); toast("Parole chiave aggiunte"); });
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = Object.assign(document.createElement("textarea"), {value: text});
    document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Percorso copiato");
}
function folderFullPath(fid) {
  const f = S.tree.folders.find(f => f.id === fid); if (!f) return "";
  const r = S.tree.roots.find(r => r.id === f.root);
  return r.path + (f.rel ? "/" + f.rel : "");
}

// ---------------------------------------------------------------- menu contestuale
const ctx = Object.assign(document.createElement("div"), {id: "ctx", hidden: true});
document.body.appendChild(ctx);

function buildMenu(items) {
  const m = document.createElement("div"); m.className = "cm";
  for (const it of items) {
    if (!it) continue;
    if (it === "-") { if (m.lastChild && m.lastChild.className !== "cm-sep") m.appendChild(Object.assign(document.createElement("div"), {className: "cm-sep"})); continue; }
    const row = document.createElement("div");
    row.className = "cmi" + (it.disabled ? " dis" : "") + (it.sub ? " has-sub" : "") + (it.danger ? " danger" : "");
    row.innerHTML = `<span class="ck">${it.checked ? "✓" : ""}</span>${it.swatch ? `<span class="dot lbl-${it.swatch}"></span>` : ""}` +
      `<span class="lb">${esc(it.label)}</span><span class="kb">${esc(it.key || "")}${it.sub ? "▸" : ""}</span>`;
    if (it.sub) { if (!it.sub.filter(Boolean).length) continue; row.appendChild(buildMenu(it.sub)); }
    else if (!it.disabled) row.addEventListener("click", e => { e.stopPropagation(); hideMenu(); it.run(); });
    m.appendChild(row);
  }
  while (m.lastChild && m.lastChild.className === "cm-sep") m.lastChild.remove();
  return m;
}
function showMenu(x, y, items) {
  ctx.innerHTML = ""; ctx.appendChild(buildMenu(items)); ctx.hidden = false;
  const r = ctx.firstChild.getBoundingClientRect();
  const left = Math.min(x, innerWidth - r.width - 6), top = Math.min(y, innerHeight - r.height - 6);
  ctx.style.left = Math.max(4, left) + "px"; ctx.style.top = Math.max(4, top) + "px";
  ctx.classList.toggle("flip", left + r.width + 220 > innerWidth);
}
function hideMenu() { ctx.hidden = true; }
document.addEventListener("mousedown", e => { if (!ctx.contains(e.target)) hideMenu(); }, true);
document.addEventListener("keydown", e => { if (e.key === "Escape" && !ctx.hidden) { e.stopImmediatePropagation(); hideMenu(); } }, true);
window.addEventListener("blur", hideMenu);
scroller.addEventListener("scroll", hideMenu);

document.addEventListener("contextmenu", async e => {
  if (e.target.closest("input, textarea")) return;   // menu di sistema (copia/incolla)
  e.preventDefault();
  hideMenu();
  const items = await menuFor(e.target);
  if (items) showMenu(e.clientX, e.clientY, items);
});

async function menuFor(t) {
  if (t.closest("#dlg, #ctx")) return null;
  const cell = t.closest(".cell");
  if (cell) {
    const i = +cell.dataset.i, it = itemAt(i);
    if (!it) return null;
    if (!S.sel.has(it.id)) await clickCell(i, {});
    return photoMenu();
  }
  const chip = t.closest(".chip[data-goto]");
  if (chip) {
    const [k, v] = chip.dataset.goto.split(":");
    if (k === "collection") return collMenu(+v, true);
    if (k === "keyword") return kwMenu(+v, true);
  }
  if (t.closest("#info")) return S.sel.size ? photoMenu() : null;
  if (t.closest("#loupe")) return photoMenu();
  const li = t.closest("#sidebar li[data-scope]");
  if (li) {
    if (li.dataset.coll) return collMenu(+li.dataset.coll);
    if (li.dataset.folder) return folderMenu(+li.dataset.folder);
    if (li.dataset.scope.startsWith("keyword:")) return kwMenu(+li.dataset.scope.split(":")[1]);
    return [{label: "Apri", run: () => setScope(li.dataset.scope)}];
  }
  if (t.closest("#coll-tree") || t.closest("#sidebar h3")?.textContent.startsWith("Raccolte"))
    return [{label: "Nuova raccolta…", run: () => newCollection(false, [], null)}, {label: "Nuovo set di raccolte…", run: () => newCollection(true, [], null)}];
  if (t.closest("#grid-scroll")) return [
    {label: "Seleziona tutte", key: "⌘A", run: COMMANDS.selectAll},
    {label: "Deseleziona", key: "⌘D", run: COMMANDS.selectNone, disabled: !S.sel.size},
    "-", {label: "Genera anteprime di questa vista", run: COMMANDS.pregen},
    {label: "Miniature", sub: [{label: "Più grandi", key: "⌘+", run: COMMANDS.zoomIn}, {label: "Più piccole", key: "⌘−", run: COMMANDS.zoomOut}]},
  ];
  return null;
}

async function photoMenu() {
  const n = S.sel.size, one = n === 1;
  let d = null;
  if (one) d = (S.detail && S.sel.has(S.detail.id)) ? S.detail : await api(`/api/photo/${selIds()[0]}`).catch(() => null);
  const [k, v] = S.scope.split(":");
  const cur = k === "collection" && S.tree.collections.find(c => c.id == v && !c.set);
  const recent = store.get("recentColl", []).map(id => S.tree.collections.find(c => c.id === id)).filter(c => c && !c.set);
  const what = one ? "la foto" : `${n.toLocaleString("it-IT")} foto`;
  return [
    {label: one ? "Apri in Photoshop" : `Apri ${Math.min(n, 50)} foto in Photoshop`, key: "⌘E", run: COMMANDS.photoshop},
    {label: "Apri con l'app predefinita", run: COMMANDS.openDefault},
    {label: "Mostra nel Finder", key: "⌘R", run: COMMANDS.finder},
    one && $("#loupe").hidden && {label: "Vista ingrandita", key: "Spazio", run: COMMANDS.loupe},
    "-",
    {label: `Aggiungi ${what} a una raccolta…`, key: "C", run: COMMANDS.addToColl},
    recent.length && {label: "Aggiungi a raccolta recente", sub: recent.map(c => ({label: c.name, run: () => addToCollection(c.id, selIds())}))},
    {label: `Nuova raccolta con ${one ? "questa foto" : "queste foto"}…`, run: () => newCollection(false, selIds())},
    cur && {label: `Togli da «${cur.name}»`, key: "⌫", run: COMMANDS.removeFromColl},
    d && d.collections.length && {label: `Raccolte di questa foto (${d.collections.length})`, sub: d.collections.map(c => ({label: c.name, run: () => setScope("collection:" + c.id)}))},
    "-",
    {label: "Valutazione", sub: [0, 1, 2, 3, 4, 5].map(r => ({label: r ? "★".repeat(r) : "Nessuna stella", key: String(r), checked: d && d.rating === r, run: () => setMeta({rating: r})}))},
    {label: "Etichetta colore", sub: [...Object.entries(LABELS).map(([lk, name], i) => ({label: name, swatch: lk, key: i < 4 ? String(6 + i) : "", checked: d && d.label === lk, run: () => setMeta({label: lk})})),
      "-", {label: "Nessuna etichetta", checked: d && !d.label, run: () => setMeta({label: null})}]},
    {label: "Bandierina", sub: [{label: "Scelta", key: "P", checked: d && d.flag === 1, run: () => setMeta({flag: 1})},
      {label: "Scartata", key: "X", checked: d && d.flag === -1, run: () => setMeta({flag: -1})},
      {label: "Nessuna", key: "U", checked: d && !d.flag, run: () => setMeta({flag: 0})}]},
    {label: "Aggiungi parole chiave…", key: "⌘K", run: COMMANDS.keywords},
    d && d.keywords.length && {label: "Togli parola chiave", sub: d.keywords.map(kw => ({label: kw.name, run: () => safe(async () => { await api("/api/keywords", {ids: selIds(), remove: [kw.id]}); await loadTree(); showInfo(true); })}))},
    "-",
    d && {label: "Vai alla cartella", run: () => setScope("folder:" + d.folder_id)},
    d && {label: "Copia percorso del file", run: () => copyText(d.path)},
  ];
}

function collMenu(cid, fromChip = false) {
  const c = S.tree.collections.find(c => c.id === cid); if (!c) return null;
  const n = S.sel.size;
  return [
    {label: c.set ? "Mostra tutte le foto del set" : "Apri raccolta", run: () => setScope("collection:" + cid)},
    !c.set && n && !fromChip && {label: `Aggiungi le ${n.toLocaleString("it-IT")} foto selezionate`, run: () => addToCollection(cid, selIds())},
    !c.set && n && fromChip && {label: n === 1 ? "Togli la foto da questa raccolta" : "Togli le foto da questa raccolta",
      run: () => safe(async () => { await api(`/api/collections/${cid}/remove`, {ids: selIds()}); await loadTree(); if (S.scope === "collection:" + cid) reload(true); showInfo(true); })},
    "-",
    c.set && {label: "Nuova raccolta in questo set…", run: () => newCollection(false, [], cid)},
    c.set && {label: "Nuovo set in questo set…", run: () => newCollection(true, [], cid)},
    {label: "Rinomina o sposta…", run: () => editCollection(cid)},
    "-",
    {label: c.set ? "Elimina set…" : "Elimina raccolta…", danger: true, run: () => deleteCollection(cid)},
  ];
}

function folderMenu(fid) {
  const f = S.tree.folders.find(f => f.id === fid); if (!f) return null;
  const root = S.tree.roots.find(r => r.id === f.root);
  const isRoot = f.parent == null;
  return [
    {label: "Mostra le foto", run: () => setScope("folder:" + fid)},
    {label: "Apri nel Finder", run: () => safe(() => api("/api/open", {folder: fid}))},
    {label: "Copia percorso", run: () => copyText(folderFullPath(fid))},
    "-",
    {label: `Sincronizza «${isRoot ? root.name : f.name}»`, run: () => safe(async () => { await api(`/api/folders/${fid}/rescan`, {}); pollJobs(); })},
    {label: "Genera anteprime di questa cartella", run: () => safe(async () => { await api("/api/thumbs/pregenerate", {scope: "folder:" + fid, deep: "1"}); toast("Generazione anteprime avviata"); pollJobs(); })},
    isRoot && "-",
    isRoot && {label: "Cambia percorso del NAS…", run: COMMANDS.relocate},
    isRoot && {label: "Rimuovi dal catalogo…", danger: true, run: () => removeRoot(root.id)},
  ];
}

function kwMenu(kid, fromChip = false) {
  const k = S.tree.keywords.find(k => k.id === kid); if (!k) return null;
  const n = S.sel.size;
  return [
    {label: "Mostra le foto", run: () => setScope("keyword:" + kid)},
    n && !fromChip && {label: `Aggiungi alle ${n.toLocaleString("it-IT")} foto selezionate`, run: () => safe(async () => { await api("/api/keywords", {ids: selIds(), add: [k.name]}); await loadTree(); showInfo(true); toast("Parola chiave aggiunta"); })},
    n && {label: "Togli dalle foto selezionate", run: () => safe(async () => { await api("/api/keywords", {ids: selIds(), remove: [kid]}); await loadTree(); showInfo(true); })},
    "-",
    {label: "Rinomina…", run: async () => {
      const f = await dialog("Rinomina parola chiave", `<label>Nome</label><input type="text" name="name" value="${esc(k.name)}" required>`, "Rinomina");
      if (f) safe(async () => { await api("/api/keywords/rename", {id: kid, name: f.name.value}); await loadTree(); showInfo(true); });
    }},
    {label: "Elimina parola chiave…", danger: true, run: async () => {
      const f = await dialog("Elimina parola chiave", `<p>Eliminare «${esc(k.name)}»? Verrà tolta da ${k.count.toLocaleString("it-IT")} foto. Le foto non vengono toccate.</p>`, "Elimina");
      if (f) safe(async () => { await api("/api/keywords/delete", {id: kid}); if (S.scope === "keyword:" + kid) setScope("all"); await loadTree(); showInfo(true); });
    }},
  ];
}

// ================================================================ cataloghi
function updateCatalogButton() {
  const b = $("#btn-catalog");
  b.innerHTML = `${CAT.protected ? "🔒 " : ""}${esc(CAT.name)} ▾`;
  b.title = CAT.path;
}

$("#btn-catalog").onclick = async e => {
  e.stopPropagation();
  const r = e.currentTarget.getBoundingClientRect();
  const cs = await api("/api/catalogs");
  const others = cs.catalogs.filter(c => !c.open);
  showMenu(r.left, r.bottom + 4, [
    {label: CAT.name, checked: true, disabled: true},
    ...others.map(c => ({label: (c.protected ? "🔒 " : "") + c.name, run: () => openCatalogFlow(c)})),
    "-",
    {label: "Gestisci cataloghi…", key: NATIVE ? "⌥⌘O" : "", run: () => showCatalogScreen(cs, false)},
    {label: "Nuovo catalogo…", run: () => newCatalogDialog()},
    "-",
    {label: "Apri questo catalogo all'avvio", checked: cs.default === CAT.path,
      run: () => safe(async () => { await api("/api/catalogs/default", {path: cs.default === CAT.path ? null : CAT.path}); toast(cs.default === CAT.path ? "All'avvio si aprirà l'ultimo catalogo usato" : `All'avvio si aprirà «${CAT.name}»`); })},
    CAT.protected ? {label: "Blocca catalogo", key: NATIVE ? "⌘L" : "", run: lockCatalog} : {label: "Proteggi con password…", run: protectDialog},
    CAT.protected && {label: "Cambia password…", run: passwordDialog},
  ]);
};

async function openCatalogFlow(c) {
  let password = null;
  if (c.protected) {
    const f = await dialog(`🔒 ${c.name}`, `<p>Questo catalogo è protetto.</p>
      <label>Password</label><input type="password" name="pw" required autofocus>`, "Sblocca");
    if (!f) return;
    password = f.pw.value;
  }
  await safe(async () => { await api("/api/catalogs/open", {path: c.path, password}); location.reload(); });
}

async function lockCatalog() {
  if (!CAT || !CAT.protected) return;
  await safe(async () => { await api("/api/catalogs/lock", {}); CAT = null; location.reload(); });
}

async function newCatalogDialog() {
  const f = await dialog("Nuovo catalogo", `
    <p>Il catalogo verrà salvato in <code>Documenti/FotoCatalogo</code>.</p>
    <label>Nome</label><input type="text" name="name" required autofocus placeholder="es. Personale">
    <label class="check"><input type="checkbox" name="prot"> Proteggi con password (catalogo cifrato)</label>
    <div class="pw" hidden>
      <label>Password</label><input type="password" name="pw" autocomplete="new-password">
      <label>Ripeti la password</label><input type="password" name="pw2" autocomplete="new-password">
      <p class="warn">Se dimentichi la password il catalogo non è recuperabile: annotala in un posto sicuro.</p>
    </div>
    <label class="check"><input type="checkbox" name="def"> Apri questo catalogo all'avvio</label>`, "Crea e apri", form => {
      form.prot.onchange = () => { form.querySelector(".pw").hidden = !form.prot.checked; form.pw.required = form.pw2.required = form.prot.checked; };
    });
  if (!f) return;
  await safe(async () => {
    await api("/api/catalogs/create", {name: f.name.value, password: f.prot.checked ? f.pw.value : null,
      password2: f.prot.checked ? f.pw2.value : null, default: f.def.checked});
    location.reload();
  });
}

async function protectDialog() {
  const f = await dialog(`Proteggi «${CAT.name}» con password`, `
    <p>Il catalogo verrà spostato in un'<b>immagine disco cifrata</b> di macOS (AES-256), insieme alle sue miniature.
    Senza password nessuno potrà vederne raccolte, parole chiave e anteprime, nemmeno aprendo i file con altri programmi.</p>
    <p>La copia non cifrata viene cancellata dopo aver verificato che il trasferimento è riuscito. Con un catalogo grande può richiedere qualche minuto.</p>
    <label>Password</label><input type="password" name="pw" required autofocus autocomplete="new-password">
    <label>Ripeti la password</label><input type="password" name="pw2" required autocomplete="new-password">
    <p class="warn">Se dimentichi la password il catalogo non è recuperabile.</p>
    <p>Nota: le foto sul NAS restano accessibili come prima. La password protegge il catalogo, non i file sul NAS.</p>`, "Proteggi");
  if (!f) return;
  toast("Protezione in corso… non chiudere l'app");
  await safe(async () => { await api("/api/catalogs/protect", {password: f.pw.value, password2: f.pw2.value}); location.reload(); });
}

async function passwordDialog() {
  const f = await dialog(`Cambia password di «${CAT.name}»`, `
    <label>Password attuale</label><input type="password" name="old" required autofocus>
    <label>Nuova password</label><input type="password" name="new" required autocomplete="new-password">
    <label>Ripeti la nuova password</label><input type="password" name="new2" required autocomplete="new-password">`, "Cambia");
  if (!f) return;
  await safe(async () => { await api("/api/catalogs/password", {old: f.old.value, new: f.new.value, new2: f.new2.value}); toast("Password cambiata"); });
}

// Schermata di scelta: all'avvio (catalogo protetto da sbloccare) o da "Gestisci cataloghi"
async function showCatalogScreen(cs, startup) {
  cs = cs || await api("/api/catalogs").catch(() => null);
  if (!cs) { document.body.innerHTML = `<div class="empty">Il motore non risponde.</div>`; return; }
  const el = $("#catscreen");
  let sel = cs.catalogs.find(c => c.path === (startup ? cs.startup : (cs.current?.path || cs.startup))) || cs.catalogs[0];
  const draw = (err = "") => {
    el.innerHTML = `<div class="cs-box">
      <h1>${startup ? "FotoCatalogo" : "Cataloghi"}</h1>
      <p class="small">Cartella: <code>${esc(cs.base)}</code></p>
      <div class="cs-list">${cs.catalogs.map(c => `<div class="cs-item${c === sel ? " on" : ""}" data-path="${esc(c.path)}">
        <span class="cs-ic">${c.protected ? "🔒" : "▦"}</span><span class="cs-name">${esc(c.name)}</span>
        ${c.default ? '<span class="cs-badge">all\'avvio</span>' : ""}${c.open ? '<span class="cs-badge open">aperto</span>' : ""}</div>`).join("")
        || '<p class="small">Nessun catalogo.</p>'}</div>
      ${sel && sel.protected && !sel.open ? `<label>Password di «${esc(sel.name)}»</label><input type="password" id="cs-pw" autofocus>` : ""}
      ${err ? `<p class="cs-err">${esc(err)}</p>` : ""}
      <label class="check"><input type="checkbox" id="cs-def" ${sel && sel.default ? "checked" : ""}> Apri questo catalogo all'avvio</label>
      <div class="btns">
        <button type="button" id="cs-new">Nuovo catalogo…</button>
        <span style="flex:1"></span>
        ${startup ? "" : '<button type="button" id="cs-cancel">Annulla</button>'}
        <button type="button" class="primary" id="cs-open" ${sel ? "" : "disabled"}>${sel && sel.protected && !sel.open ? "Sblocca e apri" : "Apri"}</button>
      </div></div>`;
    el.hidden = false;
    $("#cs-pw")?.focus();
  };
  el.onclick = e => {
    const it = e.target.closest(".cs-item");
    if (it) { sel = cs.catalogs.find(c => c.path === it.dataset.path); draw(); return; }
    if (e.target.id === "cs-cancel") { el.hidden = true; return; }
    if (e.target.id === "cs-new") { el.hidden = true; newCatalogDialog().then(() => { if (!CAT) el.hidden = false; }); return; }
    if (e.target.id === "cs-open") go();
  };
  el.ondblclick = e => { const it = e.target.closest(".cs-item"); if (it && !(sel.protected && !sel.open)) go(); };
  el.onkeydown = e => { if (e.key === "Enter") go(); if (e.key === "Escape" && !startup) el.hidden = true; };
  async function go() {
    if (!sel) return;
    const wantDef = $("#cs-def").checked;
    try {
      if (wantDef !== sel.default) await api("/api/catalogs/default", {path: wantDef ? sel.path : null});
      if (sel.open) { el.hidden = true; return; }
      await api("/api/catalogs/open", {path: sel.path, password: $("#cs-pw")?.value || null});
      location.reload();
    } catch (err) { draw(err.message); }
  }
  draw();
}

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
  const cs = await api("/api/catalogs").catch(() => null);
  if (!cs || !cs.current) return showCatalogScreen(cs, true);
  CAT = cs.current;
  document.title = CAT.name;
  updateCatalogButton();
  S.scope = store.get("scope", "all");
  S.expanded = new Set(store.get("expanded", []));
  $("#zoom").value = store.get("zoom", 180);
  for (const j of await api("/api/jobs").catch(() => [])) if (j.status !== "running") seenDone.add(j.id);
  await safe(loadTree);
  if (!scopeTitle(S.scope) || scopeTitle(S.scope) === "?") S.scope = "all";
  $("#view-title").textContent = scopeTitle(S.scope);
  renderSidebar();
  await reload();
  pollJobs();
  scroller.focus();
})();
