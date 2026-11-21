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
// "v" cambia quando cambia il file: così il browser non mostra miniature vecchie
const VERS = new Map();
const qv = id => `?c=${CAT ? CAT.uid : ""}&v=${VERS.get(id) || ""}`;
// Le immagini arrivano da un indirizzo diverso da quello dei comandi (127.0.0.1 invece di
// localhost): il browser apre al massimo 6 collegamenti per indirizzo, e così i comandi
// (clic, stelle, informazioni) non restano in coda dietro alle miniature in preparazione.
const IMG_BASE = location.hostname === "localhost" ? `${location.protocol}//127.0.0.1:${location.port}`
  : location.hostname === "127.0.0.1" ? `${location.protocol}//localhost:${location.port}` : "";
const thumbURL = id => `${IMG_BASE}/thumb/${id}${qv(id)}`;
const previewURL = id => `${IMG_BASE}/preview/${id}${qv(id)}`;
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
    for (const it of data.items) VERS.set(it.id, it.v);
    S.pages.set(p, data.items);
    $("#view-count").textContent = `· ${data.total.toLocaleString("it-IT")} ${S.scope === "video" ? "video" : "foto"}`;
    layout();
  } catch (e) { toast(e.message, true); }
  finally { S.loading.delete(p); }
}

async function reload(keepScroll = false) {
  S.version++;
  S.pages.clear(); S.loading.clear();
  for (const el of cells.values()) dropCell(el);
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
  for (const [i, el] of cells) if (i < i0 || i >= i1) { dropCell(el); cells.delete(i); }
  for (let i = i0; i < i1; i++) {
    const it = itemAt(i);
    if (!it) loadPage(Math.floor(i / PAGE));
    let el = cells.get(i);
    const key = it ? `${it.id}|${it.r}|${it.l}|${it.f}|${it.m}|${it.v}` : "?";
    if (!el || el._key !== key) {
      if (el) el.remove();
      el = makeCell(it, i); el._key = key;
      cells.set(i, el); space.appendChild(el);
    }
    el.style.cssText = `left:${4 + (i % S.cols) * S.cw + 3}px;top:${4 + Math.floor(i / S.cols) * S.ch + 3}px;width:${S.cw - 6}px;height:${S.ch - 6}px`;
    el.classList.toggle("sel", !!it && S.sel.has(it.id));
    el.classList.toggle("cursor", i === S.cursor);
  }
  const ahead = Math.floor(Math.min(S.total - 1, i1 + S.cols * 12) / PAGE);
  if (S.total && !S.pages.has(ahead)) loadPage(ahead);
}

// tipo di file -> triangolino nell'angolo
const RAW_PROP = new Set(["nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "raf", "orf", "rw2", "pef", "srw",
  "x3f", "3fr", "iiq", "erf", "mos", "mrw", "raw", "rwl", "kdc", "dcr", "mef"]);
const VIDEO_EXT = new Set(["mov", "mp4", "m4v", "avi", "mkv", "mts", "m2ts", "3gp", "mpg", "mpeg", "wmv", "webm"]);
const VIDEO_PLAYABLE = new Set(["mov", "mp4", "m4v", "3gp"]);   // riproducibili dal Mac senza altre app
const isVideo = x => VIDEO_EXT.has((x || "").toLowerCase());
function fileType(x) {
  x = (x || "").toLowerCase();
  if (VIDEO_EXT.has(x)) return ["video", "Video " + x.toUpperCase()];
  if (x === "jpg" || x === "jpeg" || x === "jpe") return ["jpg", "JPEG"];
  if (x === "heic" || x === "heif") return ["jpg", "HEIC"];          // foto "standard" come i JPEG
  if (x === "psd" || x === "psb") return ["psd", "Photoshop (" + x.toUpperCase() + ")"];
  if (RAW_PROP.has(x)) return ["raw", "RAW proprietario (" + x.toUpperCase() + ") da convertire"];
  return null;
}

// Una cella che sparisce (scorrimento, cambio di cartella) interrompe il caricamento della
// sua miniatura: altrimenti WebKit la scarica comunque e le miniature della nuova vista
// restano in coda dietro a quelle che non servono più.
function dropCell(el) {
  const img = el.querySelector("img");
  if (img && !img.complete) { img.onerror = null; img.removeAttribute("src"); }
  el.remove();
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
    ${ft && ft[0] !== "video" ? `<span class="ftype ft-${ft[0]}" title="${ft[1]}"></span>` : ""}
    <div class="tl${ft && ft[0] !== "video" ? " after-tri" : ""}">${ft && ft[0] === "video" ? `<span class="vbadge" title="${ft[1]}">▶ ${esc(it.x.toUpperCase())}</span>` : ""}${it.m ? '<span class="miss" title="File non trovato">⚠ mancante</span>' : ""}</div>
    ${it.f === 1 ? '<span class="flag" title="Scelta">⚑</span>' : ""}
    <div class="cap"${it.l ? ` title="Etichetta ${LABELS[it.l] || ""}"` : ""}><span class="fn" title="${esc(it.n)}">${esc(it.n)}</span><span class="stars">${stars}</span></div>`;
  const img = el.querySelector("img");
  img.draggable = false;
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

let renameTimer = null;
space.addEventListener("click", e => {
  clearTimeout(renameTimer);
  if (S.nativeDrag) return;          // fine di un trascinamento, non un clic
  const c = e.target.closest(".cell"); if (!c) { if (!e.metaKey && !e.shiftKey) { S.sel.clear(); selChanged(); } return; }
  const it = itemAt(+c.dataset.i);
  const wasOnly = it && S.sel.size === 1 && S.sel.has(it.id);
  clickCell(+c.dataset.i, e);
  // come nel Finder: clic sul nome di una foto già selezionata -> rinomina
  if (wasOnly && e.target.closest(".fn") && !e.shiftKey && !e.metaKey && !e.ctrlKey && e.detail === 1)
    renameTimer = setTimeout(() => startInlineRename(it.id), 600);
});
space.addEventListener("dblclick", e => { clearTimeout(renameTimer); const c = e.target.closest(".cell"); if (c) openLoupe(+c.dataset.i); });
space.addEventListener("dragstart", e => {
  const c = e.target.closest(".cell"); const it = c && itemAt(+c.dataset.i);
  if (!it) return e.preventDefault();
  if (!S.sel.has(it.id)) { S.sel.clear(); S.sel.add(it.id); S.anchor = S.cursor = +c.dataset.i; selChanged(); }
  if (NATIVE) {
    // nell'app per Mac il trascinamento lo fa macOS con i file veri: sul Finder o sulla
    // scrivania si crea una copia; dentro la finestra arriva come "Files" (vedi isInternal)
    e.preventDefault();
    S.nativeDrag = [...S.sel];
    window.webkit.messageHandlers.fc.postMessage({cmd: "drag", ids: S.nativeDrag});
    return;
  }
  e.dataTransfer.setData("application/x-fc-photos", JSON.stringify([...S.sel]));
  e.dataTransfer.effectAllowed = "copyMove";   // copia in una raccolta, spostamento su una cartella
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
  $("#btn-convert-all").hidden = scope !== "toconvert";
  $("#btn-remove-missing").hidden = scope !== "missing";
  $("#btn-rename-all").hidden = scope !== "torename";
  $("#btn-delete-rejected").hidden = scope !== "rejected";
  renderSidebar(); reload();
}
function scopeTitle(scope) {
  const [k, v] = scope.split(":"); const t = S.tree; if (!t) return "";
  const fixed = {all: "Tutte le foto", nocoll: "Foto senza raccolta", toconvert: "Da convertire (non DNG, JPEG, HEIC, PSD o video)", video: "Video", torename: "Da rinominare (nomi della fotocamera)", rejected: "Scartate (bandierina ✕), in tutto il catalogo", missing: "File mancanti", recent: "Importazione più recente"};
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
  if (t.stats.video) cat.push(["video", "▶", "Video", t.stats.video]);
  cat.push(["torename", "✎", "Da rinominare", t.stats.torename]);
  if (t.stats.rejected) cat.push(["rejected", "⊘", "Scartate", t.stats.rejected]);
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
  if (isInternal(e.dataTransfer) && li.dataset.isset === "0") return li;
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
  const photos = dragIds(e.dataTransfer);
  if (photos.length) return addToCollection(+li.dataset.coll, photos);
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
    <div class="fname" title="Clic per rinominare">${esc(d.filename)}</div>
    <div class="path">${esc(d.path)}</div>
    ${rateBars(d.rating, d.label, d.flag)}
    <div class="row">
      <button data-open="photoshop" title="⌘E">Apri in Photoshop</button>
      <button data-open="finder" title="⌘R">Mostra nel Finder</button>
    </div>
    <div class="row"><button data-rot="-90" title="⌘← oppure ⌘[">⟲ Ruota a sinistra</button><button data-rot="90" title="⌘→ oppure ⌘]">⟳ Ruota a destra</button>
      <button data-act="export" title="⇧⌘E">Esporta JPEG…</button></div>
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
    <div class="row"><button data-open="photoshop">Apri in Photoshop${S.sel.size > 50 ? " (prime 50)" : ""}</button></div>
    <div class="row"><button data-rot="-90">⟲ Ruota a sinistra</button><button data-rot="90">⟳ Ruota a destra</button></div>
    <div class="row"><button data-act="export">Esporta in JPEG…</button></div>`;
}

