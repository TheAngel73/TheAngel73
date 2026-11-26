#!/usr/bin/env python3
"""Provinia (ex FotoCatalogo): catalogo foto locale con cartelle, raccolte e parole chiave.

Avvio:  python3 server.py   (poi apri http://localhost:8765)
Solo libreria standard di Python: non serve installare nulla.
"""
import collections
import json
import os
import zlib
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from catalog import EXIF_EXT, PHOTO_EXT, QT_EXT, nfc, now, resolve_path  # noqa: E402
CHUNK = 1024 * 1024
from lightroom import import_lrcat  # noqa: E402
import catalogs  # noqa: E402
import conversione  # noqa: E402
import gestione_file  # noqa: E402
import esporta  # noqa: E402
import exif  # noqa: E402
import salva  # noqa: E402
import luoghi  # noqa: E402
import thumbs  # noqa: E402

PORT = int(os.environ.get("FOTOCAT_PORT", "8765"))

# Catalogo aperto in questo momento (None = nessuno, es. in attesa della password)
CURRENT = None
cat = None
THUMB_DIR = None
switch_lock = threading.Lock()
STATIC = os.path.join(HERE, "static")
LABELS = ("red", "yellow", "green", "blue", "purple")
# formati "definitivi": tutto il resto finisce in "Da convertire"
FINAL_EXT = ("dng", "jpg", "jpeg", "jpe", "heic", "heif", "psd", "psb")
from catalog import VIDEO_EXT  # noqa: E402
NOT_TO_CONVERT = FINAL_EXT + tuple(sorted(VIDEO_EXT))
VIDEO_TYPES = {"mp4": "video/mp4", "m4v": "video/x-m4v", "mov": "video/quicktime", "3gp": "video/3gpp",
               "webm": "video/webm"}


# ---------------------------------------------------------------- lavori in background
class Job:
    def __init__(self, label):
        self.id = uuid.uuid4().hex[:8]
        self.label, self.message = label, "In attesa…"
        self.done, self.total = 0, None
        self.status = "running"
        self.result = None
        self.started = time.time()
        self.ended = None
        self.cancelled = False
        self.quick = False
        self.cancellable = False
        self.thread = None

    def update(self, done=None, message=None, total=None):
        if done is not None:
            self.done = done
        if message is not None:
            self.message = message
        if total is not None:
            self.total = total

    def as_dict(self):
        return {k: getattr(self, k) for k in
                ("id", "label", "message", "done", "total", "status", "result", "quick", "cancellable", "cancelled",
                 "ended")}


JOBS = {}
JOB_KEEP_S = 60


# ---------------------------------------------------------------- cataloghi
def stop_quick_jobs():
    """La sincronizzazione rapida si può interrompere in qualsiasi momento (es. cambio catalogo)."""
    for j in list(JOBS.values()):
        if j.quick and j.status == "running":
            j.cancelled = True
            j.thread.join(timeout=30)


def jobs_running():
    return any(j.status == "running" for j in JOBS.values())


def start_quick_sync():
    if cat.db.execute("SELECT 1 FROM roots LIMIT 1").fetchone():
        def run(job):
            try:
                return cat.quick_sync(job)
            finally:
                and_gps(None)               # anche se la sincronizzazione non riesce (es. NAS)
        start_job("Sincronizzazione rapida", run, quick=True)


# ---------------------------------------------------------------- posizioni GPS
def read_gps(path, ext):
    """(lat, lon) di un file, oppure None. Lettore interno per JPEG, RAW TIFF e video
    MP4/MOV; sul Mac ImageIO (fcimg info) per tutto il resto (HEIC, CR3, PSD, PNG…)."""
    ext = (ext or "").lower()
    if ext in EXIF_EXT:
        ex = exif.read_exif(path, head=128 * 1024)
        if ex.get("gps_beyond"):
            ex = exif.read_exif(path, head=4 * 1024 * 1024)
        if "lat" in ex:
            return ex["lat"], ex["lon"]
        if ext not in ("tif", "tiff") or not thumbs.find_imghelper():
            return None
    elif ext in QT_EXT:
        ex = exif.read_video(path)
        return (ex["lat"], ex["lon"]) if "lat" in ex else None
    elif ext in VIDEO_EXT:
        return None
    helper = thumbs.find_imghelper()
    if not helper:
        return None
    try:
        r = subprocess.run([helper, "info", path], capture_output=True, timeout=20)
        d = json.loads(r.stdout or b"{}") if r.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if isinstance(d.get("lat"), (int, float)) and isinstance(d.get("lon"), (int, float)):
        return round(d["lat"], 6), round(d["lon"], 6)
    return None


def gps_pending():
    return cat.db.execute("SELECT count(*) FROM photos WHERE gps_read=0 AND missing=0").fetchone()[0]


def places_pending(c):
    """Foto con posizione ma senza nome del luogo (o con nomi di una versione precedente)."""
    r = c.db.execute("SELECT v FROM meta WHERE k='luoghi'").fetchone()
    if not r or r[0] != luoghi.VERSION:
        with c.write_lock:
            c.db.execute("UPDATE photos SET place=NULL")
            c.db.execute("INSERT OR REPLACE INTO meta VALUES('luoghi', ?)", (luoghi.VERSION,))
            c.db.commit()
    return c.db.execute("SELECT count(*) FROM photos WHERE gps_lat IS NOT NULL AND place IS NULL").fetchone()[0]


def fill_places(c, job=None):
    """Dà un nome del luogo alle foto che hanno la posizione (veloce: niente accesso ai file)."""
    rows = c.db.execute("SELECT id, gps_lat, gps_lon FROM photos WHERE gps_lat IS NOT NULL AND place IS NULL").fetchall()
    for i in range(0, len(rows), 500):
        if job and job.cancelled:
            break
        upd = [(luoghi.place_name(la, lo) or "", pid) for pid, la, lo in rows[i:i + 500]]
        with c.write_lock:
            c.db.executemany("UPDATE photos SET place=? WHERE id=?", upd)
            c.db.commit()
        if job:
            job.update(None, f"Nomi dei luoghi: {min(i + 500, len(rows))} / {len(rows)}")


