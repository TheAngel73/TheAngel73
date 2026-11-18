"""Database del catalogo (SQLite) e scansione delle cartelle.

Il catalogo NON modifica mai i file delle foto: legge soltanto.
"""
import os
import sqlite3
import threading
import time
import unicodedata

from exif import read_exif

PHOTO_EXT = {
    "jpg", "jpeg", "jpe", "png", "tif", "tiff", "psd", "psb", "heic", "heif",
    "gif", "bmp", "webp", "avif", "jxl", "jp2",
    # RAW
    "nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "raf", "orf",
    "rw2", "pef", "dng", "srw", "x3f", "3fr", "iiq", "erf", "mos", "mrw",
    "raw", "rwl", "kdc", "dcr", "mef",
}
# Cartelle di sistema dei NAS da ignorare (Synology, QNAP, macOS)
SKIP_DIRS = {"@eaDir", "#recycle", "@Recycle", ".@__thumb", "#snapshot",
             ".Trashes", ".Spotlight-V100", ".fseventsd", "@Recently-Snapshot"}
EXIF_EXT = {"jpg", "jpeg", "jpe", "tif", "tiff", "nef", "nrw", "cr2", "arw",
            "srf", "sr2", "dng", "orf", "rw2", "pef", "srw", "erf", "3fr",
            "iiq", "mos", "kdc", "dcr", "mef", "rwl"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS roots(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT);
CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(id),
  rel_path TEXT NOT NULL, parent_id INTEGER, UNIQUE(root_id, rel_path));
CREATE TABLE IF NOT EXISTS photos(
  id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id),
  filename TEXT NOT NULL, ext TEXT, size INTEGER, mtime REAL,
  capture_time TEXT, width INTEGER, height INTEGER, orientation INTEGER,
  rating INTEGER DEFAULT 0, label TEXT, flag INTEGER DEFAULT 0,
  missing INTEGER DEFAULT 0, added_at TEXT,
  UNIQUE(folder_id, filename));