$("#info").addEventListener("click", async e => {
  const t = e.target.closest("[data-rate],[data-flag],[data-label],[data-open],[data-act],[data-uncoll],[data-unkw],[data-goto],[data-rot]");
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
  if (t.dataset.rot) return rotate(+t.dataset.rot);
  if (t.dataset.act === "export") return exportDialog(ids, ids.length === 1 ? "la foto selezionata" : `${ids.length.toLocaleString("it-IT")} foto selezionate`);
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

// ================================================================ vista ingrandita (con zoom)
// Clic: alterna "adatta allo schermo" / 100%. Rotellina (o pizzico sul trackpad): zoom.
// Trascinando ci si sposta nell'immagine ingrandita.
const fullURL = id => `${IMG_BASE}/full/${id}${qv(id)}`;
const L = {i: -1, id: null, fitW: 0, fitH: 0, bx: 0, by: 0, z: 1, tx: 0, ty: 0, natW: 0, full: "no"};
const stage = $("#loupe-stage"), limg = $("#loupe-img");
let loupeIdx = -1;

function openLoupe(i) {
  const it = itemAt(i); if (!it) return;
  loupeIdx = i; S.cursor = i; S.sel.clear(); S.sel.add(it.id); selChanged();
  Object.assign(L, {i, id: it.id, z: 1, tx: 0, ty: 0, natW: 0, full: "no", video: isVideo(it.x)});
  stopVideo();
  limg.hidden = L.video;
  $("#loupe").classList.toggle("is-video", L.video);
  $("#loupe-hint").textContent = L.video ? "spazio: avvia/ferma · ← → scorri · Esc chiudi"
    : "clic: adatta/100% · doppio clic: griglia · rotellina: zoom · ← →";
  if (L.video) {
    $("#loupe-name").textContent = `${it.n} · ${fmtDate(it.t)} ${it.r ? "· " + "★".repeat(it.r) : ""}`;
    $("#loupe-zoom").textContent = "Video";
    $("#loupe").hidden = false;
    if (VIDEO_PLAYABLE.has(it.x.toLowerCase())) {
      const v = $("#loupe-video");
      v.hidden = false; v.poster = previewURL(it.id); v.src = fullURL(it.id);
      v.onerror = () => { v.hidden = true; $("#loupe-novideo").hidden = false; };
    } else $("#loupe-novideo").hidden = false;
    return;
  }
  limg.onload = () => loupeLayout();
  limg.src = thumbURL(it.id);
  const big = new Image();
  big.onload = () => { if (L.id === it.id && L.full === "no") limg.src = big.src; };
  big.src = previewURL(it.id);
  $("#loupe-name").textContent = `${it.n} · ${fmtDate(it.t)} ${it.r ? "· " + "★".repeat(it.r) : ""}`;
  $("#loupe").hidden = false;
  loupeLayout();
}
function stopVideo() {
  const v = $("#loupe-video");
  v.pause(); v.removeAttribute("src"); v.removeAttribute("poster"); v.load(); v.hidden = true;
  $("#loupe-novideo").hidden = true;
}
function closeLoupe() { stopVideo(); $("#loupe").hidden = true; L.id = null; }
$("#loupe-novideo").addEventListener("click", e => {
  if (e.target.closest("button")) safe(() => api("/api/open", {ids: [L.id], how: "default"}));
});

function loupeLayout() {
  if (!limg.naturalWidth) return;
  const W = stage.clientWidth, H = stage.clientHeight, ar = limg.naturalWidth / limg.naturalHeight;
  if (W / H > ar) { L.fitH = H; L.fitW = H * ar; } else { L.fitW = W; L.fitH = W / ar; }
  L.bx = (W - L.fitW) / 2; L.by = (H - L.fitH) / 2;
  loupeApply();
}
function loupeClamp() {
  const W = stage.clientWidth, H = stage.clientHeight, w = L.fitW * L.z, h = L.fitH * L.z;
  L.tx = w <= W ? (L.fitW - w) / 2 : Math.min(-L.bx, Math.max(W - L.bx - w, L.tx));
  L.ty = h <= H ? (L.fitH - h) / 2 : Math.min(-L.by, Math.max(H - L.by - h, L.ty));
}
function loupeApply(animate = false) {
  loupeClamp();
  limg.classList.toggle("anim", animate);
  // dimensioni reali (non scale()) così il browser ridisegna l'immagine nitida a ogni zoom
  limg.style.width = L.fitW * L.z + "px"; limg.style.height = L.fitH * L.z + "px";
  limg.style.transform = `translate(${L.bx + L.tx}px, ${L.by + L.ty}px)`;
  stage.classList.toggle("zoomed", L.z > 1.01);
  const z100 = L.natW ? L.natW / L.fitW : 0;
  $("#loupe-zoom").textContent = L.full === "loading" ? "Caricamento a piena risoluzione…"
    : Math.abs(L.z - 1) < 0.01 ? "Adatta" : z100 ? Math.round(L.z / z100 * 100) + "%" : Math.round(L.z * 100) + "%";
}
function loupeZoomAt(px, py, z, animate = false) {
  const z100 = L.natW ? L.natW / L.fitW : 1;
  // si può scendere sotto "adatta" solo per vedere al 100% una foto più piccola dello schermo
  z = Math.max(Math.min(1, z100), Math.min(Math.max(4, z100 * 3), z));
  const u = (px - L.bx - L.tx) / L.z, v = (py - L.by - L.ty) / L.z;
  L.tx = px - L.bx - u * z; L.ty = py - L.by - v * z; L.z = z;
  loupeApply(animate);
}
// carica l'immagine a piena risoluzione (una volta per foto)
function loupeLoadFull() {
  if (L.full !== "no") return Promise.resolve();
  L.full = "loading"; loupeApply();
  const id = L.id;
  return new Promise(res => {
    const im = new Image();
    im.onload = () => {
      if (L.id !== id) return res();
      L.full = "yes"; L.natW = im.naturalWidth;
      limg.onload = null; limg.src = im.src;
      const ar = im.naturalWidth / im.naturalHeight;
      if (Math.abs(ar - L.fitW / L.fitH) > 0.02) { L.z = 1; L.tx = L.ty = 0; loupeLayout(); } else loupeApply();
      res();
    };
    im.onerror = () => { if (L.id === id) { L.full = "error"; loupeApply(); toast("Piena risoluzione non disponibile: il NAS è collegato?", true); } res(); };
    im.src = fullURL(id);
  });
}
async function loupeToggle(px, py) {
  if (Math.abs(L.z - 1) >= 0.01) { L.z = 1; L.tx = L.ty = 0; return loupeApply(true); }
  await loupeLoadFull();
  loupeZoomAt(px, py, L.natW ? L.natW / L.fitW : 2, true);
}
async function loupeStep(d) {
  const i = Math.max(0, Math.min(S.total - 1, loupeIdx + d));
  if (!itemAt(i)) await loadPage(Math.floor(i / PAGE));
  ensureVisible(i); openLoupe(i);
}
$("#loupe-prev").onclick = () => loupeStep(-1);
$("#loupe-next").onclick = () => loupeStep(1);

stage.addEventListener("wheel", e => {
  if (L.video) return;
  e.preventDefault();
  if (!L.fitW) return;
  const r = stage.getBoundingClientRect();
  const z = L.z * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018));
  loupeZoomAt(e.clientX - r.left, e.clientY - r.top, z);
  if (L.z > 1.01) loupeLoadFull();
}, {passive: false});

let drag = null;
stage.addEventListener("pointerdown", e => {
  if (e.button !== 0 || L.video) return;
  drag = {x: e.clientX, y: e.clientY, tx: L.tx, ty: L.ty, moved: false};
  stage.setPointerCapture(e.pointerId);
});
stage.addEventListener("pointermove", e => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  if (drag.moved && L.z > 1.01) { stage.classList.add("dragging"); L.tx = drag.tx + dx; L.ty = drag.ty + dy; loupeApply(); }
});
stage.addEventListener("pointerup", e => {
  const d = drag; drag = null; stage.classList.remove("dragging");
  if (!d || d.moved) return;
  const r = stage.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  const ir = limg.getBoundingClientRect();
  const onImage = e.clientX >= ir.left && e.clientX <= ir.right && e.clientY >= ir.top && e.clientY <= ir.bottom;
  // doppio clic: torna alla griglia (come Lightroom). Il clic singolo aspetta un attimo
  // per capire se arriva il secondo, poi alterna adatta/100%.
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; return closeLoupe(); }
  clickTimer = setTimeout(() => {
    clickTimer = null;
    if ($("#loupe").hidden) return;
    if (!onImage && Math.abs(L.z - 1) < 0.01) return closeLoupe();   // clic sul fondo scuro: chiude
    loupeToggle(px, py);
  }, 240);
});
let clickTimer = null;
window.addEventListener("resize", () => { if (!$("#loupe").hidden) loupeLayout(); });
function loupeKeyZoom(f) {
  const W = stage.clientWidth, H = stage.clientHeight;
  loupeZoomAt(W / 2, H / 2, L.z * f, true);
  if (L.z > 1.01) loupeLoadFull();
}