def start_gps_job():
    """Legge in background le posizioni delle foto non ancora lette (es. un catalogo creato
    prima di questa funzione). Si può interrompere: riprende da dove era rimasto."""
    if cat is None or any(j.label == "Posizioni GPS" and j.status == "running" for j in JOBS.values()):
        return
    if not gps_pending() and not places_pending(cat):
        return
    job_cat = cat

    def run(job):
        fill_places(job_cat, job)
        rows = job_cat.db.execute("""SELECT id, ext FROM photos WHERE gps_read=0 AND missing=0
                                     ORDER BY capture_time DESC""").fetchall()
        total, found, batch = len(rows), 0, []

        def flush():
            if batch:
                with job_cat.write_lock:
                    job_cat.db.executemany("UPDATE photos SET gps_lat=?, gps_lon=?, place=?, gps_read=1 WHERE id=?", batch)
                    job_cat.db.commit()
                batch.clear()
        job.update(0, f"Posizioni GPS: 0 / {total}", total=total)
        print(f"Posizioni GPS: inizio, {total} foto da leggere", flush=True)
        for n, (pid, ext) in enumerate(rows, 1):
            if job.cancelled or job_cat.closed:
                break
            thumbs.wait_viewer_idle()           # le miniature di chi guarda hanno la precedenza
            path = None
            try:
                path = job_cat.photo_path(pid)
                if path and os.path.exists(path):   # NAS scollegato: si riproverà la prossima volta
                    pos = read_gps(path, ext)
                    found += bool(pos)
                    batch.append((pos[0] if pos else None, pos[1] if pos else None,
                                  (luoghi.place_name(*pos) or "") if pos else None, pid))
            except Exception as e:  # noqa: BLE001  un file strano non deve fermare tutto
                print(f"Posizioni GPS: file saltato {path or pid}: {e!r}", flush=True)
                batch.append((None, None, None, pid))
            if len(batch) >= 200:
                flush()
            if n % 50 == 0 or n == total:
                job.update(n, f"Posizioni GPS: {n} / {total} ({found} con posizione)")
            if n % 5000 == 0:
                print(f"Posizioni GPS: {n} / {total} ({found} con posizione)", flush=True)
        flush()
        fill_places(job_cat, job)            # foto con posizione arrivate nel frattempo (importazioni)
        print(f"Posizioni GPS: {'interrotta' if job.cancelled else 'fine'} dopo {n if rows else 0} foto, "
              f"{found} con posizione", flush=True)
        return {"gps": True, "lette": total, "trovate": found, "interrotta": job.cancelled}
    job = start_job("Posizioni GPS", run, quick=True)
    job.cancellable = True


def and_gps(res):
    """Dopo una sincronizzazione: legge le posizioni dei file nuovi che ne hanno bisogno."""
    try:
        start_gps_job()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return res


def gps_points(q):
    """Punti della mappa per la vista corrente (tutti i filtri tranne l'area): posizioni
    arrotondate a ~100 m con il numero di foto in ciascuna."""
    q = dict(q)
    q.pop("geo", None)
    where, args, _ = build_query(q)
    db = cat.db
    rows = db.execute(f"""SELECT round(p.gps_lat, 3), round(p.gps_lon, 3), count(*), max(p.place) FROM photos p
                          WHERE {where} AND p.gps_lat IS NOT NULL GROUP BY 1, 2""", args).fetchall()
    total = db.execute(f"SELECT count(*) FROM photos p WHERE {where}", args).fetchone()[0]
    pending = gps_pending()
    if pending:
        start_gps_job()                     # se per qualche motivo non è partita, parte ora
    return {"points": [list(r) for r in rows], "total": total, "with": sum(r[2] for r in rows),
            "pending": pending}


def close_current():
    global CURRENT, cat, THUMB_DIR
    if CURRENT:
        old = CURRENT
        CURRENT, cat, THUMB_DIR = None, None, None
        old.close()
    thumbs.reset()


def open_catalog(path, password=None):
    global CURRENT, cat, THUMB_DIR
    with switch_lock:
        if CURRENT and CURRENT.path == path:
            return CURRENT.info()
        stop_quick_jobs()
        if jobs_running():
            raise ValueError("Attendi la fine dei lavori in corso prima di cambiare catalogo")
        oc = catalogs.OpenCatalog(path, password)   # se la password è errata, resta aperto il precedente
        close_current()
        CURRENT, cat, THUMB_DIR = oc, oc.cat, oc.thumb_dir
        JOBS.clear()
        gestione_file.LAST.clear()
        catalogs.remember(path)
        start_quick_sync()
        return oc.info()


def catalogs_state():
    s = catalogs.load_settings()
    default = s.get("default")
    return {
        "current": CURRENT.info() if CURRENT else None,
        "default": default,
        "base": catalogs.BASE,
        "startup": catalogs.startup_catalog(),
        "catalogs": [{"path": p, "name": catalogs.display_name(p), "protected": catalogs.is_protected(p),
                      "default": p == default, "open": bool(CURRENT and CURRENT.path == p)}
                     for p in catalogs.list_catalogs()],
    }


def catalogs_post(action, body):
    if action == "open":
        return open_catalog(body["path"], body.get("password"))
    if action == "create":
        pw = body.get("password") or None
        if pw and pw != body.get("password2"):
            raise ValueError("Le due password non coincidono")
        path = catalogs.create(body.get("name"), pw)
        if body.get("default"):
            s = catalogs.load_settings(); s["default"] = path; catalogs.save_settings(s)
        return open_catalog(path, pw)
    if action == "default":
        s = catalogs.load_settings()
        s["default"] = body.get("path") or None
        catalogs.save_settings(s)
        return None
    if action == "lock":
        with switch_lock:
            stop_quick_jobs()
            if jobs_running():
                raise ValueError("Attendi la fine dei lavori in corso")
            close_current()
        return None
    if action == "protect":
        pw = body.get("password") or ""
        if pw != body.get("password2"):
            raise ValueError("Le due password non coincidono")
        with switch_lock:
            if not CURRENT or CURRENT.protected:
                raise ValueError("Apri prima il catalogo da proteggere")
            stop_quick_jobs()
            if jobs_running():
                raise ValueError("Attendi la fine dei lavori in corso")
            if len(pw) < 4:
                raise ValueError("La password deve avere almeno 4 caratteri")
            oc = CURRENT
            close_current()
            try:
                new_path = catalogs.protect_existing(oc, pw)
            except Exception:
                reopen = catalogs.OpenCatalog(oc.path)   # torna al catalogo originale, intatto
                globals().update(CURRENT=reopen, cat=reopen.cat, THUMB_DIR=reopen.thumb_dir)
                raise
        return open_catalog(new_path, pw)
    if action == "password":
        if body.get("new") != body.get("new2"):
            raise ValueError("Le due nuove password non coincidono")
        with switch_lock:
            if not CURRENT or not CURRENT.protected:
                raise ValueError("Il catalogo aperto non è protetto")
            if not CURRENT.check_password(body.get("old")):
                raise ValueError("Password attuale errata")
            stop_quick_jobs()
            if jobs_running():
                raise ValueError("Attendi la fine dei lavori in corso")
            path = CURRENT.path
            close_current()
        try:
            catalogs.change_password(path, body.get("old", ""), body.get("new", ""))
        except Exception:
            open_catalog(path, body.get("old", ""))
            raise
        return open_catalog(path, body.get("new"))
    raise ValueError("Comando sconosciuto")