CREATE INDEX IF NOT EXISTS ix_photos_capture ON photos(capture_time);
CREATE INDEX IF NOT EXISTS ix_photos_folder ON photos(folder_id);
CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER,
  is_set INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS collection_photos(
  collection_id INTEGER NOT NULL, photo_id INTEGER NOT NULL,
  added_at TEXT, PRIMARY KEY(collection_id, photo_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_cp_photo ON collection_photos(photo_id);
CREATE TABLE IF NOT EXISTS keywords(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER NOT NULL DEFAULT 0,
  UNIQUE(name, parent_id));
CREATE TABLE IF NOT EXISTS photo_keywords(
  photo_id INTEGER NOT NULL, keyword_id INTEGER NOT NULL,
  PRIMARY KEY(photo_id, keyword_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_pk_kw ON photo_keywords(keyword_id);
"""


def nfc(s):
    return unicodedata.normalize("NFC", s) if s else s


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Catalog:
    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.write_lock:
            self.db.executescript(SCHEMA)

    @property
    def db(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=60)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=OFF")
            self._local.conn = c
        return c

    # ---------- percorsi ----------
    def root_path(self, root_id):
        r = self.db.execute("SELECT path FROM roots WHERE id=?", (root_id,)).fetchone()
        return r["path"] if r else None

    def photo_path(self, photo_id):
        r = self.db.execute(
            """SELECT r.path, f.rel_path, p.filename FROM photos p
               JOIN folders f ON f.id=p.folder_id JOIN roots r ON r.id=f.root_id
               WHERE p.id=?""", (photo_id,)).fetchone()
        if not r:
            return None
        return resolve_path(os.path.join(r["path"], r["rel_path"], r["filename"]))

    # ---------- radici e cartelle ----------
    def get_or_create_root(self, path, name=None):
        path = nfc(os.path.normpath(path))
        r = self.db.execute("SELECT id FROM roots WHERE path=?", (path,)).fetchone()
        if r:
            return r["id"]
        cur = self.db.execute("INSERT INTO roots(path, name) VALUES(?,?)",
                              (path, name or os.path.basename(path) or path))
        rid = cur.lastrowid
        self.ensure_folder(rid, "")
        return rid

    def ensure_folder(self, root_id, rel_path, cache=None):
        rel_path = nfc(rel_path.strip("/"))
        key = (root_id, rel_path)
        if cache is not None and key in cache:
            return cache[key]
        r = self.db.execute("SELECT id FROM folders WHERE root_id=? AND rel_path=?",
                            key).fetchone()
        if r:
            fid = r["id"]
        else:
            parent = None
            if rel_path:
                parent = self.ensure_folder(root_id, os.path.dirname(rel_path), cache)
            fid = self.db.execute(
                "INSERT INTO folders(root_id, rel_path, parent_id) VALUES(?,?,?)",
                (root_id, rel_path, parent)).lastrowid
        if cache is not None:
            cache[key] = fid
        return fid

    # ---------- scansione ----------
    def scan_root(self, root_id, job=None):
        root = self.root_path(root_id)
        if not root or not os.path.isdir(root):
            raise RuntimeError(f"Cartella non raggiungibile (NAS collegato?): {root}")
        db = self.db
        known = {}
        for r in db.execute(
                """SELECT p.id, p.filename, p.size, p.mtime, p.capture_time, f.rel_path
                   FROM photos p JOIN folders f ON f.id=p.folder_id WHERE f.root_id=?""",
                (root_id,)):
            known[(r["rel_path"], r["filename"])] = r
        seen = set()
        new_rows, updates = [], []
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d not in SKIP_DIRS)
            rel = nfc(os.path.relpath(dirpath, root))
            rel = "" if rel == "." else rel
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                if ext not in PHOTO_EXT:
                    continue
                name = nfc(fn)
                key = (rel, name)
                seen.add(key)
                count += 1
                if job and count % 200 == 0:
                    job.update(count, f"Scansione: {rel or '/'} ({count} foto)")
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                old = known.get(key)
                if old is None:
                    new_rows.append((rel, name, ext, st.st_size, st.st_mtime, full))
                elif old["size"] != st.st_size or old["mtime"] != st.st_mtime:
                    updates.append((st.st_size, st.st_mtime, old["id"], old["capture_time"], full, ext))
        # inserimento nuove foto (con lettura EXIF)
        with self.write_lock:
            cache = {}
            for i, (rel, name, ext, size, mtime, full) in enumerate(new_rows):
                if job and i % 100 == 0:
                    job.update(count, f"Aggiunta nuove foto: {i}/{len(new_rows)}")
                ex = read_exif(full) if ext in EXIF_EXT else {}
                cap = ex.get("capture") or time.strftime("%Y-%m-%dT%H:%M:%S",
                                                         time.localtime(mtime))
                fid = self.ensure_folder(root_id, rel, cache)
                db.execute(
                    """INSERT OR IGNORE INTO photos(folder_id, filename, ext, size, mtime,
                       capture_time, width, height, orientation, added_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (fid, name, ext, size, mtime, cap, ex.get("width"), ex.get("height"),
                     ex.get("orientation"), now()))
            for size, mtime, pid, cap, full, ext in updates:
                if not cap and ext in EXIF_EXT:
                    cap = read_exif(full).get("capture")
                db.execute("UPDATE photos SET size=?, mtime=?, missing=0, capture_time=? WHERE id=?",
                           (size, mtime, cap, pid))
            missing = [r["id"] for k, r in known.items() if k not in seen]
            found_again = [r["id"] for k, r in known.items() if k in seen]
            for ids, val in ((missing, 1), (found_again, 0)):
                for i in range(0, len(ids), 5000):
                    chunk = ",".join(map(str, ids[i:i + 5000]))
                    db.execute(f"UPDATE photos SET missing=? WHERE missing<>? AND id IN ({chunk})",
                               (val, val))
            db.commit()
        return {"total": count, "new": len(new_rows), "missing": len(missing)}


def resolve_path(path):
    """Prova il percorso così com'è e, se non esiste, nella forma Unicode NFD
    (alcuni NAS salvano i nomi accentati in forma decomposta)."""
    if os.path.exists(path):
        return path
    alt = unicodedata.normalize("NFD", path)
    if os.path.exists(alt):
        return alt
    return path