// ================================================================ tastiera
document.addEventListener("keydown", async e => {
  if (e.target.matches("input, select, textarea") || $("#dlg").open) return;
  if (!$("#dupscreen").hidden || !$("#catscreen").hidden) {      // schermate a tutta pagina: niente tasti della griglia
    if (e.key === "Escape" && !$("#dupscreen").hidden) { e.preventDefault(); $("#dupscreen").hidden = true; }
    return;
  }
  const loupe = !$("#loupe").hidden;
  const mod = e.metaKey || e.ctrlKey;
  if (e.key === "Escape") {
    if (loupe) { e.preventDefault(); closeLoupe(); }
    else if (S.sel.size) { e.preventDefault(); S.sel.clear(); selChanged(); }
    return;            // niente da fare: il Mac emette il normale suono di avviso
  }
  if (loupe && L.video && e.key === " ") {      // nei video la barra spaziatrice avvia/ferma
    e.preventDefault(); const v = $("#loupe-video"); if (!v.hidden) v.paused ? v.play() : v.pause(); return;
  }
  if (loupe && !mod && !L.video) {
    const kz = e.key.toLowerCase();
    if (kz === "z") { e.preventDefault(); return loupeToggle(stage.clientWidth / 2, stage.clientHeight / 2); }
    if (e.key === "+" || e.key === "=") { e.preventDefault(); return loupeKeyZoom(1.5); }
    if (e.key === "-") { e.preventDefault(); return loupeKeyZoom(1 / 1.5); }
  }
  if (loupe && !mod && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { e.preventDefault(); return loupeStep(e.key === "ArrowRight" ? 1 : -1); }
  const shortcut = mod && (e.shiftKey && e.key.toLowerCase() === "e" ? "exportSel" :
    {a: "selectAll", e: "photoshop", r: "finder", k: "keywords", d: "selectNone", backspace: "deleteFiles",
     "[": "rotateLeft", "]": "rotateRight", arrowleft: "rotateLeft", arrowright: "rotateRight"}[e.key.toLowerCase()]);
  // nell'app per Mac queste scorciatoie arrivano dalla barra dei menu
  if (shortcut && !NATIVE) { e.preventDefault(); return COMMANDS[shortcut](); }
  if (mod) return;
  // ogni tasto gestito va "consumato" (preventDefault), altrimenti il Mac emette il suono d'avviso
  if (/^[0-5]$/.test(e.key)) { e.preventDefault(); return setMeta({rating: +e.key}); }
  const lab = {"6": "red", "7": "yellow", "8": "green", "9": "blue"}[e.key];
  if (lab) { e.preventDefault(); const cur = S.sel.size === 1 && S.detail ? S.detail.label : null; return setMeta({label: cur === lab ? null : lab}); }
  const k = e.key.toLowerCase();
  if (k === "p") { e.preventDefault(); return setMeta({flag: 1}); }
  if (k === "x") { e.preventDefault(); return setMeta({flag: -1}); }
  if (k === "u") { e.preventDefault(); return setMeta({flag: 0}); }
  if (k === "c" || k === "b") { e.preventDefault(); return pickCollection([...S.sel]); }
  if (e.key === "F2" && !NATIVE) { e.preventDefault(); return COMMANDS.renameSel(); }   // nell'app: menu Foto
  if (e.key === "Enter" || e.key === " ") { if (S.cursor >= 0) { e.preventDefault(); loupe ? closeLoupe() : openLoupe(S.cursor); } return; }
  if (e.key === "Backspace" || e.key === "Delete") {
    e.preventDefault();
    return S.scope === "missing" ? removeMissing(selIds()) : removeFromCurrentCollection();
  }
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
    ["Frecce", "Spostati tra le foto"], ["Invio, Spazio, doppio clic", "Vista ingrandita"],
    ["Nella vista ingrandita", "clic o Z: adatta / 100% · doppio clic: torna alla griglia · rotellina o + −: zoom · trascina per spostarti"], ["0 – 5", "Stelle"],
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
  if (a === "dups") COMMANDS.findDups();
  if (a === "del-rejected") deleteRejected();
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
  convertDng: needSel(() => convertDng(selIds())),
  deleteFiles: needSel(() => deleteFiles(selIds())),
  deleteRejected: () => deleteRejected(),
  renameSel: needSel(() => S.sel.size === 1 ? startInlineRename(selIds()[0]) : renameDialog(selIds())),
  renameNum: needSel(() => renameDialog(selIds())),
  moveSel: needSel(() => moveDialog(selIds())),
  rotateLeft: needSel(() => rotate(-90)), rotateRight: needSel(() => rotate(90)),
  exportSel: needSel(() => exportDialog(selIds(), S.sel.size === 1 ? "la foto selezionata" : `${S.sel.size.toLocaleString("it-IT")} foto selezionate`)),
  findDups: () => openDuplicates(),
  lockCatalog: () => lockCatalog(),
  zoomIn: () => { $("#zoom").value = +$("#zoom").value + 40; $("#zoom").dispatchEvent(new Event("input")); },
  zoomOut: () => { $("#zoom").value = +$("#zoom").value - 40; $("#zoom").dispatchEvent(new Event("input")); },
};

// ponte con l'app per Mac (Provinia.app): i menu nativi chiamano fc.cmd("...")
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
    // mentre si scrive, ⌘← ⌘→ ⌘⌫ agiscono sul testo, non sulle foto
    if (document.activeElement?.matches("input, textarea") && ["rotateLeft", "rotateRight", "deleteFiles"].includes(name)) return;
    COMMANDS[name]?.();
  },
  // ⌘C dal menu dell'app: le foto selezionate da copiare, oppure null se si sta copiando del testo
  copyIds() {
    const a = document.activeElement;
    if ($("#dlg").open || !$("#catscreen").hidden || !$("#dupscreen").hidden) return null;
    if (a && a.matches("input, textarea, select")) return null;
    if (String(window.getSelection() || "").trim()) return null;
    return S.sel.size ? [...S.sel] : null;
  },
  copied(n, missing) {
    if (!n) return toast("Nessun file da copiare (il NAS è collegato?)", true);
    toast(`${n === 1 ? "Foto copiata" : n.toLocaleString("it-IT") + " foto copiate"}: incolla nel Finder, in Mail, Messaggi…` + (missing ? ` (${missing} mancanti)` : ""));
  },
  dragEnded() { setTimeout(() => { S.nativeDrag = null; }, 400); },
  pickResult(id, path) { pickWaiters.get(id)?.(path); pickWaiters.delete(id); },
};
// sicurezza: se la fine del trascinamento nativo non arrivasse, il clic successivo lo chiude
document.addEventListener("mousedown", () => { S.nativeDrag = null; }, true);
// file trascinati dal Finder: non devono far "navigare" la finestra
for (const ev of ["dragover", "drop"]) document.addEventListener(ev, e => {
  if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault();
});
// foto trascinate su un campo di testo: niente testo incollato
document.addEventListener("drop", e => {
  if (e.dataTransfer && isInternal(e.dataTransfer) && e.target.closest("input, textarea")) e.preventDefault();
}, true);

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
document.addEventListener("keydown", e => { if (e.key === "Escape" && !ctx.hidden) { e.preventDefault(); e.stopImmediatePropagation(); hideMenu(); } }, true);
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
    "-", {label: "Esporta tutte le foto della vista in JPEG…", run: () => exportScope(null, "tutte le foto della vista")},
    {label: "Genera anteprime di questa vista", run: COMMANDS.pregen},
    S.scope === "toconvert" && S.total && {label: "Converti tutti (RAW e PNG)…", run: convertAll},
    S.scope === "missing" && S.total && {label: "Rimuovi tutte dal catalogo…", danger: true, run: () => removeMissing(null)},
    S.scope === "torename" && S.total && {label: "Rinomina tutte…", run: renameAll},
    S.scope === "rejected" && S.total && {label: "Elimina dal disco tutte le scartate…", danger: true, run: deleteRejected},
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
    "-",
    one && {label: "Rinomina", key: "F2", run: COMMANDS.renameSel},
    {label: one ? "Rinomina con nome e numero…" : `Rinomina ${n.toLocaleString("it-IT")} file…`, key: one ? "" : "F2", run: COMMANDS.renameNum},
    {label: "Sposta in…", run: COMMANDS.moveSel},
    {label: "Ruota a sinistra", key: "⌘←", run: COMMANDS.rotateLeft},
    {label: "Ruota a destra", key: "⌘→", run: COMMANDS.rotateRight},
    {label: `Esporta ${one ? "la foto" : "le foto"} in JPEG…`, key: "⇧⌘E", run: COMMANDS.exportSel},
    rawInSelection() && {label: "Converti (RAW → DNG, PNG → JPEG)…", run: COMMANDS.convertDng},
    "-",
    {label: `Elimina ${one ? "il file" : n.toLocaleString("it-IT") + " file"} dal disco…`, key: "⌘⌫", danger: true, run: COMMANDS.deleteFiles},
    missingInSelection() && {label: "Rimuovi dal catalogo (file mancanti)…", danger: true, run: () => removeMissing(selIds())},
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
    {label: c.set ? "Esporta il set in JPEG…" : "Esporta la raccolta in JPEG…", run: () => exportScope("collection:" + cid, `«${c.name}»`)},
    {label: c.set ? "Salva il set con le sottocartelle…" : "Salva raccolta…", run: () => saveCollection(cid)},
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
    {label: "Nuova cartella…", run: () => folderCreate(fid)},
    !isRoot && {label: "Rinomina cartella…", run: () => folderRename(fid)},
    !isRoot && {label: "Elimina cartella vuota…", danger: true, run: () => folderDelete(fid)},
    "-",
    {label: `Sincronizza «${isRoot ? root.name : f.name}»`, run: () => safe(async () => { await api(`/api/folders/${fid}/rescan`, {}); pollJobs(); })},
    {label: "Esporta la cartella in JPEG…", run: () => exportScope("folder:" + fid, `la cartella «${isRoot ? root.name : f.name}» (con le sottocartelle)`)},
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
    <p>Il catalogo verrà salvato in <code>Documenti/Provinia</code>.</p>
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
      <h1>${startup ? "Provinia" : "Cataloghi"}</h1>
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
  el.onkeydown = e => { if (e.key === "Enter") go(); if (e.key === "Escape" && !startup) { e.preventDefault(); el.hidden = true; } };
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