def client_alive(sock):
    """False se il browser ha chiuso la connessione (non vuole più la risposta)."""
    try:
        ready, _, _ = select.select([sock], [], [], 0)
        if ready:
            return sock.recv(1, socket.MSG_PEEK) != b""
        return True
    except (OSError, ValueError):
        return False


def rollback(c):
    """Annulla una modifica al database rimasta a metà per un errore (altrimenti il
    catalogo resterebbe occupato e le operazioni successive aspetterebbero)."""
    try:
        if c is not None and not c.closed:
            c.db.rollback()
    except Exception:  # noqa: BLE001
        pass


def start_job(label, fn, quick=False):
    job = Job(label)
    job.quick = quick
    job_cat = cat
    JOBS[job.id] = job

    def run():
        try:
            job.result = fn(job)
            job.status = "done"
            job.message = "Completato"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            rollback(job_cat)
            job.status = "error"
            job.message = str(e)
        job.ended = time.time()
    job.thread = threading.Thread(target=run, daemon=True)
    job.thread.start()
    return job


# ---------------------------------------------------------------- query foto
def scope_filter(scope, deep=True):
    kind, _, val = (scope or "all").partition(":")
    if kind == "folder":
        f = cat.db.execute("SELECT root_id, rel_path FROM folders WHERE id=?", (int(val),)).fetchone()
        if not f:
            return "0", []
        if not deep:
            return "p.folder_id=?", [int(val)]
        if f["rel_path"] == "":
            return "p.folder_id IN (SELECT id FROM folders WHERE root_id=?)", [f["root_id"]]
        return ("""p.folder_id IN (SELECT id FROM folders WHERE root_id=? AND
                   (rel_path=? OR substr(rel_path,1,?)=?))""",
                [f["root_id"], f["rel_path"], len(f["rel_path"]) + 1, f["rel_path"] + "/"])
    if kind == "collection":
        ids = collection_descendants(int(val))
        return (f"p.id IN (SELECT photo_id FROM collection_photos WHERE collection_id IN "
                f"({','.join(map(str, ids))}))", [])
    if kind == "keyword":
        return "p.id IN (SELECT photo_id FROM photo_keywords WHERE keyword_id=?)", [int(val)]
    if kind == "toconvert":
        return f"COALESCE(p.ext,'') NOT IN ({','.join(repr(e) for e in NOT_TO_CONVERT)})", []
    if kind == "video":
        return f"p.ext IN ({','.join(repr(e) for e in sorted(VIDEO_EXT))})", []
    if kind == "torename":
        return "fc_camname(p.filename)=1", []
    if kind == "nocoll":
        return "NOT EXISTS (SELECT 1 FROM collection_photos cp WHERE cp.photo_id=p.id)", []
    if kind == "missing":
        return "p.missing=1", []
    if kind == "rejected":
        return "p.flag=-1", []
    if kind == "recent":
        return "p.added_at >= (SELECT datetime(max(added_at), '-1 day') FROM photos)", []
    return "1", []


def collection_descendants(cid):
    rows = cat.db.execute("SELECT id, parent_id FROM collections").fetchall()
    kids = {}
    for r in rows:
        kids.setdefault(r["parent_id"], []).append(r["id"])
    out, stack = [], [cid]
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(kids.get(c, []))
    return out


def build_query(q):
    where, args = scope_filter(q.get("scope"), q.get("deep", "1") == "1")
    where = [where]
    text = (q.get("q") or "").strip()
    if text:
        for word in text.split():
            like = f"%{word}%"
            where.append("""(p.filename LIKE ? OR p.place LIKE ? OR p.id IN (SELECT pk.photo_id FROM photo_keywords pk
                            JOIN keywords k ON k.id=pk.keyword_id WHERE k.name LIKE ?)
                            OR p.folder_id IN (SELECT id FROM folders WHERE rel_path LIKE ?))""")
            args += [like, like, like, like]
    if q.get("geo"):                        # area della mappa: sud, ovest, nord, est
        try:
            s_, w_, n_, e_ = (float(x) for x in q["geo"].split(","))
        except ValueError:
            s_ = w_ = n_ = e_ = 0.0
        where.append("p.gps_lat BETWEEN ? AND ?")
        args += [s_, n_]
        if w_ <= e_:
            where.append("p.gps_lon BETWEEN ? AND ?")
            args += [w_, e_]
        else:                               # area che attraversa la linea del cambio di data
            where.append("(p.gps_lon >= ? OR p.gps_lon <= ?)")
            args += [w_, e_]
    if q.get("rating"):
        where.append("p.rating >= ?")
        args.append(int(q["rating"]))
    if q.get("label"):
        if q["label"] == "none":
            where.append("p.label IS NULL")
        else:
            where.append("p.label = ?")
            args.append(q["label"])
    if q.get("flag"):
        where.append("p.flag = ?")
        args.append(int(q["flag"]))
    if q.get("ext"):
        exts = [e.strip().lower() for e in q["ext"].split(",") if e.strip()]
        where.append(f"p.ext IN ({','.join('?' * len(exts))})")
        args += exts
    if q.get("year"):
        where.append("substr(p.capture_time,1,4) = ?")
        args.append(q["year"])
    sort = {"date": "p.capture_time", "name": "p.filename COLLATE NOCASE",
            "added": "p.added_at", "rating": "p.rating"}.get(q.get("sort"), "p.capture_time")
    direction = "DESC" if q.get("dir", "asc") == "desc" else "ASC"
    return " AND ".join(where), args, f"{sort} {direction}, p.id {direction}"


