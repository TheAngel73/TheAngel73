#!/usr/bin/env python3
"""FotoCatalogo: catalogo foto locale con cartelle, raccolte e parole chiave.

Avvio:  python3 server.py   (poi apri http://localhost:8765)
Solo libreria standard di Python: non serve installare nulla.
"""
import json
import os
import signal
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

from catalog import nfc, now  # noqa: E402
from lightroom import import_lrcat  # noqa: E402
import catalogs  # noqa: E402
import thumbs  # noqa: E402

PORT = int(os.environ.get("FOTOCAT_PORT", "8765"))

# Catalogo aperto in questo momento (None = nessuno, es. in attesa della password)
CURRENT = None
cat = None
THUMB_DIR = None
switch_lock = threading.Lock()
STATIC = os.path.join(HERE, "static")
LABELS = ("red", "yellow", "green", "blue", "purple")


# ---------------------------------------------------------------- lavori in background
class Job:
    def __init__(self, label):
        self.id = uuid.uuid4().hex[:8]
        self.label, self.message = label, "In attesa…"
        self.done, self.total = 0, None
        self.status = "running"
        self.result = None
        self.started = time.time()

    def update(self, done=None, message=None, total=None):
        if done is not None:
            self.done = done
        if message is not None:
            self.message = message
        if total is not None:
            self.total = total

    def as_dict(self):
        return {k: getattr(self, k) for k in
                ("id", "label", "message", "done", "total", "status", "result")}


JOBS = {}


# ---------------------------------------------------------------- cataloghi
def jobs_running():
    return any(j.status == "running" for j in JOBS.values())


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
        if jobs_running():
            raise ValueError("Attendi la fine dei lavori in corso prima di cambiare catalogo")
        if CURRENT and CURRENT.path == path:
            return CURRENT.info()
        oc = catalogs.OpenCatalog(path, password)   # se la password è errata, resta aperto il precedente
        close_current()
        CURRENT, cat, THUMB_DIR = oc, oc.cat, oc.thumb_dir
        JOBS.clear()
        catalogs.remember(path)
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


def start_job(label, fn):
    job = Job(label)
    JOBS[job.id] = job

    def run():
        try:
            job.result = fn(job)
            job.status = "done"
            job.message = "Completato"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job.status = "error"
            job.message = str(e)
    threading.Thread(target=run, daemon=True).start()
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
    if kind == "nocoll":
        return "NOT EXISTS (SELECT 1 FROM collection_photos cp WHERE cp.photo_id=p.id)", []
    if kind == "missing":
        return "p.missing=1", []
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
            where.append("""(p.filename LIKE ? OR p.id IN (SELECT pk.photo_id FROM photo_keywords pk
                            JOIN keywords k ON k.id=pk.keyword_id WHERE k.name LIKE ?)
                            OR p.folder_id IN (SELECT id FROM folders WHERE rel_path LIKE ?))""")
            args += [like, like, like]
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
        f"""SELECT p.id, p.filename, p.ext, p.capture_time, p.rating, p.label, p.flag, p.missing
            FROM photos p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?""",
        args + [limit, offset]).fetchall()
    return {"total": total, "offset": offset, "items": [
        {"id": r["id"], "n": r["filename"], "x": r["ext"], "t": r["capture_time"],
         "r": r["rating"], "l": r["label"], "f": r["flag"], "m": r["missing"]} for r in rows]}


def photo_detail(pid):
    db = cat.db
    p = db.execute("""SELECT p.*, f.rel_path, r.path AS root, r.id AS root_id FROM photos p
                      JOIN folders f ON f.id=p.folder_id JOIN roots r ON r.id=f.root_id
                      WHERE p.id=?""", (pid,)).fetchone()
    if not p:
        return None
    d = dict(p)
    d["path"] = os.path.join(p["root"], p["rel_path"], p["filename"])
    d["collections"] = [dict(r) for r in db.execute(
        """SELECT c.id, c.name, c.parent_id FROM collection_photos cp
           JOIN collections c ON c.id=cp.collection_id WHERE cp.photo_id=? ORDER BY c.name""", (pid,))]
    d["keywords"] = [dict(r) for r in db.execute(
        """SELECT k.id, k.name FROM photo_keywords pk JOIN keywords k ON k.id=pk.keyword_id
           WHERE pk.photo_id=? ORDER BY k.name""", (pid,))]
    return d