// ================================================================ conversione in DNG
const convertible = x => RAW_PROP.has((x || "").toLowerCase()) || (x || "").toLowerCase() === "png";
function rawInSelection() {
  if (S.detail && S.sel.size === 1 && S.sel.has(S.detail.id)) return convertible(S.detail.ext);
  for (const pg of S.pages.values()) for (const it of pg) if (S.sel.has(it.id) && convertible(it.x)) return true;
  return false;
}
async function convertAll() {
  const r = await safe(() => api(`/api/photos?${params({offset: 0, limit: 1000000, ids_only: 1})}`));
  if (r) convertDng(r.ids);
}
async function convertDng(ids) {
  const known = new Map();
  for (const pg of S.pages.values()) for (const it of pg) known.set(it.id, (it.x || "").toLowerCase());
  if (S.detail) known.set(S.detail.id, (S.detail.ext || "").toLowerCase());
  const allKnown = ids.every(id => known.has(id));
  const raws = allKnown ? ids.filter(id => RAW_PROP.has(known.get(id))).length : null;
  const pngs = allKnown ? ids.filter(id => known.get(id) === "png").length : null;
  if (allKnown && !raws && !pngs) return toast("Nella selezione non ci sono RAW o PNG da convertire");
  if (raws !== 0) {
    const st = await safe(() => api("/api/dng"));
    if (!st) return;
    if (!st.available && !pngs) return dialog("Adobe DNG Converter non trovato", `
      <p>Per convertire i RAW in DNG serve <b>Adobe DNG Converter</b>, gratuito, installato nella cartella Applicazioni.</p>
      <p>Lo trovi sul sito Adobe cercando «Adobe DNG Converter».</p>`, null);
  }
  const n = v => v.toLocaleString("it-IT");
  const lines = allKnown
    ? [raws && `<li><b>${n(raws)}</b> RAW → <b>DNG</b> (impostazioni predefinite di Lightroom)</li>`,
       pngs && `<li><b>${n(pngs)}</b> PNG → <b>JPEG</b> (qualità 92%, parti trasparenti su fondo bianco)</li>`].filter(Boolean).join("")
    : `<li>RAW → <b>DNG</b> (impostazioni predefinite di Lightroom)</li><li>PNG → <b>JPEG</b> (qualità 92%, parti trasparenti su fondo bianco)</li>`;
  const f = await dialog("Converti", `
    <p>Verranno convertiti:</p><ul>${lines}</ul>
    <p>Ogni nuovo file viene salvato nella stessa cartella e <b>prende il posto dell'originale nel catalogo</b>,
    mantenendo raccolte, parole chiave, stelle ed etichette.</p>
    <p class="warn">Dopo una conversione riuscita e verificata, l'originale (e il suo eventuale .xmp)
    viene <b>cancellato</b>: sul NAS finisce nel cestino di rete, se attivo.</p>
    <p>Puoi interrompere in qualsiasi momento dalla barra in basso.</p>`, "Converti");
  if (!f) return;
  await safe(async () => { await api("/api/convert", {ids}); toast("Conversione avviata"); pollJobs(); });
}
function dngSummary(r) {
  const list = (title, rows) => rows.length ? `<h4>${title} (${rows.length})</h4><div class="list">${rows.slice(0, 200).map(([n, why]) =>
    `<div><b>${esc(n)}</b> <span class="setname">${esc(why)}</span></div>`).join("")}</div>` : "";
  if (!r.saltate.length && !r.errori.length && !r.avvisi.length && !r.interrotta)
    return toast(`File convertiti: ${r.convertite.toLocaleString("it-IT")}`);
  dialog(r.interrotta ? "Conversione interrotta" : "Conversione completata", `
    <p>Convertiti: <b>${r.convertite.toLocaleString("it-IT")}</b> su ${r.da_convertire.toLocaleString("it-IT")}.</p>
    ${list("Con avvisi", r.avvisi)}${list("Saltati", r.saltate)}${list("Non riusciti (originale intatto)", r.errori)}`, "OK");
}
$("#btn-convert-all").onclick = convertAll;
$("#jobs").addEventListener("click", e => {
  const b = e.target.closest("[data-cancel-job]");
  if (b) safe(async () => { await api(`/api/jobs/${b.dataset.cancelJob}/cancel`, {}); pollJobs(); });
});

// ================================================================ foto mancanti
function missingInSelection() {
  if (S.detail && S.sel.size === 1 && S.sel.has(S.detail.id)) return !!S.detail.missing;
  for (const pg of S.pages.values()) for (const it of pg) if (S.sel.has(it.id) && it.m) return true;
  return false;
}
// ids = null: tutte le foto mancanti del catalogo
async function removeMissing(ids) {
  if (ids && !ids.length) return;
  const how = ids ? "le foto selezionate il cui file non esiste più" : `tutte le ${S.tree.stats.missing.toLocaleString("it-IT")} foto il cui file non esiste più`;
  const f = await dialog("Rimuovi dal catalogo", `
    <p>Verranno tolte dal catalogo ${how}, insieme alle loro raccolte e parole chiave.
    Le foto selezionate che esistono ancora non vengono toccate.</p>
    <p>Nessun file viene cancellato.</p>
    <p class="warn">Se le hai solo spostate o rinominate, o se il NAS non è collegato, non rimuoverle:
    ricollega il NAS e usa ⟳ Sincronizza.</p>`, "Rimuovi");
  if (!f) return;
  await safe(async () => {
    const r = await api("/api/photos/remove-missing", ids ? {ids} : {});
    toast(`Rimosse dal catalogo: ${r.removed.toLocaleString("it-IT")}`);
    S.sel.clear(); await loadTree();
    if (S.scope === "missing" && !S.tree.stats.missing) setScope("all"); else reload();
  });
}
$("#btn-remove-missing").onclick = () => removeMissing(null);

// ================================================================ elimina dal disco
const fmtBytes = b => b >= 1e9 ? (b / 1e9).toFixed(1).replace(".", ",") + " GB" : b >= 1e6 ? Math.round(b / 1e6) + " MB" : Math.round(b / 1e3) + " KB";
// tutte le foto con la bandierina «scartata», in qualunque cartella (i filtri in alto non contano)
async function deleteRejected() {
  const r = await safe(() => api(`/api/photos?${new URLSearchParams({scope: "rejected", offset: 0, limit: 1000000, ids_only: 1})}`));
  if (!r) return;
  if (!r.ids.length) return toast("Non ci sono foto scartate");
  deleteFiles(r.ids, `tutte le foto scartate del catalogo (${r.ids.length.toLocaleString("it-IT")} file, in qualunque cartella)`);
}
$("#btn-delete-rejected").onclick = deleteRejected;

async function deleteFiles(ids, what = null) {
  if (!ids.length) return;
  const n = ids.length.toLocaleString("it-IT");
  const f = await dialog(`Elimina ${n} file dal disco`, `
    <p>Stai per <b>cancellare dal disco ${what ? esc(what) : ids.length === 1 ? "il file selezionato" : `${n} file`}</b> (e gli eventuali .xmp),
    che verranno tolti anche dal catalogo, con le loro raccolte e parole chiave.</p>
    <p class="warn">Sul NAS i file finiscono nel cestino di rete, se è attivo. Non passano dal Cestino del Mac.</p>
    <label class="check"><input type="checkbox" name="ok" required> Ho capito, elimina i file</label>`, `Elimina ${n} file`);
  if (!f) return;
  await safe(async () => { await api("/api/photos/delete", {ids}); closeLoupe(); S.sel.clear(); selChanged(); toast("Eliminazione avviata"); pollJobs(); });
}
function deleteSummary(r) {
  const msg = `Eliminati ${r.eliminati.toLocaleString("it-IT")} file (${fmtBytes(r.byte)})`;
  if (!r.errori.length && !r.interrotta) return toast(msg);
  dialog(r.interrotta ? "Eliminazione interrotta" : "Eliminazione completata", `<p>${msg}.</p>
    ${r.errori.length ? `<h4>Non eliminati (${r.errori.length})</h4><div class="list">${r.errori.slice(0, 200).map(([nm, why]) =>
      `<div><b>${esc(nm)}</b> <span class="setname">${esc(why)}</span></div>`).join("")}</div>` : ""}`, "OK");
}