def list_photos(q):
    where, args, order = build_query(q)
    db = cat.db
    offset = int(q.get("offset", 0))
    limit = min(int(q.get("limit", 300)), 1_000_000 if q.get("ids_only") else 5000)
    if q.get("ids_only"):
        rows = db.execute(f"SELECT p.id FROM photos p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                          args + [limit, offset]).fetchall()
        return {"ids": [r[0] for r in rows]}
    total = db.execute(f"SELECT count(*) FROM photos p WHERE {where}", args).fetchone()[0]
    rows = db.execute(
        f"""SELECT p.id, p.filename, p.ext, p.capture_time, p.rating, p.label, p.flag, p.missing,
                   p.added_at, p.mtime, p.rotation
            FROM photos p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?""",
        args + [limit, offset]).fetchall()
    # prepara in background le miniature di questa pagina (quelle visibili arrivano comunque prima)
    thumbs.PREFETCH.add(cat, THUMB_DIR, [r["id"] for r in rows if not r["missing"]])
    # th: miniatura già pronta (1), impossibile (-1) o da creare (0): la griglia mostra
    # subito quelle pronte e chiede le altre con /api/thumbs/want
    return {"total": total, "offset": offset, "items": [
        {"id": r["id"], "n": r["filename"], "x": r["ext"], "t": r["capture_time"],
         "r": r["rating"], "l": r["label"], "f": r["flag"], "m": r["missing"], "v": version(r),
         "o": r["rotation"] or 0, "th": thumbs.PREFETCH.state(THUMB_DIR, r["id"])} for r in rows]}


def version(r):
    """Cambia quando cambia il file (es. dopo una conversione): evita miniature vecchie in cache."""
    return format(zlib.crc32(f"{r['added_at']}|{r['mtime']}|{r['rotation']}".encode()), "x")


def remove_missing(ids):
    """Toglie dal catalogo le foto il cui file non esiste più. I file non vengono toccati."""
    with cat.write_lock:
        db = cat.db
        if ids is None:
            ids = [r[0] for r in db.execute("SELECT id FROM photos WHERE missing=1")]
        else:
            ids = [r[0] for i in range(0, len(ids), 900) for r in db.execute(
                f"SELECT id FROM photos WHERE missing=1 AND id IN ({','.join('?' * len(ids[i:i + 900]))})",
                ids[i:i + 900])]
        for i in range(0, len(ids), 900):
            chunk = ",".join(map(str, ids[i:i + 900]))
            db.execute(f"DELETE FROM collection_photos WHERE photo_id IN ({chunk})")
            db.execute(f"DELETE FROM photo_keywords WHERE photo_id IN ({chunk})")
            db.execute(f"DELETE FROM photos WHERE id IN ({chunk})")
        db.commit()
    for pid in ids:
        thumbs.drop_cached(THUMB_DIR, pid)
    return {"removed": len(ids)}


# ---------------------------------------------------------------- dati di scatto
SHOT_CACHE = collections.OrderedDict()      # (percorso, mtime) -> dati, gli ultimi 3000
MAKES = {"nikon corporation": "Nikon", "nikon": "Nikon", "canon": "Canon", "sony": "Sony",
         "fujifilm": "Fujifilm", "olympus imaging corp.": "Olympus", "olympus corporation": "Olympus",
         "om digital solutions": "OM System", "panasonic": "Panasonic", "pentax": "Pentax",
         "ricoh imaging company, ltd.": "Ricoh", "leica camera ag": "Leica", "apple": "Apple",
         "samsung": "Samsung", "dji": "DJI", "gopro": "GoPro", "hasselblad": "Hasselblad",
         "sigma": "Sigma", "google": "Google", "xiaomi": "Xiaomi", "huawei": "Huawei"}


def _camera_name(make, model):
    make = (make or "").strip()
    model = (model or "").strip()
    nice = MAKES.get(make.lower(), make.title() if make.isupper() else make)
    if not model:
        return nice or None
    first = nice.split()[0] if nice else ""
    if first and model.lower().startswith(first.lower()):
        return first + model[len(first):]          # «NIKON Z 6_2» -> «Nikon Z 6_2»
    if nice:
        return f"{nice} {model}"
    return model


def _shot_format(raw):
    out = {}
    cam = _camera_name(raw.get("make"), raw.get("model"))
    if cam:
        out["camera"] = cam
    lens = (raw.get("lens") or "").strip()
    if lens and lens not in ("----", "0.0 mm f/0.0"):
        out["lens"] = lens
    try:
        f = float(raw.get("focal") or 0)
        if f > 0:
            txt = f"{round(f, 1):g} mm".replace(".", ",")
            f35 = float(raw.get("focal35") or 0)
            if f35 and abs(f35 - f) >= 1:
                txt += f" (equivalente {f35:g} mm)"
            out["focal"] = txt
    except (TypeError, ValueError):
        pass
    try:
        t = float(raw.get("exposure") or 0)
        if t > 0:
            out["exposure"] = (f"{t:g} s".replace(".", ",") if t >= 0.4
                               else f"1/{round(1 / t)} s")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        fn = float(raw.get("fnumber") or 0)
        if fn > 0:
            out["fnumber"] = f"ƒ/{fn:.1f}".replace(".0", "").replace(".", ",")
    except (TypeError, ValueError):
        pass
    try:
        iso = int(float(raw.get("iso") or 0))
        if iso > 0:
            out["iso"] = f"ISO {iso}"
    except (TypeError, ValueError):
        pass
    return out


def shooting_info(path, mtime):
    """Fotocamera, obiettivo, focale, tempo, apertura e ISO, letti dai metadati del file.
    Sul Mac con ImageIO (fcimg info: tutti i formati, anche CR3, HEIC, PSD), altrimenti
    con il lettore EXIF interno (JPEG e RAW basati su TIFF)."""
    key = (path, mtime)
    if key in SHOT_CACHE:
        SHOT_CACHE.move_to_end(key)
        return SHOT_CACHE[key]
    raw = {}
    helper = thumbs.find_imghelper()
    if helper:
        try:
            r = subprocess.run([helper, "info", path], capture_output=True, timeout=15)
            if r.returncode == 0:
                raw = json.loads(r.stdout or b"{}")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raw = {}
    if not raw:
        raw = exif.read_exif(path, shooting=True)
    info = _shot_format(raw)
    SHOT_CACHE[key] = info
    while len(SHOT_CACHE) > 3000:
        SHOT_CACHE.popitem(last=False)
    return info


def photo_detail(pid):
    db = cat.db
    p = db.execute("""SELECT p.*, f.rel_path, r.path AS root, r.id AS root_id FROM photos p
                      JOIN folders f ON f.id=p.folder_id JOIN roots r ON r.id=f.root_id
                      WHERE p.id=?""", (pid,)).fetchone()
    if not p:
        return None
    d = dict(p)
    d["path"] = os.path.join(p["root"], p["rel_path"], p["filename"])
    d["v"] = version(p)
    d["shot"] = {}
    if not p["missing"] and (p["ext"] or "").lower() not in VIDEO_EXT:
        full = resolve_path(d["path"])
        if os.path.isfile(full):
            d["shot"] = shooting_info(full, p["mtime"])
    d["collections"] = [dict(r) for r in db.execute(
        """SELECT c.id, c.name, c.parent_id FROM collection_photos cp
           JOIN collections c ON c.id=cp.collection_id WHERE cp.photo_id=? ORDER BY c.name""", (pid,))]
    d["keywords"] = [dict(r) for r in db.execute(
        """SELECT k.id, k.name FROM photo_keywords pk JOIN keywords k ON k.id=pk.keyword_id
           WHERE pk.photo_id=? ORDER BY k.name""", (pid,))]
    return d


_root_state = {}     # id cartella principale -> raggiungibile all'ultimo controllo


def _reachable(path, timeout=2.0):
    """isdir con un limite di tempo: un NAS che non risponde non deve bloccare nulla."""
    out = []
    t = threading.Thread(target=lambda: out.append(os.path.isdir(path)), daemon=True)
    t.start()
    t.join(timeout)
    return bool(out and out[0])


def roots_status():
    """Stato delle cartelle principali, controllato spesso dall'interfaccia. Quando una torna
    raggiungibile (NAS ricollegato) si riprovano subito le anteprime che erano fallite."""
    res = []
    for r in cat.db.execute("SELECT id, name, path FROM roots ORDER BY name").fetchall():
        on = _reachable(r["path"])
        if on and _root_state.get(r["id"]) is False:
            thumbs._unavailable.clear()
        _root_state[r["id"]] = on
        res.append({"id": r["id"], "name": r["name"], "online": on})
    return res


def tree():
    db = cat.db
    fcount = dict(db.execute("SELECT folder_id, count(*) FROM photos GROUP BY folder_id").fetchall())
    ccount = dict(db.execute(
        "SELECT collection_id, count(*) FROM collection_photos GROUP BY collection_id").fetchall())
    kcount = dict(db.execute(
        "SELECT keyword_id, count(*) FROM photo_keywords GROUP BY keyword_id").fetchall())
    roots = [{"id": r["id"], "path": r["path"], "name": r["name"],
              "online": _reachable(r["path"])} for r in db.execute("SELECT * FROM roots ORDER BY name")]
    for r in roots:
        _root_state[r["id"]] = r["online"]
    folders = [{"id": r["id"], "root": r["root_id"], "parent": r["parent_id"], "rel": r["rel_path"],
                "name": os.path.basename(r["rel_path"]) if r["rel_path"] else None,
                "count": fcount.get(r["id"], 0)}
               for r in db.execute("SELECT * FROM folders ORDER BY rel_path COLLATE NOCASE")]
    colls = [{"id": r["id"], "name": r["name"], "parent": r["parent_id"], "set": r["is_set"],
              "count": ccount.get(r["id"], 0)}
             for r in db.execute("SELECT * FROM collections ORDER BY is_set DESC, name COLLATE NOCASE")]
    kws = [{"id": r["id"], "name": r["name"], "parent": r["parent_id"], "count": kcount.get(r["id"], 0)}
           for r in db.execute("SELECT * FROM keywords ORDER BY name COLLATE NOCASE")]
    stats = db.execute(f"""SELECT count(*) AS total, sum(missing) AS missing,
        (SELECT count(*) FROM photos p WHERE NOT EXISTS
           (SELECT 1 FROM collection_photos cp WHERE cp.photo_id=p.id)) AS nocoll,
        (SELECT count(*) FROM photos p WHERE COALESCE(p.ext,'') NOT IN
           ({','.join(repr(e) for e in NOT_TO_CONVERT)})) AS toconvert,
        (SELECT count(*) FROM photos p WHERE p.ext IN
           ({','.join(repr(e) for e in sorted(VIDEO_EXT))})) AS video,
        (SELECT count(*) FROM photos p WHERE fc_camname(p.filename)=1) AS torename,
        (SELECT count(*) FROM photos p WHERE p.flag=-1) AS rejected
        FROM photos""").fetchone()
    return {"roots": roots, "folders": folders, "collections": colls, "keywords": kws,
            "stats": dict(stats)}


# ---------------------------------------------------------------- azioni
def ids_of(body):
    return [int(i) for i in body.get("ids", [])]


def add_to_collection(cid, ids):
    with cat.write_lock:
        c = cat.db.execute("SELECT is_set FROM collections WHERE id=?", (cid,)).fetchone()
        if not c or c["is_set"]:
            raise ValueError("Le foto si possono aggiungere solo a una raccolta, non a un set")
        ts = now()
        cat.db.executemany("INSERT OR IGNORE INTO collection_photos VALUES(?,?,?)",
                           [(cid, i, ts) for i in ids])
        cat.db.commit()


def remove_from_collection(cid, ids):
    with cat.write_lock:
        cat.db.executemany("DELETE FROM collection_photos WHERE collection_id=? AND photo_id=?",
                           [(cid, i) for i in ids])
        cat.db.commit()


def set_meta(body):
    ids = ids_of(body)
    sets, vals = [], []
    if "rating" in body:
        sets.append("rating=?")
        vals.append(max(0, min(5, int(body["rating"]))))
    if "label" in body:
        sets.append("label=?")
        vals.append(body["label"] if body["label"] in LABELS else None)
    if "flag" in body:
        sets.append("flag=?")
        vals.append(int(body["flag"]))
    if sets and ids:
        with cat.write_lock:
            cat.db.executemany(f"UPDATE photos SET {', '.join(sets)} WHERE id=?",
                               [vals + [i] for i in ids])
            cat.db.commit()


def edit_keywords(body):
    ids = ids_of(body)
    with cat.write_lock:
        db = cat.db
        for name in body.get("add", []):
            name = nfc(name.strip())
            if not name:
                continue
            db.execute("INSERT OR IGNORE INTO keywords(name, parent_id) VALUES(?,0)", (name,))
            kid = db.execute("SELECT id FROM keywords WHERE name=? AND parent_id=0", (name,)).fetchone()[0]
            db.executemany("INSERT OR IGNORE INTO photo_keywords VALUES(?,?)", [(i, kid) for i in ids])
        for kid in body.get("remove", []):
            db.executemany("DELETE FROM photo_keywords WHERE photo_id=? AND keyword_id=?",
                           [(i, int(kid)) for i in ids])
        db.commit()


def pick_path(kind):
    """Apre la finestra nativa di macOS per scegliere una cartella o un file."""
    if sys.platform != "darwin":
        return None
    what = ('choose folder with prompt "Scegli la cartella delle foto"' if kind == "folder"
            else 'choose folder with prompt "Scegli la cartella di destinazione"' if kind == "dest"
            else 'choose file with prompt "Scegli il catalogo Lightroom (.lrcat)"')
    r = subprocess.run(["osascript", "-e", 'tell application "Finder"', "-e", "activate",
                        "-e", f"set p to POSIX path of ({what})", "-e", "end tell",
                        "-e", "return p"], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def folder_path(fid):
    r = cat.db.execute("""SELECT r.path, f.rel_path FROM folders f JOIN roots r ON r.id=f.root_id
                          WHERE f.id=?""", (fid,)).fetchone()
    return os.path.join(r["path"], r["rel_path"]) if r else None


def open_photos(ids, how, folder=None):
    if folder:
        path = folder_path(int(folder))
        if not path or not os.path.isdir(path):
            raise ValueError("Cartella non trovata: il NAS è collegato?")
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        return {"paths": [path]}
    paths = [p for p in (cat.photo_path(i) for i in ids[:50]) if p and os.path.exists(p)]
    if not paths:
        raise ValueError("File non trovato: il NAS è collegato?")
    if sys.platform != "darwin":
        return {"paths": paths}
    if how == "finder":
        subprocess.Popen(["open", "-R", paths[0]])
    elif how == "photoshop":
        subprocess.Popen(["open", "-b", "com.adobe.Photoshop"] + paths)
    else:
        subprocess.Popen(["open"] + paths)
    return {"paths": paths}


def pregenerate(scope_q):
    def run(job):
        where, args, order = build_query(scope_q)
        ids = [r[0] for r in cat.db.execute(
            f"SELECT p.id FROM photos p WHERE {where} AND p.missing=0 ORDER BY {order}", args)]
        todo = [i for i in ids if not os.path.exists(thumbs.thumb_file(THUMB_DIR, i, "m"))]
        job.update(0, f"Anteprime da creare: {len(todo)}", total=len(todo))
        done = [0]
        lock = threading.Lock()

        def one(pid):
            if job.cancelled:
                return
            thumbs.wait_viewer_idle()          # chi guarda la griglia ha la precedenza
            thumbs.get_thumb(cat, THUMB_DIR, pid, "m", slots=thumbs.BG_SLOTS)
            with lock:
                done[0] += 1
                if done[0] % 20 == 0 or done[0] == len(todo):
                    job.update(done[0], f"Anteprime: {done[0]} / {len(todo)}")
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(one, todo))
        return {"create": len(todo)}
    job = start_job("Generazione anteprime", run)
    job.cancellable = True
    return job


def delete_collection(cid):
    with cat.write_lock:
        ids = collection_descendants(cid)
        q = ",".join(map(str, ids))
        cat.db.execute(f"DELETE FROM collection_photos WHERE collection_id IN ({q})")
        cat.db.execute(f"DELETE FROM collections WHERE id IN ({q})")
        cat.db.commit()


def remove_root(rid):
    with cat.write_lock:
        db = cat.db
        sub = "SELECT p.id FROM photos p JOIN folders f ON f.id=p.folder_id WHERE f.root_id=?"
        db.execute(f"DELETE FROM collection_photos WHERE photo_id IN ({sub})", (rid,))
        db.execute(f"DELETE FROM photo_keywords WHERE photo_id IN ({sub})", (rid,))
        db.execute("DELETE FROM photos WHERE folder_id IN (SELECT id FROM folders WHERE root_id=?)", (rid,))
        db.execute("DELETE FROM folders WHERE root_id=?", (rid,))
        db.execute("DELETE FROM roots WHERE id=?", (rid,))
        db.commit()


def relocate(body):
    """Sostituisce l'inizio del percorso delle cartelle radice (es. NAS rimontato altrove)."""
    old, new = body["from"].rstrip("/"), body["to"].rstrip("/")
    n = 0
    with cat.write_lock:
        for r in cat.db.execute("SELECT id, path FROM roots").fetchall():
            if r["path"] == old or r["path"].startswith(old + "/"):
                cat.db.execute("UPDATE roots SET path=? WHERE id=?", (new + r["path"][len(old):], r["id"]))
                n += 1
        cat.db.commit()
    return {"changed": n}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, ctype, cache=True, private=False):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # le miniature dei cataloghi protetti non devono finire nella cache del disco
        self.send_header("Cache-Control", "no-store" if private else "max-age=86400" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def send_range(self, path, ctype, private=False):
        """Invio a pezzi con supporto 'Range': serve per riprodurre e scorrere i video."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        partial = rng.startswith("bytes=")
        if partial:
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a.strip():
                start = int(a)
                end = min(int(b), size - 1) if b.strip() else size - 1
            else:
                start = max(0, size - int(b))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store" if private else "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1024 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def not_found(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = u.path.strip("/").split("/")
        try:
            if u.path == "/":
                return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8", False)
            if parts[0] == "static" and len(parts) == 2 and "/" not in parts[1] and ".." not in parts[1]:
                p = os.path.join(STATIC, parts[1])
                ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                         "svg": "image/svg+xml", "png": "image/png", "woff2": "font/woff2", "json": "application/json",
                         "txt": "text/plain; charset=utf-8"}.get(p.rsplit(".", 1)[-1], "application/octet-stream")
                return (self.send_file(p, ctype, ctype == "font/woff2") if os.path.isfile(p)
                        else self.not_found())
            if u.path == "/api/catalogs":
                return self.send_json(catalogs_state())
            if u.path == "/api/dng":
                return self.send_json({"available": bool(conversione.find_converter())})
            if cat is None:
                return self.send_json({"error": "Nessun catalogo aperto", "nocatalog": True}, 409)
            if parts[0] == "full" and len(parts) == 2:
                c, tdir, private = cat, THUMB_DIR, CURRENT.protected
                path = thumbs.get_full(c, tdir, int(parts[1]))
                if not path:
                    return self.not_found()
                ext = path.rsplit(".", 1)[-1].lower()
                if ext in VIDEO_EXT:
                    return self.send_range(path, VIDEO_TYPES.get(ext, "application/octet-stream"), private)
                ctype = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
                return self.send_file(path, ctype, private=private)
            if parts[0] in ("thumb", "preview") and len(parts) == 2:
                c, tdir, private = cat, THUMB_DIR, CURRENT.protected
                path = thumbs.get_thumb(c, tdir, int(parts[1]), "m" if parts[0] == "thumb" else "l",
                                        alive=lambda: client_alive(self.connection))
                return self.send_file(path, "image/jpeg", private=private) if path else self.not_found()
            if u.path == "/api/tree":
                return self.send_json(tree())
            if u.path == "/api/roots/status":
                return self.send_json(roots_status())
            if u.path == "/api/gps":
                return self.send_json(gps_points(q))
            if u.path == "/api/photos":
                return self.send_json(list_photos(q))
            if parts[:2] == ["api", "photo"] and len(parts) == 3:
                d = photo_detail(int(parts[2]))
                return self.send_json(d) if d else self.not_found()
            if u.path == "/api/jobs":
                # i lavori finiti bene spariscono da soli dopo un minuto; gli errori restano
                now = time.time()
                for k in [k for k, j in JOBS.items() if j.status == "done" and j.ended and now - j.ended > JOB_KEEP_S]:
                    del JOBS[k]
                return self.send_json([j.as_dict() for j in JOBS.values()])
            if u.path == "/api/duplicates":
                return self.send_json(gestione_file.LAST)
            if u.path == "/api/years":
                return self.send_json([r[0] for r in cat.db.execute(
                    "SELECT DISTINCT substr(capture_time,1,4) y FROM photos WHERE y IS NOT NULL ORDER BY y DESC")])
            return self.not_found()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def do_PUT(self):
        """Importazione di un file trascinato nella finestra: /api/upload?folder=ID&path=sotto/nome.ext&mtime=ms"""
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        left = int(self.headers.get("Content-Length") or 0)

        def drain():
            nonlocal left
            while left > 0:
                left -= len(self.rfile.read(min(left, CHUNK)))
        try:
            if u.path != "/api/upload" or cat is None:
                drain()
                return self.send_json({"error": "Nessun catalogo aperto"}, 409)
            parts = gestione_file.safe_subpath(q.get("path"))
            name = parts[-1]
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in PHOTO_EXT:
                drain()
                return self.send_json({"skipped": "non è una foto o un video"})
            f = cat.db.execute("""SELECT f.root_id, f.rel_path, r.path FROM folders f
                                  JOIN roots r ON r.id=f.root_id WHERE f.id=?""", (int(q["folder"]),)).fetchone()
            if not f:
                drain()
                return self.send_json({"error": "Cartella sconosciuta"}, 400)
            rel = "/".join([x for x in [f["rel_path"]] if x] + parts[:-1])
            folder = os.path.join(f["path"], rel)
            target = os.path.join(folder, name)
            if os.path.exists(resolve_path(target)):
                drain()
                return self.send_json({"skipped": "nella cartella esiste già un file con questo nome"})
            os.makedirs(folder, exist_ok=True)
            tmp = os.path.join(folder, f".{name}.fotocatalogo-upload")
            with open(tmp, "wb") as out:
                while left > 0:
                    chunk = self.rfile.read(min(left, CHUNK))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
            if left > 0:
                os.remove(tmp)
                return self.send_json({"error": "trasferimento interrotto"}, 400)
            if q.get("mtime"):
                t = int(q["mtime"]) / 1000
                os.utime(tmp, (t, t))
            os.replace(tmp, target)
            return self.send_json({"id": cat.add_file(f["root_id"], rel, target)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            drain()
            self.send_json({"error": str(e)}, 400)

    def do_POST(self):
        u = urlparse(self.path)
        ln = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
        parts = u.path.strip("/").split("/")
        try:
            if parts[:2] == ["api", "catalogs"] and len(parts) == 3:
                res = catalogs_post(parts[2], body)
                return self.send_json(res if res is not None else {"ok": True})
            if parts[:2] == ["api", "pick"]:
                return self.send_json({"path": pick_path(body.get("kind", "folder"))})
            if parts[:2] == ["api", "clientlog"]:          # errori dell'interfaccia -> registro
                print(f"[interfaccia] {str(body.get('msg'))[:2000]} | {str(body.get('ua'))[:300]}", flush=True)
                return self.send_json({"ok": True})
            if cat is None:
                return self.send_json({"error": "Nessun catalogo aperto", "nocatalog": True}, 409)
            res = self.post(parts, body)
            self.send_json(res if res is not None else {"ok": True})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            rollback(cat)
            self.send_json({"error": str(e)}, 400)

    def post(self, parts, body):
        db = cat.db
        route = "/".join(parts[1:])
        if route == "collections":
            name = body.get("name", "").strip()
            if not name:
                raise ValueError("Nome mancante")
            with cat.write_lock:
                cid = db.execute(
                    "INSERT INTO collections(name, parent_id, is_set, created_at) VALUES(?,?,?,?)",
                    (name, body.get("parent") or None, 1 if body.get("set") else 0, now())).lastrowid
                db.commit()
            if body.get("ids"):
                add_to_collection(cid, ids_of(body))
            return {"id": cid}
        if parts[1] == "collections" and len(parts) == 4:
            cid, action = int(parts[2]), parts[3]
            if action == "add":
                return add_to_collection(cid, ids_of(body))
            if action == "remove":
                return remove_from_collection(cid, ids_of(body))
            if action == "delete":
                return delete_collection(cid)
            if action == "update":
                with cat.write_lock:
                    if "name" in body and body["name"].strip():
                        db.execute("UPDATE collections SET name=? WHERE id=?", (body["name"].strip(), cid))
                    if "parent" in body:
                        parent = body["parent"] or None
                        if parent and int(parent) in collection_descendants(cid):
                            raise ValueError("Non puoi spostare un set dentro sé stesso")
                        db.execute("UPDATE collections SET parent_id=? WHERE id=?", (parent, cid))
                    db.commit()
                return None
        if route == "duplicates/find":
            job = start_job("Ricerca duplicati", lambda job: gestione_file.find(cat, job))
            job.cancellable = True
            return job.as_dict()
        if route == "duplicates/apply":
            plan, tdir = body.get("plan", []), THUMB_DIR
            job = start_job("Eliminazione duplicati", lambda job: gestione_file.apply(cat, tdir, plan, job))
            job.cancellable = True
            return job.as_dict()
        if route == "photos/delete":
            ids, tdir = ids_of(body), THUMB_DIR
            job = start_job("Eliminazione dal disco", lambda job: gestione_file.delete(cat, tdir, ids, job))
            job.cancellable = True
            return job.as_dict()
        if route == "photos/remove-missing":
            return remove_missing(ids_of(body) if "ids" in body else None)
        if route == "rotate":
            ids, step = ids_of(body), 90 if int(body.get("dir", 90)) > 0 else 270
            with cat.write_lock:
                db.executemany("""UPDATE photos SET rotation=(COALESCE(rotation,0)+?)%360
                                  WHERE id=? AND COALESCE(ext,'') NOT IN ({})""".format(
                                      ",".join(repr(e) for e in sorted(VIDEO_EXT))),
                               [(step, i) for i in ids])
                db.commit()
            vers = {}
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                for r in db.execute(f"""SELECT id, added_at, mtime, rotation FROM photos
                                        WHERE id IN ({','.join('?' * len(chunk))})""", chunk):
                    vers[r["id"]] = [version(r), r["rotation"] or 0]
            return {"versions": vers}
        if route == "export":
            ids, opts = ids_of(body), body.get("options", {})
            job = start_job("Esportazione JPEG", lambda job: esporta.export(cat, ids, opts, job))
            job.cancellable = True
            return job.as_dict()
        if route == "collsave/preview":
            return salva.summary(cat, int(body["id"]))
        if route == "collsave":
            cid, opts = int(body["id"]), body.get("options", {})
            job = start_job("Salva raccolta", lambda job: salva.save(cat, cid, opts, job))
            job.cancellable = True
            return job.as_dict()
        if route == "collsave/reveal":
            dest = salva.LAST.get("dest")
            if dest and os.path.isdir(dest) and sys.platform == "darwin":
                subprocess.Popen(["open", dest])
            return None
        if route == "paths":
            # percorsi dei file esistenti, per trascinarli fuori dall'app (copia nel Finder)
            out = []
            for pid in ids_of(body)[:20000]:
                path = cat.photo_path(pid)
                if path and os.path.isfile(path):
                    out.append(path)
            return {"paths": out}
        if route == "rename/one":
            pid, name = int(body["id"]), body.get("name")
            _, _, conflicts = gestione_file.rename_plan(cat, [pid], {"name": name})
            if conflicts:
                raise ValueError(f"«{conflicts[0][0]}»: {conflicts[0][1]}")
            job = Job("Rinomina")
            res = gestione_file.rename(cat, [pid], {"name": name}, job)
            if res["errori"]:
                raise ValueError(f"Rinomina non riuscita: {res['errori'][0][1]}")
            if res["saltate"]:
                raise ValueError(f"Rinomina non possibile: {res['saltate'][0][1]}")
            r = cat.db.execute("SELECT filename, ext FROM photos WHERE id=?", (pid,)).fetchone()
            return {"filename": r["filename"], "ext": r["ext"]}
        if route == "export/reveal":
            dest = esporta.LAST.get("dest")
            if dest and os.path.isdir(dest) and sys.platform == "darwin":
                subprocess.Popen(["open", dest])
            return None
        if route == "move":
            ids, fid = ids_of(body), int(body["folder"])
            job = start_job("Spostamento", lambda job: gestione_file.move(cat, ids, fid, job))
            job.cancellable = True
            return job.as_dict()
        if route == "rename/preview":
            plan, skipped, conflicts = gestione_file.rename_plan(cat, ids_of(body), body.get("options", {}))
            return {"total": len(plan), "rows": [[p["old"], p["new"]] for p in plan[:12]],
                    "skipped": skipped[:50], "conflicts": conflicts[:50], "nconflicts": len(conflicts)}
        if route == "rename":
            ids, opts = ids_of(body), body.get("options", {})
            return start_job("Rinomina", lambda job: gestione_file.rename(cat, ids, opts, job)).as_dict()
        if parts[1] == "folders" and len(parts) == 4 and parts[3] in ("create", "rename", "delete"):
            fid = int(parts[2])
            if parts[3] == "create":
                return gestione_file.folder_create(cat, fid, body.get("name"))
            if parts[3] == "rename":
                return gestione_file.folder_rename(cat, fid, body.get("name"))
            return gestione_file.folder_delete(cat, fid)
        if route == "meta":
            return set_meta(body)
        if route == "keywords":
            return edit_keywords(body)
        if route == "keywords/delete":
            kid = int(body["id"])
            with cat.write_lock:
                parent = db.execute("SELECT parent_id FROM keywords WHERE id=?", (kid,)).fetchone()
                # le parole chiave annidate salgono di un livello invece di sparire
                db.execute("UPDATE OR IGNORE keywords SET parent_id=? WHERE parent_id=?",
                           (parent[0] if parent else 0, kid))
                db.execute("DELETE FROM photo_keywords WHERE keyword_id=?", (kid,))
                db.execute("DELETE FROM keywords WHERE id=?", (kid,))
                db.commit()
            return None
        if route == "keywords/rename":
            name = nfc(body.get("name", "").strip())
            if not name:
                raise ValueError("Nome mancante")
            with cat.write_lock:
                try:
                    db.execute("UPDATE keywords SET name=? WHERE id=?", (name, int(body["id"])))
                except Exception:
                    raise ValueError("Esiste già una parola chiave con questo nome nello stesso livello")
                db.commit()
            return None
        if parts[1] == "folders" and len(parts) == 4 and parts[3] == "rescan":
            f = db.execute("SELECT root_id, rel_path FROM folders WHERE id=?", (int(parts[2]),)).fetchone()
            if not f:
                raise ValueError("Cartella sconosciuta")
            label = os.path.basename(f["rel_path"]) or "cartella"
            return start_job(f"Sincronizzazione {label}",
                             lambda job: and_gps(cat.scan_root(f["root_id"], job, f["rel_path"]))).as_dict()
        if route == "open":
            return open_photos(ids_of(body), body.get("how", "default"), body.get("folder"))
        if route == "roots/add":
            path = os.path.expanduser(body.get("path", "").strip())
            if not os.path.isdir(path):
                raise ValueError(f"Cartella non trovata: {path}")
            with cat.write_lock:
                rid = cat.get_or_create_root(path)
                db.commit()
            return start_job(f"Scansione {os.path.basename(path)}",
                             lambda job: and_gps(cat.scan_root(rid, job))).as_dict()
        if parts[1] == "roots" and len(parts) == 4:
            rid, action = int(parts[2]), parts[3]
            if action == "rescan":
                return start_job("Sincronizzazione cartella", lambda job: and_gps(cat.scan_root(rid, job))).as_dict()
            if action == "delete":
                return remove_root(rid)
        if route == "roots/relocate":
            return relocate(body)
        if route == "import/lightroom":
            path = os.path.expanduser(body.get("path", "").strip())
            if not os.path.exists(path):
                raise ValueError(f"File non trovato: {path}")
            return start_job("Importazione Lightroom", lambda job: import_lrcat(cat, path, job)).as_dict()
        if route == "thumbs/want":
            # miniature sullo schermo adesso: passano davanti a tutto
            res = thumbs.PREFETCH.want(cat, THUMB_DIR, ids_of(body)[:400], timeout=8)
            return {"state": {str(k): v for k, v in res.items()}}
        if route == "thumbs/pregenerate":
            return pregenerate(body).as_dict()
        if route in ("convert", "convert/dng"):
            ids, tdir = ids_of(body), THUMB_DIR
            job = start_job("Conversione", lambda job: conversione.convert(cat, ids, tdir, job))
            job.cancellable = True
            return job.as_dict()
        if parts[1] == "jobs" and len(parts) == 4 and parts[3] == "cancel":
            j = JOBS.get(parts[2])
            if j and j.status == "running":
                j.cancelled = True
                j.message = "Interruzione dopo i file in corso…"
            return None
        if route == "jobs/clear":
            for k in [k for k, j in JOBS.items() if j.status != "running"]:
                del JOBS[k]
            return None
        raise ValueError("Comando sconosciuto")


def shutdown(*_):
    """Chiusura (anche quando l'app per Mac termina): blocca il catalogo protetto."""
    try:
        close_current()
    finally:
        os._exit(0)


def startup():
    for m in catalogs.migrate_name():
        print(f"Cartella dei dati rinominata: {m}")
    catalogs.lock_leftovers()
    moved = catalogs.migrate_old()
    if moved:
        print(f"Catalogo spostato in: {moved}")
    path = catalogs.startup_catalog()
    if path is None:
        path = catalogs.create("Catalogo principale")
        s = catalogs.load_settings(); s["default"] = path; catalogs.save_settings(s)
    if not catalogs.is_protected(path):
        open_catalog(path)
    print(f"Cataloghi in: {catalogs.BASE}")
    print(f"Catalogo aperto: {CURRENT.path if CURRENT else '(protetto, in attesa della password)'}")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    signal.signal(signal.SIGTERM, shutdown)
    startup()
    url = f"http://localhost:{PORT}/"
    print(f"Provinia attiva su {url}")
    print("Per chiudere: Ctrl+C (o chiudi questa finestra)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nChiuso.")
        shutdown()


if __name__ == "__main__":
    main()
