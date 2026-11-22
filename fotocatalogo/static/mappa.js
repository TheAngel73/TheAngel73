// ================================================================ mappa dei luoghi
// Mostra dove sono state scattate le foto della vista corrente (tutti i filtri tranne
// l'area) e permette di scegliere un'area: diventa il filtro «Luogo» della griglia.
//
// Contorni del mondo: static/mondo.json (Natural Earth, pubblico dominio), già proiettati
// con Mercatore in un quadrato di W0×W0 unità; vedi strumenti/crea_mappa.py.
// Disegno su canvas: i punti vicini sullo schermo si raggruppano in cerchi con il conteggio.
"use strict";

const MAP = {
  world: null, land: null, borders: null,       // contorni (caricati una volta)
  landD: null, bordersD: null, loadingD: false,  // contorni dettagliati, caricati quando si ingrandisce
  data: null,                                   // {points: [[lat, lon, n]], total, with, pending}
  k: 1, tx: 0, ty: 0,                            // vista: schermo = unità * k + t
  clusters: [], drag: null, sel: null, hover: null, focus: null, raf: 0,
};
const W0 = 8192;
// Versione dei contorni: va cambiata ogni volta che si rigenerano mondo*.json, così chi li
// ha già in memoria (Safari, Firefox) scarica quelli nuovi.
const MAP_DATA_V = 2;
const mercX = lon => (lon + 180) / 360 * W0;
const mercY = lat => {
  const la = Math.max(-85, Math.min(85, lat)) * Math.PI / 180;
  return (1 - Math.log(Math.tan(Math.PI / 4 + la / 2)) / Math.PI) / 2 * W0;
};
const unX = x => x / W0 * 360 - 180;
const unY = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / W0))) * 180 / Math.PI;

async function openMap(opts = {}) {
  const box = $("#mapscreen");
  box.innerHTML = `
    <div class="map-head">
      <h2>${svg('<path d="M8 14.2s4.6-4.3 4.6-7.9A4.6 4.6 0 0 0 3.4 6.3c0 3.6 4.6 7.9 4.6 7.9z"/><circle cx="8" cy="6.3" r="1.6"/>')} Luoghi</h2>
      <span class="map-info" id="map-info">Caricamento…</span>
      <span class="map-acts">
        <button type="button" data-map="area" class="primary" title="Mostra nella griglia le foto dell'area che vedi sulla mappa">Mostra le foto di quest'area</button>
        ${S.geo ? `<button type="button" data-map="clear">Togli il filtro luogo</button>` : ""}
        <button type="button" data-map="fit" title="Inquadra tutti i punti">Adatta</button>
        <button type="button" data-map="world">Mondo</button>
        <button type="button" data-map="close" title="Esc">Chiudi</button>
      </span>
    </div>
    <div class="map-stage"><canvas id="map-canvas"></canvas><div id="map-tip" hidden></div>
      <div class="map-hint">Trascina per spostarti · rotellina o pizzico per ingrandire · clic su un cerchio per le sue foto · <b>⇧ + trascina</b> per scegliere un'area</div></div>`;
  box.hidden = false;
  MAP.focus = opts.focus || null;
  const cv = $("#map-canvas");
  MAP.canvas = cv; MAP.ctx = cv.getContext("2d");
  box.querySelector(".map-head").addEventListener("click", e => {
    const a = e.target.closest("[data-map]")?.dataset.map; if (!a) return;
    if (a === "close") closeMap();
    if (a === "world") { mapFitBounds(-60, -170, 75, 180); }
    if (a === "fit") mapFitData();
    if (a === "area") mapApplyArea(mapVisibleBounds());
    if (a === "clear") { S.geo = null; closeMap(); markFilters(); reload(); }
  });
  mapBindEvents(cv);
  try {
    if (!MAP.world) {
      MAP.world = await (await fetch(`/static/mondo.json?v=${MAP_DATA_V}`)).json();
      MAP.land = new Path2D(MAP.world.land);
      MAP.borders = new Path2D(MAP.world.borders);
    }
    MAP.data = await api(`/api/gps?${params({geo: ""})}`);
  } catch (e) { $("#map-info").textContent = "Mappa non disponibile: " + e.message; return; }
  mapResize();
  if (MAP.focus) mapFitBounds(MAP.focus[0] - .05, MAP.focus[1] - .07, MAP.focus[0] + .05, MAP.focus[1] + .07);
  else if (S.geo) {                              // l'area scelta, con un po' di contesto intorno
    const g = S.geo, dl = Math.max(.05, g.n - g.s), dw = Math.max(.07, g.e - g.w);
    mapFitBounds(g.s - dl, g.w - dw, g.n + dl, g.e + dw);
  }
  else mapFitData();
  const d = MAP.data, n = v => v.toLocaleString("it-IT");
  $("#map-info").innerHTML = `${esc(scopeTitle(S.scope))} · <b>${n(d.with)}</b> foto con posizione su ${n(d.total)}` +
    (d.pending ? ` · <span class="map-pending">lettura delle posizioni in corso: ancora ${n(d.pending)} foto</span>` : "");
}