// ================================================================ duplicati
// Revisione a gruppi: una copia per gruppo è proposta "da tenere". Regola della proposta:
// mai un file vuoto o nascosto › cartella preferita › più informazioni di catalogo ›
// cartella creata più di recente (la destinazione di uno spostamento) .
const DUP = {data: null, pref: "", choice: new Map(), skip: new Set(), emptiesOn: true, orphansOn: false, tab: "dup", shown: 60};
async function openDuplicates() {
  const d = await safe(() => api("/api/duplicates"));
  if (!d) return;
  if (!d.groups) {
    const f = await dialog("Trova duplicati", `
      <p>Cerco le foto con lo stesso nome in cartelle diverse e ne confronto il <b>contenuto</b>:
      sono duplicati solo i file identici byte per byte.</p>
      <p>Trovo anche i <b>file vuoti</b> (0 byte) che hanno una copia integra altrove, tipici di uno spostamento interrotto.</p>
      <p>La ricerca legge i file sul NAS: con molte foto può richiedere qualche minuto. Al termine si apre la revisione: nulla viene eliminato senza la tua conferma.</p>`, "Cerca");
    if (!f) return;
    return safe(async () => { await api("/api/duplicates/find", {}); toast("Ricerca duplicati avviata"); pollJobs(); });
  }
  DUP.data = d; DUP.pref = store.get("dupPref", ""); DUP.choice.clear(); DUP.skip.clear(); DUP.shown = 60;
  DUP.tab = d.groups.length ? "dup" : "empty";
  renderDup();
  $("#dupscreen").hidden = false;
}
function inPref(m) { const p = DUP.pref.replace(/\/+$/, ""); return !!p && (m.path === p || m.path.startsWith(p + "/")); }
function suggestKeep(ms) {
  const ok = m => m.size > 0 && !m.hidden;
  const score = m => m.nc + m.nk + (m.rating ? 1 : 0) + (m.label ? 1 : 0) + (m.flag ? 1 : 0);
  return [...ms].sort((a, b) => (ok(b) - ok(a)) || (inPref(b) - inPref(a)) || (score(b) - score(a))
    || (b.fbirth - a.fbirth) || (b.id - a.id))[0].id;
}
const keepOf = g => DUP.choice.get(g.gid) ?? suggestKeep(g.members);
function dupPlan() {
  const d = DUP.data, plan = [];
  let files = 0, bytes = 0;
  for (const g of d.groups) {
    if (DUP.skip.has(g.gid)) continue;
    const keep = keepOf(g), remove = g.members.filter(m => m.id !== keep);
    plan.push({keep, remove: remove.map(m => m.id)}); files += remove.length; bytes += remove.length * g.size;
  }
  if (DUP.emptiesOn) for (const e of d.empties) { plan.push({keep: e.good.id, remove: [e.empty.id]}); files++; }
  if (DUP.orphansOn && d.orphans.length) { plan.push({keep: null, remove: d.orphans.map(m => m.id)}); files += d.orphans.length; }
  return {plan, files, bytes};
}
function memberHTML(m, keep) {
  const info = [m.nc && `${m.nc} raccolte`, m.nk && `${m.nk} parole chiave`, m.rating && "★".repeat(m.rating)].filter(Boolean).join(" · ");
  return `<div class="dup-m ${keep ? "keep" : "del"}" data-pick="${m.id}" title="${esc(m.path)}">
    <div class="dup-th"><img loading="lazy" src="${thumbURL(m.id)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'ph',textContent:'${m.size ? "" : "vuoto"}'}))"></div>
    <div class="dup-tag">${keep ? "✓ TIENI" : "ELIMINA"}</div>
    <div class="dup-folder">${esc(m.folder)}</div>
    <div class="dup-meta">${fmtSize(m.size)}${m.hidden ? " · <b>nascosto</b>" : ""}${inPref(m) ? " · <b>cartella preferita</b>" : ""}</div>
    <div class="dup-meta">${info || "nessuna informazione di catalogo"}</div></div>`;
}
function renderDup() {
  const d = DUP.data, el = $("#dupscreen");
  const {files, bytes} = dupPlan();
  const tabs = `<div class="dup-tabs">
    <button data-tab="dup" class="${DUP.tab === "dup" ? "on" : ""}">Duplicati identici (${d.groups.length.toLocaleString("it-IT")} gruppi)</button>
    <button data-tab="empty" class="${DUP.tab === "empty" ? "on" : ""}">File vuoti (${(d.empties.length + d.orphans.length).toLocaleString("it-IT")})</button></div>`;
  let body = "";
  if (DUP.tab === "dup") {
    body = d.groups.slice(0, DUP.shown).map(g => {
      const keep = keepOf(g), skip = DUP.skip.has(g.gid);
      return `<div class="dup-g${skip ? " skipped" : ""}" data-g="${g.gid}">
        <div class="dup-head"><b>${esc(g.members[0].name)}</b> · ${g.members.length} copie identiche · ${fmtSize(g.size)}
          <label class="check"><input type="checkbox" data-skip="${g.gid}" ${skip ? "checked" : ""}> Salta</label></div>
        <div class="dup-ms">${g.members.map(m => memberHTML(m, !skip && m.id === keep)).join("")}</div></div>`;
    }).join("") || `<p class="empty">Nessun duplicato identico.</p>`;
    if (d.groups.length > DUP.shown) body += `<button class="dup-more" data-more>Mostra altri ${Math.min(60, d.groups.length - DUP.shown)} gruppi…</button>`;
  } else {
    body = `<label class="check dup-all"><input type="checkbox" data-empties ${DUP.emptiesOn ? "checked" : ""}>
      Elimina i <b>${d.empties.length.toLocaleString("it-IT")}</b> file vuoti che hanno una copia integra (le informazioni passano alla copia integra)</label>
      <div class="list">${d.empties.slice(0, 500).map(e => `<div><b>${esc(e.empty.name)}</b>
        <span class="setname">vuoto in ${esc(e.empty.folder)} → resta ${esc(e.good.folder)}</span></div>`).join("")}</div>`;
    if (d.orphans.length) body += `<label class="check dup-all"><input type="checkbox" data-orphans ${DUP.orphansOn ? "checked" : ""}>
      Elimina anche i <b>${d.orphans.length.toLocaleString("it-IT")}</b> file vuoti <b>senza</b> copia integra (non contengono nulla, ma si perdono le loro informazioni di catalogo)</label>
      <div class="list">${d.orphans.slice(0, 500).map(m => `<div><b>${esc(m.name)}</b> <span class="setname">${esc(m.folder)}</span></div>`).join("")}</div>`;
  }
  el.innerHTML = `<div class="dup-box">
    <div class="dup-top"><h1>Duplicati</h1><span style="flex:1"></span>
      <button data-again>Cerca di nuovo</button><button data-close>Chiudi</button></div>
    <div class="dup-pref"><span>Cartella preferita:</span>
      <input id="dup-pref" value="${esc(DUP.pref)}" placeholder="nessuna: si tiene la copia con più informazioni di catalogo; a parità, quella nella cartella più recente">
      <button data-pickpref>Scegli…</button>${DUP.pref ? "<button data-nopref>✕</button>" : ""}</div>
    <p class="small">Clic su una copia per tenerla. Prima di eliminare, raccolte, parole chiave, stelle ed etichette
      delle copie eliminate vengono riunite su quella che resta.</p>
    ${tabs}<div class="dup-list">${body}</div>
    <div class="dup-foot"><span>Verranno eliminati <b>${files.toLocaleString("it-IT")}</b> file${bytes ? ` (${fmtBytes(bytes)})` : ""}.
      I file finiscono nel cestino di rete del NAS, se attivo.</span><span style="flex:1"></span>
      <button class="primary danger" data-apply ${files ? "" : "disabled"}>Elimina ${files.toLocaleString("it-IT")} file…</button></div></div>`;
}
$("#dupscreen").addEventListener("click", async e => {
  const t = e.target;
  const m = t.closest("[data-pick]");
  if (m) { const g = t.closest("[data-g]").dataset.g; DUP.choice.set(g, +m.dataset.pick); DUP.skip.delete(g); return renderDup(); }
  if (t.dataset.tab) { DUP.tab = t.dataset.tab; return renderDup(); }
  if (t.dataset.skip) { t.checked ? DUP.skip.add(t.dataset.skip) : DUP.skip.delete(t.dataset.skip); return renderDup(); }
  if (t.hasAttribute("data-empties")) { DUP.emptiesOn = t.checked; return renderDup(); }
  if (t.hasAttribute("data-orphans")) { DUP.orphansOn = t.checked; return renderDup(); }
  if (t.hasAttribute("data-more")) { DUP.shown += 60; return renderDup(); }
  if (t.hasAttribute("data-close")) { $("#dupscreen").hidden = true; return; }
  if (t.hasAttribute("data-nopref")) { DUP.pref = ""; store.set("dupPref", ""); DUP.choice.clear(); return renderDup(); }
  if (t.hasAttribute("data-pickpref")) {
    const inp = $("#dup-pref"); await pickInto(inp, "folder");
    DUP.pref = inp.value.trim(); store.set("dupPref", DUP.pref); DUP.choice.clear(); return renderDup();
  }
  if (t.hasAttribute("data-again")) { $("#dupscreen").hidden = true; return safe(async () => { await api("/api/duplicates/find", {}); toast("Ricerca duplicati avviata"); pollJobs(); }); }
  if (t.hasAttribute("data-apply")) {
    const {plan, files, bytes} = dupPlan();
    const f = await dialog("Elimina duplicati", `<p>Verranno eliminati <b>${files.toLocaleString("it-IT")}</b> file${bytes ? ` (${fmtBytes(bytes)})` : ""},
      tenendo una copia per gruppo. Le informazioni di catalogo delle copie eliminate passano a quella che resta.</p>
      <p>Prima di ogni eliminazione la copia da tenere viene ricontrollata. Sul NAS i file finiscono nel cestino di rete, se attivo.</p>
      <label class="check"><input type="checkbox" name="ok" required> Ho capito, elimina le copie</label>`, `Elimina ${files.toLocaleString("it-IT")} file`);
    if (!f) return;
    await safe(async () => { await api("/api/duplicates/apply", {plan}); $("#dupscreen").hidden = true; toast("Eliminazione duplicati avviata"); pollJobs(); });
  }
});
$("#dupscreen").addEventListener("change", e => {
  if (e.target.id === "dup-pref") { DUP.pref = e.target.value.trim(); store.set("dupPref", DUP.pref); DUP.choice.clear(); renderDup(); }
});