def tree():
    db = cat.db
    fcount = dict(db.execute("SELECT folder_id, count(*) FROM photos GROUP BY folder_id").fetchall())
    ccount = dict(db.execute(
        "SELECT collection_id, count(*) FROM collection_photos GROUP BY collection_id").fetchall())
    kcount = dict(db.execute(
        "SELECT keyword_id, count(*) FROM photo_keywords GROUP BY keyword_id").fetchall())
    roots = [{"id": r["id"], "path": r["path"], "name": r["name"],
              "online": os.path.isdir(r["path"])} for r in db.execute("SELECT * FROM roots ORDER BY name")]
    folders = [{"id": r["id"], "root": r["root_id"], "parent": r["parent_id"], "rel": r["rel_path"],
                "name": os.path.basename(r["rel_path"]) if r["rel_path"] else None,
                "count": fcount.get(r["id"], 0)}
               for r in db.execute("SELECT * FROM folders ORDER BY rel_path COLLATE NOCASE")]
    colls = [{"id": r["id"], "name": r["name"], "parent": r["parent_id"], "set": r["is_set"],
              "count": ccount.get(r["id"], 0)}
             for r in db.execute("SELECT * FROM collections ORDER BY is_set DESC, name COLLATE NOCASE")]
    kws = [{"id": r["id"], "name": r["name"], "parent": r["parent_id"], "count": kcount.get(r["id"], 0)}
           for r in db.execute("SELECT * FROM keywords ORDER BY name COLLATE NOCASE")]
    stats = db.execute("""SELECT count(*) AS total, sum(missing) AS missing,
        (SELECT count(*) FROM photos p WHERE NOT EXISTS
           (SELECT 1 FROM collection_photos cp WHERE cp.photo_id=p.id)) AS nocoll
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
            thumbs.get_thumb(cat, THUMB_DIR, pid, "m")
            with lock:
                done[0] += 1
                if done[0] % 20 == 0 or done[0] == len(todo):
                    job.update(done[0], f"Anteprime: {done[0]} / {len(todo)}")
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(one, todo))
        return {"create": len(todo)}
    return start_job("Generazione anteprime", run)


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
                ctype = {"js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}.get(
                    p.rsplit(".", 1)[-1], "application/octet-stream")
                return self.send_file(p, ctype + "; charset=utf-8", False) if os.path.isfile(p) else self.not_found()
            if u.path == "/api/catalogs":
                return self.send_json(catalogs_state())
            if cat is None:
                return self.send_json({"error": "Nessun catalogo aperto", "nocatalog": True}, 409)
            if parts[0] in ("thumb", "preview") and len(parts) == 2:
                c, tdir, private = cat, THUMB_DIR, CURRENT.protected
                path = thumbs.get_thumb(c, tdir, int(parts[1]), "m" if parts[0] == "thumb" else "l")
                return self.send_file(path, "image/jpeg", private=private) if path else self.not_found()
            if u.path == "/api/tree":
                return self.send_json(tree())
            if u.path == "/api/photos":
                return self.send_json(list_photos(q))
            if parts[:2] == ["api", "photo"] and len(parts) == 3:
                d = photo_detail(int(parts[2]))
                return self.send_json(d) if d else self.not_found()
            if u.path == "/api/jobs":
                return self.send_json([j.as_dict() for j in JOBS.values()])
            if u.path == "/api/years":
                return self.send_json([r[0] for r in cat.db.execute(
                    "SELECT DISTINCT substr(capture_time,1,4) y FROM photos WHERE y IS NOT NULL ORDER BY y DESC")])
            return self.not_found()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

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
            if cat is None:
                return self.send_json({"error": "Nessun catalogo aperto", "nocatalog": True}, 409)
            res = self.post(parts, body)
            self.send_json(res if res is not None else {"ok": True})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
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
                             lambda job: cat.scan_root(f["root_id"], job, f["rel_path"])).as_dict()
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
                             lambda job: cat.scan_root(rid, job)).as_dict()
        if parts[1] == "roots" and len(parts) == 4:
            rid, action = int(parts[2]), parts[3]
            if action == "rescan":
                return start_job("Sincronizzazione cartella", lambda job: cat.scan_root(rid, job)).as_dict()
            if action == "delete":
                return remove_root(rid)
        if route == "roots/relocate":
            return relocate(body)
        if route == "import/lightroom":
            path = os.path.expanduser(body.get("path", "").strip())
            if not os.path.isfile(path):
                raise ValueError(f"File non trovato: {path}")
            return start_job("Importazione Lightroom", lambda job: import_lrcat(cat, path, job)).as_dict()
        if route == "thumbs/pregenerate":
            return pregenerate(body).as_dict()
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
    print(f"FotoCatalogo attivo su {url}")
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