function closeMap() {
  const box = $("#mapscreen");
  box.hidden = true; box.innerHTML = "";
  cancelAnimationFrame(MAP.raf);
  window.removeEventListener("resize", mapResize);
}

function mapResize() {
  const cv = MAP.canvas; if (!cv || !cv.isConnected) return;
  const r = cv.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  cv.style.width = r.width + "px"; cv.style.height = r.height + "px";
  MAP.w = r.width; MAP.h = r.height; MAP.dpr = dpr;
  mapDraw();
}

// ---------------------------------------------------------------- vista
function mapFitBounds(s, w, n, e) {
  if (e < w) e += 360;
  const x0 = mercX(w), x1 = mercX(e), y0 = mercY(n), y1 = mercY(s);
  const pad = 40;
  const k = Math.min((MAP.w - 2 * pad) / Math.max(x1 - x0, 1e-3), (MAP.h - 2 * pad) / Math.max(y1 - y0, 1e-3));
  MAP.k = Math.max(mapMinK(), Math.min(400, k));
  MAP.tx = MAP.w / 2 - (x0 + x1) / 2 * MAP.k;
  MAP.ty = MAP.h / 2 - (y0 + y1) / 2 * MAP.k;
  mapDraw();
}
function mapFitData() {
  const p = MAP.data?.points || [];
  if (!p.length) return mapFitBounds(-55, -170, 72, 180);
  let s = 90, n = -90, w = 180, e = -180;
  for (const [la, lo] of p) { s = Math.min(s, la); n = Math.max(n, la); w = Math.min(w, lo); e = Math.max(e, lo); }
  const m = Math.max(.02, (n - s) * .08);
  mapFitBounds(s - m, w - m, n + m, e + m);
}
const mapMinK = () => Math.min(MAP.w, MAP.h * 1.6) / W0 * .9;
function mapVisibleBounds() {
  const x0 = -MAP.tx / MAP.k, x1 = (MAP.w - MAP.tx) / MAP.k, y0 = -MAP.ty / MAP.k, y1 = (MAP.h - MAP.ty) / MAP.k;
  return {s: unY(Math.min(W0, y1)), n: unY(Math.max(0, y0)), w: Math.max(-180, unX(x0)), e: Math.min(180, unX(x1))};
}
function mapApplyArea(b) {
  const r = x => Math.round(x * 1e5) / 1e5;
  S.geo = {s: r(b.s), w: r(b.w), n: r(b.n), e: r(b.e)};
  closeMap(); markFilters(); reload();
}