// ================================================================ rotazione (solo nel catalogo, i file non cambiano)
async function rotate(dir) {
  const ids = selIds(); if (!ids.length) return;
  await safe(async () => {
    const r = await api("/api/rotate", {ids, dir});
    for (const [id, [v, o]] of Object.entries(r.versions)) VERS.set(+id, v);
    for (const pg of S.pages.values()) for (const it of pg) { const x = r.versions[it.id]; if (x) { it.v = x[0]; it.o = x[1]; } }
    render(); showInfo(true);
    if (!$("#loupe").hidden && ids.includes(L.id)) openLoupe(loupeIdx);
  });
}
$("#loupe-rotl").onclick = e => { e.stopPropagation(); rotate(-90); };
$("#loupe-rotr").onclick = e => { e.stopPropagation(); rotate(90); };

// ================================================================ esportazione in JPEG
async function exportScope(scope, what) {
  // cartella o raccolta: tutto il contenuto, senza i filtri in alto; altrimenti la vista con i filtri
  const q = scope ? new URLSearchParams({scope, deep: "1", sort: "name", offset: 0, limit: 1000000, ids_only: 1}).toString()
    : params({offset: 0, limit: 1000000, ids_only: 1});
  const r = await safe(() => api(`/api/photos?${q}`));
  if (r) exportDialog(r.ids, what);
}
async function exportDialog(ids, what) {
  if (!ids.length) return toast("Non ci sono foto da esportare");
  const o = {dest: "", mode: "long", size: 2048, quality: 90, nogps: false, ...store.get("export", {})};
  const f = await dialog("Esporta in JPEG", `
    <p>Esporta ${esc(what)}${ids.length > 1 ? ` (${ids.length.toLocaleString("it-IT")} file; i video vengono saltati)` : ""}.
    Gli originali non vengono toccati; la rotazione del catalogo viene applicata.</p>
    <label>Cartella di destinazione</label>
    <div class="pickrow"><input type="text" name="dest" required value="${esc(o.dest)}" placeholder="/Users/…/Desktop/Esportate"><button type="button" data-pick>Scegli…</button></div>
    <label>Dimensioni</label>
    <div class="pickrow"><select name="mode">
      <option value="long">Bordo lungo</option><option value="width">Larghezza</option>
      <option value="height">Altezza</option><option value="original">Dimensioni originali</option></select>
      <input type="number" name="size" min="16" max="20000" step="1" value="${o.size}" style="width:110px"> <span class="small" style="margin:0">pixel</span></div>
    <p class="small" id="exp-hint"></p>
    <label>Qualità JPEG: <b id="exp-q">${o.quality}</b></label>
    <input type="range" name="quality" min="40" max="100" value="${o.quality}" style="width:100%">
    <label class="check"><input type="checkbox" name="nogps" ${o.nogps ? "checked" : ""}> Togli la posizione GPS dai file esportati</label>`,
    `Esporta ${ids.length.toLocaleString("it-IT")} file`, form => {
      form.mode.value = o.mode;
      const hint = () => {
        const m = form.mode.value; form.size.disabled = m === "original";
        $("#exp-hint").textContent = {long: "Il lato più lungo avrà al massimo questa misura, sia per le foto orizzontali sia per le verticali.",
          width: "Tutte le foto avranno al massimo questa larghezza.", height: "Tutte le foto avranno al massimo questa altezza.",
          original: "Le foto mantengono le loro dimensioni."}[m] + (m === "original" ? "" : " Le foto più piccole non vengono ingrandite.");
      };
      form.mode.onchange = hint; hint();
      form.quality.oninput = () => $("#exp-q").textContent = form.quality.value;
      form.querySelector("[data-pick]").onclick = () => pickInto(form.dest, "dest");
    });
  if (!f) return;
  const opts = {dest: f.dest.value.trim(), mode: f.mode.value, size: +f.size.value || 2048, quality: +f.quality.value, nogps: f.nogps.checked};
  store.set("export", opts);
  await safe(async () => { await api("/api/export", {ids, options: opts}); toast("Esportazione avviata"); pollJobs(); });
}
async function exportSummary(r) {
  const msg = `Esportati ${r.esportate.toLocaleString("it-IT")} file (${fmtBytes(r.byte)})`;
  const rows = [...r.saltate, ...r.errori];
  const f = await dialog(r.interrotta ? "Esportazione interrotta" : "Esportazione completata", `<p>${msg} in <code>${esc(r.dest)}</code>.</p>
    ${rows.length ? `<h4>Non esportati (${rows.length})</h4><div class="list">${rows.slice(0, 200).map(([n, why]) =>
      `<div><b>${esc(n)}</b> <span class="setname">${esc(why)}</span></div>`).join("")}</div>` : ""}`, NATIVE || navigator.platform.startsWith("Mac") ? "Mostra nel Finder" : "OK");
  if (f && (NATIVE || navigator.platform.startsWith("Mac"))) safe(() => api("/api/export/reveal", {}));
}

// ================================================================ rinomina di un solo file (clic sul nome, F2)
function startInlineRename(id) {
  clearTimeout(renameTimer);
  let i = -1;
  for (const [k] of cells) if (itemAt(k)?.id === id) { i = k; break; }
  const el = i >= 0 && cells.get(i), it = el && itemAt(i);
  if ($("#dlg").open) return;
  if (!el || !$("#loupe").hidden) return renameOneDialog(id);
  if (it.m) return toast("Il file è mancante: non si può rinominare", true);
  if (el.classList.contains("editing")) return;
  ensureVisible(i);
  const dot = it.n.lastIndexOf(".");
  const base = dot > 0 ? it.n.slice(0, dot) : it.n, ext = dot > 0 ? it.n.slice(dot) : "";
  const cap = el.querySelector(".cap");
  el.draggable = false; el.classList.add("editing");
  cap.innerHTML = `<input class="fn-edit" type="text" spellcheck="false" autocomplete="off"><span class="fn-ext">${esc(ext)}</span>`;
  const inp = cap.querySelector("input");
  inp.value = base; inp.focus(); inp.select();
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    const v = inp.value.trim();
    if (save && v && v !== base) await renameOne(id, v);
    el._key = null; el.classList.remove("editing");
    render(); scroller.focus({preventScroll: true});
  };
  inp.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  inp.addEventListener("blur", () => finish(true));
  for (const ev of ["click", "dblclick", "mousedown"]) inp.addEventListener(ev, e => e.stopPropagation());
}
async function renameOne(id, name) {
  try {
    const r = await api("/api/rename/one", {id, name});
    patchItems([id], {n: r.filename, x: r.ext});
    if (S.detail && S.detail.id === id) showInfo(true);
    loadTree();                                     // conteggio «Da rinominare»
    return true;
  } catch (e) { toast(e.message, true); return false; }
}
async function renameOneDialog(id) {
  const d = await safe(() => api(`/api/photo/${id}`)); if (!d) return;
  if (d.missing) return toast("Il file è mancante: non si può rinominare", true);
  const dot = d.filename.lastIndexOf(".");
  const base = dot > 0 ? d.filename.slice(0, dot) : d.filename, ext = dot > 0 ? d.filename.slice(dot) : "";
  const f = await dialog("Rinomina", `<label>Nuovo nome</label>
    <div class="pickrow"><input type="text" name="name" value="${esc(base)}" required spellcheck="false"><span>${esc(ext)}</span></div>
    <p class="small">L'estensione resta la stessa; l'eventuale file .xmp segue la foto.</p>`, "Rinomina", form => setTimeout(() => form.name.select()));
  if (f && f.name.value.trim() && f.name.value.trim() !== base && await renameOne(id, f.name.value.trim())) {
    for (const [k, el] of cells) if (itemAt(k)?.id === id) el._key = null;
    render();
    const it = itemAt(L.i);
    if (!$("#loupe").hidden && it && it.id === id) $("#loupe-name").textContent = `${it.n} · ${fmtDate(it.t)} ${it.r ? "· " + "★".repeat(it.r) : ""}`;
  }
}
$("#info").addEventListener("click", e => { if (e.target.closest(".fname") && S.sel.size === 1) startInlineRename(selIds()[0]); });

// ================================================================ salva raccolta (originali, con le sottocartelle)
async function saveCollection(cid) {
  const c = S.tree.collections.find(c => c.id === cid); if (!c) return;
  const info = await safe(() => api("/api/collsave/preview", {id: cid})); if (!info) return;
  if (!info.files) return toast(c.set ? "Il set non contiene foto" : "La raccolta è vuota");
  const o = {dest: "", xmp: true, ...store.get("collsave", {})};
  const tree = info.folders.map(([p, n]) => {
    const depth = p.split("/").length - 1, name = p.split("/").pop();
    return `<div style="padding-left:${depth * 18}px">📁 ${esc(name)} <span class="setname">${n ? n.toLocaleString("it-IT") + " file" : ""}</span></div>`;
  }).join("") + (info.nfolders > info.folders.length ? `<div class="small">… e altre ${info.nfolders - info.folders.length} cartelle</div>` : "");
  const f = await dialog(c.set ? `Salva il set «${c.name}»` : `Salva la raccolta «${c.name}»`, `
    <p>Copia i file <b>originali</b> (${info.files.toLocaleString("it-IT")} file, ${fmtBytes(info.bytes)}) in una nuova cartella,
    con una sottocartella per ogni raccolta e set. Il catalogo e i file sul NAS non vengono toccati.</p>
    <div class="list">${tree}</div>
    <label>Dove creare la cartella «${esc(info.root)}»</label>
    <div class="pickrow"><input type="text" name="dest" required value="${esc(o.dest)}" placeholder="/Users/…/Desktop"><button type="button" data-pick>Scegli…</button></div>
    <label class="check"><input type="checkbox" name="xmp" ${o.xmp ? "checked" : ""}> Copia anche i file .xmp accanto alle foto</label>
    <p class="small">Se la cartella esiste già ne viene creata una nuova («${esc(info.root)} (2)»): nulla viene sovrascritto.</p>`,
    `Salva ${info.files.toLocaleString("it-IT")} file`, form => { form.querySelector("[data-pick]").onclick = () => pickInto(form.dest, "dest"); });
  if (!f) return;
  const opts = {dest: f.dest.value.trim(), xmp: f.xmp.checked};
  store.set("collsave", opts);
  await safe(async () => { await api("/api/collsave", {id: cid, options: opts}); toast("Salvataggio avviato"); pollJobs(); });
}
async function saveSummary(r) {
  const msg = `Copiati ${r.copiate.toLocaleString("it-IT")} file (${fmtBytes(r.byte)}) in ${r.cartelle} cartell${r.cartelle === 1 ? "a" : "e"}`;
  const rows = [...r.saltate, ...r.errori];
  const mac = NATIVE || navigator.platform.startsWith("Mac");
  const f = await dialog(r.interrotta ? "Salvataggio interrotto" : "Raccolta salvata", `<p>${msg} in <code>${esc(r.dest)}</code>.</p>
    ${rows.length ? `<h4>Non copiati (${rows.length})</h4><div class="list">${rows.slice(0, 200).map(([n, why]) =>
      `<div><b>${esc(n)}</b> <span class="setname">${esc(why)}</span></div>`).join("")}</div>` : ""}`, mac ? "Mostra nel Finder" : "OK");
  if (f && mac) safe(() => api("/api/collsave/reveal", {}));
}

// ================================================================ rinomina
async function renameAll() {
  const r = await safe(() => api(`/api/photos?${params({offset: 0, limit: 1000000, ids_only: 1})}`));
  if (r) renameDialog(r.ids);
}
$("#btn-rename-all").onclick = renameAll;
function suggestedPrefix() {
  const [k, v] = S.scope.split(":");
  if (k === "collection") { const c = S.tree.collections.find(c => c.id == v); if (c) return c.name; }
  if (k === "folder") { const f = S.tree.folders.find(f => f.id == v); if (f && f.name) return f.name; }
  return store.get("rename", {}).prefix || "";
}
async function renameDialog(ids) {
  if (!ids.length) return;
  const o = {start: 1, digits: 4, sep: " ", order: "date", lower_ext: true, keep_pairs: true, ...store.get("rename", {}), prefix: suggestedPrefix()};
  let ok = false;
  const opts = form => ({prefix: form.prefix.value, start: +form.start.value || 0, digits: +form.digits.value,
    sep: form.sep.value === "none" ? "" : form.sep.value, order: form.order.value,
    lower_ext: form.lower_ext.checked, keep_pairs: form.keep_pairs.checked});
  const f = await dialog(ids.length === 1 ? "Rinomina" : `Rinomina ${ids.length.toLocaleString("it-IT")} file`, `
    <div class="pickrow"><div style="flex:1"><label>Nome (prefisso)</label><input type="text" name="prefix" value="${esc(o.prefix)}" autofocus></div>
      <div><label>Primo numero</label><input type="number" name="start" min="0" value="${o.start}" style="width:100px"></div>
      <div><label>Cifre</label><select name="digits">${[2, 3, 4, 5, 6].map(d => `<option ${d === o.digits ? "selected" : ""}>${d}</option>`).join("")}</select></div></div>
    <div class="pickrow"><div style="flex:1"><label>Separatore</label><select name="sep">
      <option value=" ">spazio — «Saggio 0001»</option><option value="_">trattino basso — «Saggio_0001»</option>
      <option value="-">trattino — «Saggio-0001»</option><option value="none">nessuno — «Saggio0001»</option></select></div>
      <div style="flex:1"><label>Ordine dei numeri</label><select name="order">
      <option value="date">per data di scatto</option><option value="name">per nome attuale</option></select></div></div>
    <label class="check"><input type="checkbox" name="lower_ext" ${o.lower_ext ? "checked" : ""}> Estensione in minuscolo (.ARW → .arw)</label>
    <label class="check"><input type="checkbox" name="keep_pairs" ${o.keep_pairs ? "checked" : ""}> File con lo stesso nome (es. RAW + JPEG) ricevono lo stesso numero</label>
    <h4>Anteprima</h4><div class="list" id="ren-prev"><div class="small">…</div></div>
    <p class="small">Anche gli eventuali file .xmp vengono rinominati. Raccolte, parole chiave e stelle restano.</p>`,
    "Rinomina", form => {
      form.sep.value = o.sep === "" ? "none" : o.sep; form.order.value = o.order;
      const ok_btn = form.querySelector("button.primary");
      let t;
      const preview = () => { clearTimeout(t); t = setTimeout(async () => {
        try {
          const r = await api("/api/rename/preview", {ids, options: opts(form)});
          ok = !r.nconflicts && r.total > 0;
          ok_btn.disabled = !ok;
          $("#ren-prev").innerHTML = r.rows.map(([a, b]) => `<div>${esc(a)} <span class="setname">→</span> <b>${esc(b)}</b></div>`).join("") +
            (r.total > r.rows.length ? `<div class="small">… e altri ${(r.total - r.rows.length).toLocaleString("it-IT")}</div>` : "") +
            (r.nconflicts ? `<div class="cs-err">Impossibile: ${r.nconflicts} nomi già occupati, es. «${esc(r.conflicts[0][0])}» (${esc(r.conflicts[0][1])}). Cambia nome o primo numero.</div>` : "") +
            (r.skipped.length ? `<div class="small">Saltati ${r.skipped.length} file mancanti.</div>` : "");
        } catch (e) { ok = false; ok_btn.disabled = true; $("#ren-prev").innerHTML = `<div class="cs-err">${esc(e.message)}</div>`; }
      }, 200); };
      form.addEventListener("input", preview); form.addEventListener("change", preview); preview();
    });
  if (!f || !ok) return;
  const op = opts(f);
  store.set("rename", op);
  await safe(async () => { await api("/api/rename", {ids, options: op}); toast("Rinomina avviata"); pollJobs(); });
}

// ================================================================ sposta
async function moveDialog(ids) {
  const folders = S.tree.folders.map(f => ({id: f.id, path: folderLabel(f)})).sort((a, b) => a.path.localeCompare(b.path));
  let chosen = null;
  const f = await dialog(`Sposta ${ids.length === 1 ? "la foto" : ids.length.toLocaleString("it-IT") + " foto"}`, `
    <input type="text" name="q" placeholder="Cerca cartella…" autocomplete="off" autofocus>
    <div class="list" id="mv-list"></div>
    <p class="small">I file vengono spostati sul disco (con i loro .xmp); raccolte, parole chiave e stelle restano.
    Puoi anche trascinare le foto su una cartella nella barra a sinistra.</p>`, "Sposta", form => {
      const draw = () => {
        const q = form.q.value.trim().toLowerCase();
        const list = folders.filter(x => x.path.toLowerCase().includes(q)).slice(0, 300);
        if (!list.find(x => x.id === chosen)) chosen = list[0]?.id ?? null;
        $("#mv-list").innerHTML = list.map(x => `<div data-id="${x.id}" class="${x.id === chosen ? "on" : ""}">${esc(x.path)}</div>`).join("");
      };
      form.q.addEventListener("input", draw);
      $("#mv-list").addEventListener("click", e => { const d = e.target.closest("[data-id]"); if (d) { chosen = +d.dataset.id; draw(); } });
      $("#mv-list").addEventListener("dblclick", e => { if (e.target.closest("[data-id]")) form.requestSubmit(); });
      draw();
    });
  if (!f || chosen == null) return;
  startMove(ids, chosen);
}
function folderLabel(f) {
  const r = S.tree.roots.find(r => r.id === f.root);
  return [r ? r.name : "", f.rel].filter(Boolean).join("/");
}
function startMove(ids, fid) {
  return safe(async () => { await api("/api/move", {ids, folder: fid}); toast("Spostamento avviato"); pollJobs(); });
}
function fileOpSummary(r) {
  const msg = r.moved ? `Spostate ${r.spostate.toLocaleString("it-IT")} foto in «${r.dest}»` : `Rinominati ${r.rinominate.toLocaleString("it-IT")} file`;
  const rows = [...(r.saltate || []), ...(r.errori || [])];
  if (!rows.length && !r.interrotta) return toast(msg);
  dialog(r.interrotta ? "Operazione interrotta" : "Operazione completata", `<p>${msg}.</p>
    <h4>Non ${r.moved ? "spostate" : "rinominati"} (${rows.length})</h4><div class="list">${rows.slice(0, 200).map(([n, why]) =>
      `<div><b>${esc(n)}</b> <span class="setname">${esc(why)}</span></div>`).join("")}</div>`, "OK");
}

// ================================================================ cartelle
async function folderCreate(fid) {
  const f = await dialog("Nuova cartella", `<p>Dentro «${esc(folderLabel(S.tree.folders.find(x => x.id === fid)))}»</p>
    <label>Nome</label><input type="text" name="name" required autofocus>`, "Crea");
  if (!f) return;
  await safe(async () => { await api(`/api/folders/${fid}/create`, {name: f.name.value}); S.expanded.add("f" + fid); store.set("expanded", [...S.expanded]); await loadTree(); });
}
async function folderRename(fid) {
  const fo = S.tree.folders.find(x => x.id === fid);
  const f = await dialog("Rinomina cartella", `<label>Nome</label><input type="text" name="name" required value="${esc(fo.name)}" autofocus>
    <p class="small">La cartella viene rinominata anche sul disco.</p>`, "Rinomina");
  if (!f) return;
  await safe(async () => { await api(`/api/folders/${fid}/rename`, {name: f.name.value}); await loadTree(); $("#view-title").textContent = scopeTitle(S.scope); });
}
async function folderDelete(fid) {
  const fo = S.tree.folders.find(x => x.id === fid);
  const f = await dialog("Elimina cartella", `<p>Eliminare la cartella vuota «${esc(fo.name)}» dal disco e dal catalogo?</p>
    <p class="small">Si può eliminare solo una cartella senza foto né sottocartelle.</p>`, "Elimina");
  if (!f) return;
  await safe(async () => { await api(`/api/folders/${fid}/delete`, {}); if (S.scope === "folder:" + fid) setScope("all"); await loadTree(); });
}