// ---------------------------------------------------------------- disegno
function mapDraw() {
  cancelAnimationFrame(MAP.raf);
  MAP.raf = requestAnimationFrame(mapPaint);
}
function mapPaint() {
  const ctx = MAP.ctx; if (!ctx || !MAP.land) return;
  const {k, tx, ty, dpr} = MAP;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#15181c"; ctx.fillRect(0, 0, MAP.w, MAP.h);
  // ingrandendo si passa ai contorni dettagliati (Natural Earth 1:10M)
  const detail = k >= 1.5;
  if (detail && !MAP.landD && !MAP.loadingD) {
    MAP.loadingD = true;
    fetch(`/static/mondo-dettaglio.json?v=${MAP_DATA_V}`).then(r => r.json()).then(w => {
      MAP.landD = new Path2D(w.land); MAP.bordersD = new Path2D(w.borders); MAP.wD = w.w; mapDraw();
    }).catch(() => {});
  }
  const land = detail && MAP.landD ? MAP.landD : MAP.land, borders = detail && MAP.bordersD ? MAP.bordersD : MAP.borders;
  const f = land === MAP.landD ? W0 / (MAP.wD || W0) : 1;    // i contorni dettagliati hanno una griglia più fine
  const kk = k * f;
  ctx.setTransform(kk * dpr, 0, 0, kk * dpr, tx * dpr, ty * dpr);
  ctx.fillStyle = "#2d3138"; ctx.fill(land);
  ctx.strokeStyle = "#474c55"; ctx.lineWidth = .8 / kk; ctx.lineJoin = "round"; ctx.stroke(borders);
  ctx.strokeStyle = "#3a3f47"; ctx.lineWidth = .6 / kk; ctx.stroke(land);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // area del filtro attivo
  if (S.geo) {
    const a = mapRect(S.geo);
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(74,144,226,.9)"; ctx.lineWidth = 1.5;
    ctx.fillStyle = "rgba(74,144,226,.08)"; ctx.fillRect(a.x, a.y, a.w, a.h); ctx.strokeRect(a.x, a.y, a.w, a.h);
    ctx.setLineDash([]);
  }
  // punti raggruppati per vicinanza sullo schermo
  const cell = 26, groups = new Map();
  for (const [la, lo, n, place] of MAP.data?.points || []) {
    const x = mercX(lo) * k + tx, y = mercY(la) * k + ty;
    if (x < -30 || y < -30 || x > MAP.w + 30 || y > MAP.h + 30) continue;
    const key = Math.floor(x / cell) + ":" + Math.floor(y / cell);
    let g = groups.get(key);
    if (!g) groups.set(key, g = {n: 0, sx: 0, sy: 0, s: 90, nn: -90, w: 180, e: -180, top: 0, place: ""});
    g.n += n; g.sx += x * n; g.sy += y * n;
    if (n > g.top && place) { g.top = n; g.place = place; }   // il luogo con più foto del gruppo
    g.s = Math.min(g.s, la); g.nn = Math.max(g.nn, la); g.w = Math.min(g.w, lo); g.e = Math.max(g.e, lo);
  }
  MAP.clusters = [];
  for (const g of groups.values()) {
    const x = g.sx / g.n, y = g.sy / g.n, r = Math.min(22, 4.5 + 3.2 * Math.log10(g.n));
    MAP.clusters.push({x, y, r, ...g});
  }
  MAP.clusters.sort((a, b) => a.n - b.n);
  ctx.font = "600 10px Inter, -apple-system, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const c of MAP.clusters) {
    const hot = MAP.hover === c;
    ctx.beginPath(); ctx.arc(c.x, c.y, c.r + (hot ? 2 : 0), 0, Math.PI * 2);
    ctx.fillStyle = hot ? "#ff8a5c" : "rgba(255,107,74,.88)"; ctx.fill();
    ctx.lineWidth = 1.2; ctx.strokeStyle = "rgba(255,255,255,.85)"; ctx.stroke();
    if (c.n > 1 && c.r >= 8) { ctx.fillStyle = "#fff"; ctx.fillText(c.n > 999 ? Math.round(c.n / 100) / 10 + "k" : c.n, c.x, c.y + .5); }
  }
  // punto da mettere in evidenza (dal pannello informazioni)
  if (MAP.focus) {
    const x = mercX(MAP.focus[1]) * k + tx, y = mercY(MAP.focus[0]) * k + ty;
    ctx.beginPath(); ctx.arc(x, y, 11, 0, Math.PI * 2); ctx.lineWidth = 2.5; ctx.strokeStyle = "#4a90e2"; ctx.stroke();
  }
  // rettangolo in corso di selezione
  if (MAP.sel) {
    const {x0, y0, x1, y1} = MAP.sel;
    ctx.fillStyle = "rgba(74,144,226,.15)"; ctx.strokeStyle = "#4a90e2"; ctx.lineWidth = 1.5;
    ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    ctx.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
  }
}
function mapRect(b) {
  const x0 = mercX(b.w) * MAP.k + MAP.tx, x1 = mercX(b.e < b.w ? b.e + 360 : b.e) * MAP.k + MAP.tx;
  const y0 = mercY(b.n) * MAP.k + MAP.ty, y1 = mercY(b.s) * MAP.k + MAP.ty;
  return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
}