// ================================================================ trascinamento: foto -> cartella (sposta), file dal Finder -> cartella (importa)
const folderTree = $("#folder-tree");
// Safari/WebKit aggiunge il tipo "Files" anche quando si trascina una miniatura della pagina:
// le foto del catalogo hanno sempre la precedenza (spostamento), i file contano solo da soli.
// Nell'app per Mac le foto si trascinano come file veri (S.nativeDrag, vedi dragstart):
// se il trascinamento l'abbiamo iniziato noi, i "Files" sono le nostre foto.
const isInternal = dt => [...dt.types].includes("application/x-fc-photos") || !!(S.nativeDrag && [...dt.types].includes("Files"));
const dragIds = dt => { const j = dt.getData("application/x-fc-photos"); return j ? JSON.parse(j) : (S.nativeDrag || []); };
const hasFiles = dt => !isInternal(dt) && [...dt.types].includes("Files");
function folderDropTarget(e) {
  const li = e.target.closest("li[data-folder]"); if (!li) return null;
  return (isInternal(e.dataTransfer) || hasFiles(e.dataTransfer)) ? li : null;
}
folderTree.addEventListener("dragover", e => {
  folderTree.querySelectorAll(".drop").forEach(n => n.classList.remove("drop"));
  const li = folderDropTarget(e); if (!li) return;
  e.preventDefault(); e.dataTransfer.dropEffect = hasFiles(e.dataTransfer) ? "copy" : "move";
  li.querySelector(".node").classList.add("drop");
});
folderTree.addEventListener("dragleave", () => folderTree.querySelectorAll(".drop").forEach(n => n.classList.remove("drop")));
folderTree.addEventListener("drop", async e => {
  folderTree.querySelectorAll(".drop").forEach(n => n.classList.remove("drop"));
  const li = folderDropTarget(e); if (!li) return;
  e.preventDefault();
  const fid = +li.dataset.folder;
  if (hasFiles(e.dataTransfer)) return importDrop(e.dataTransfer, fid);
  const ids = dragIds(e.dataTransfer);
  if (!ids.length) return toast(`Trascinamento non riconosciuto (contenuto: ${[...e.dataTransfer.types].join(", ") || "vuoto"})`, true);
  const fo = S.tree.folders.find(x => x.id === fid);
  const d = await dialog("Sposta", `<p>Spostare ${ids.length === 1 ? "la foto" : ids.length.toLocaleString("it-IT") + " foto"}
    in «${esc(folderLabel(fo))}»?</p><p class="small">I file vengono spostati sul disco; raccolte, parole chiave e stelle restano.</p>`, "Sposta");
  if (d) startMove(ids, fid);
});
// file dal Finder sulla griglia: si importano nella cartella che stai guardando
scroller.addEventListener("dragover", e => { if (hasFiles(e.dataTransfer) && S.scope.startsWith("folder:")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; scroller.classList.add("dropping"); } });
scroller.addEventListener("dragleave", () => scroller.classList.remove("dropping"));
scroller.addEventListener("drop", e => {
  scroller.classList.remove("dropping");
  if (!hasFiles(e.dataTransfer)) return;
  e.preventDefault();
  if (!S.scope.startsWith("folder:")) return toast("Trascina i file su una cartella nella barra a sinistra", true);
  importDrop(e.dataTransfer, +S.scope.split(":")[1]);
});

// legge file e cartelle trascinati (con le sottocartelle) mantenendo la struttura
async function collectDropped(dt) {
  const entries = [...dt.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  const out = [];
  const fileOf = entry => new Promise((res, rej) => entry.file(res, rej));
  const readAll = reader => new Promise((res, rej) => {
    const all = []; const next = () => reader.readEntries(b => { if (!b.length) return res(all); all.push(...b); next(); }, rej); next();
  });
  async function walk(entry, prefix) {
    if (entry.name.startsWith(".")) return;
    if (entry.isFile) out.push({file: await fileOf(entry), path: prefix + entry.name});
    else if (entry.isDirectory) for (const c of await readAll(entry.createReader())) await walk(c, prefix + entry.name + "/");
  }
  if (entries.length) { for (const en of entries) await walk(en, ""); }
  else for (const file of dt.files) out.push({file, path: file.name});
  return out;
}
async function importDrop(dt, fid) {
  const types = [...dt.types].join(", ");
  const items = await collectDropped(dt);
  if (!items.length) return toast(`Nessun file da importare in questo trascinamento (contenuto: ${types || "vuoto"})`, true);
  const fo = S.tree.folders.find(x => x.id === fid);
  const total = items.length;
  let done = 0, ok = 0;
  const skipped = [];
  const show = () => { const t = $("#toast"); t.textContent = `Importazione in «${fo.name || folderLabel(fo)}»: ${done} / ${total}`; t.className = ""; t.hidden = false; clearTimeout(toast._t); };
  show();
  const queue = [...items];
  async function worker() {
    while (queue.length) {
      const it = queue.shift();
      try {
        const q = new URLSearchParams({folder: fid, path: it.path, mtime: it.file.lastModified || ""});
        const r = await fetch(`/api/upload?${q}`, {method: "PUT", body: it.file});
        const d = await r.json().catch(() => ({}));
        if (d.error) skipped.push([it.path, d.error]); else if (d.skipped) skipped.push([it.path, d.skipped]); else ok++;
      } catch (e) { skipped.push([it.path, e.message]); }
      done++; show();
    }
  }
  await Promise.all([worker(), worker()]);
  $("#toast").hidden = true;
  await loadTree(); reload(true);
  const msg = `Importati ${ok.toLocaleString("it-IT")} file in «${folderLabel(fo)}». I file originali non sono stati toccati.`;
  if (!skipped.length) return toast(msg);
  dialog("Importazione completata", `<p>${esc(msg)}</p><h4>Non importati (${skipped.length})</h4><div class="list">${skipped.slice(0, 300).map(([n, why]) =>
    `<div><b>${esc(n)}</b> <span class="setname">${esc(why)}</span></div>`).join("")}</div>`, "OK");
}

// ================================================================ filtri
let searchT;
// filtri attivi evidenziati in colore, con ✕ per toglierli tutti
const FILTERS = ["#search", "#f-rating", "#f-label", "#f-flag", "#f-year", "#f-ext"];
function markFilters() {
  let any = false;
  for (const id of FILTERS) { const on = $(id).value.trim() !== ""; $(id).classList.toggle("on", on); any ||= on; }
  $("#clear-filters").hidden = !any;
}
$("#clear-filters").onclick = () => { for (const id of FILTERS) $(id).value = ""; markFilters(); reload(); };
$("#search").addEventListener("input", () => { markFilters(); clearTimeout(searchT); searchT = setTimeout(() => reload(), 300); });
for (const id of ["#f-rating", "#f-label", "#f-flag", "#f-year", "#f-ext", "#sort", "#deep"])
  $(id).addEventListener("change", () => { if (id === "#sort") store.set("sort", $("#sort").value); markFilters(); reload(); });
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
      return `<span class="job">⏳ ${esc(j.label)}: ${esc(j.message)} ${j.total ? `<progress value="${j.done}" max="${j.total}"></progress>` : ""}` +
        `${j.cancellable && !j.cancelled ? ` <button class="mini" data-cancel-job="${j.id}">Interrompi</button>` : ""}</span>`;
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
  const r = j.result;
  if (r.dng) { await loadTree(); reload(true); return dngSummary(r); }
  if (r.export) return exportSummary(r);
  if (r.saved) return saveSummary(r);
  if (r.moved || r.renamed) { await loadTree(); reload(true); showInfo(true); return fileOpSummary(r); }
  if (r.duplicates) {
    if (r.interrotta) return;
    if (!r.gruppi && !r.vuoti && !r.orfani) return toast("Nessun duplicato trovato 🎉");
    return openDuplicates();
  }
  if (r.dupapply || r.deleted) { await loadTree(); reload(true); return deleteSummary(r); }
  if (r.quick) {
    if (!r.new && !r.missing && !r.ritrovate) return;
    await loadTree(); reload(true);
    const parts = [r.new && `${r.new.toLocaleString("it-IT")} foto nuove`, r.missing && `${r.missing.toLocaleString("it-IT")} non più trovate`,
      r.ritrovate && `${r.ritrovate.toLocaleString("it-IT")} ritrovate`].filter(Boolean);
    return toast("Sincronizzazione: " + parts.join(", "));
  }
  await loadTree(); reload(true);
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
  $("#btn-convert-all").hidden = S.scope !== "toconvert";
  $("#btn-remove-missing").hidden = S.scope !== "missing";
  $("#btn-rename-all").hidden = S.scope !== "torename";
  $("#btn-delete-rejected").hidden = S.scope !== "rejected";
  renderSidebar();
  await reload();
  pollJobs();
  scroller.focus();
})();