// ---------------------------------------------------------------- mouse e trackpad
function mapHit(x, y) {
  for (let i = MAP.clusters.length - 1; i >= 0; i--) {
    const c = MAP.clusters[i];
    if ((c.x - x) ** 2 + (c.y - y) ** 2 <= (c.r + 4) ** 2) return c;
  }
  return null;
}
function mapZoomAt(x, y, f) {
  const k = Math.max(mapMinK(), Math.min(400, MAP.k * f));
  MAP.tx = x - (x - MAP.tx) * (k / MAP.k); MAP.ty = y - (y - MAP.ty) * (k / MAP.k); MAP.k = k;
  mapDraw();
}
function mapBindEvents(cv) {
  window.addEventListener("resize", mapResize);
  const pos = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  cv.addEventListener("mousedown", e => {
    const [x, y] = pos(e);
    if (e.shiftKey) MAP.sel = {x0: x, y0: y, x1: x, y1: y};
    else MAP.drag = {x, y, tx: MAP.tx, ty: MAP.ty, moved: false};
    e.preventDefault();
  });
  window.addEventListener("mousemove", mapMove);
  window.addEventListener("mouseup", mapUp);
  function mapMove(e) {
    if (!cv.isConnected) { window.removeEventListener("mousemove", mapMove); window.removeEventListener("mouseup", mapUp); return; }
    const [x, y] = pos(e);
    if (MAP.sel) { MAP.sel.x1 = x; MAP.sel.y1 = y; return mapDraw(); }
    if (MAP.drag) {
      const d = MAP.drag;
      if (Math.abs(x - d.x) + Math.abs(y - d.y) > 3) d.moved = true;
      MAP.tx = d.tx + x - d.x; MAP.ty = d.ty + y - d.y; cv.style.cursor = "grabbing"; return mapDraw();
    }
    const c = mapHit(x, y), tip = $("#map-tip");
    if (c !== MAP.hover) { MAP.hover = c; mapDraw(); }
    cv.style.cursor = c ? "pointer" : "grab";
    if (c) {
      tip.hidden = false;
      tip.innerHTML = (c.place ? `<b>${esc(c.place.replace(/^vicino a /, "dintorni di "))}</b><br>` : "") +
        `${c.n.toLocaleString("it-IT")} foto · clic per vederle`;
      tip.style.left = x + 14 + "px"; tip.style.top = y + 10 + "px";
    } else tip.hidden = true;
  }
  function mapUp(e) {
    if (!cv.isConnected) return;
    const [x, y] = pos(e);
    if (MAP.sel) {
      const {x0, y0} = MAP.sel; MAP.sel = null;
      if (Math.abs(x - x0) > 6 && Math.abs(y - y0) > 6) {
        const ux = v => unX((v - MAP.tx) / MAP.k), uy = v => unY((v - MAP.ty) / MAP.k);
        return mapApplyArea({w: ux(Math.min(x0, x)), e: ux(Math.max(x0, x)), n: uy(Math.min(y0, y)), s: uy(Math.max(y0, y))});
      }
      return mapDraw();
    }
    if (MAP.drag) {
      const moved = MAP.drag.moved; MAP.drag = null; cv.style.cursor = "grab";
      if (!moved) {
        const c = mapHit(x, y);
        if (c) {
          const m = 0.0006;                     // ~60 m attorno ai punti del gruppo
          mapApplyArea({s: c.s - m, n: c.nn + m, w: c.w - m, e: c.e + m});
        }
      }
    }
  }
  cv.addEventListener("wheel", e => {
    e.preventDefault();
    const [x, y] = pos(e);
    // pizzico sul trackpad = ctrl + rotellina; rotellina e trackpad = zoom
    mapZoomAt(x, y, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0022)));
  }, {passive: false});
  cv.addEventListener("dblclick", e => { const [x, y] = pos(e); mapZoomAt(x, y, 2.5); });
}
