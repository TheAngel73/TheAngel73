"""Database del catalogo (SQLite) e scansione delle cartelle.

Il catalogo NON modifica mai i file delle foto: legge soltanto.
"""
import os
import re
import sqlite3
import threading
import time
import unicodedata

from exif import read_exif, read_video

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
VIDEO_EXT = {"mov", "mp4", "m4v", "avi", "mkv", "mts", "m2ts", "3gp", "mpg", "mpeg", "wmv", "webm"}
QT_EXT = {"mov", "mp4", "m4v", "3gp"}      # formati con data leggibile (QuickTime/MPEG-4)
PHOTO_EXT |= VIDEO_EXT
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
CREATE TABLE IF NOT EXISTS dir_state(
  root_id INTEGER NOT NULL, rel_path TEXT NOT NULL, mtime REAL,
  PRIMARY KEY(root_id, rel_path)) WITHOUT ROWID;
"""


# Nomi assegnati dalle fotocamere/telefoni (da rinominare): _DSC0001, DSC_0001, IMG_1234,
# _MG_1234, DSCF1234, DSCN1234, P1010001, DJI_0001, GOPR0001, GH010001, PXL_20230101_123456…
CAMERA_NAME = re.compile(
    r"^(_DSC|DSC_|_DSCF|DSCF|DSCN|DSC|_MG_|_IMG_|IMG_|IMG|DJI_|GOPR|GH|GX|MVI_|PXL_|CIMG|SAM_|IMAG|VID_|VID|MOV_|C|P)"
    r"\d+([_-]\d+)*( ?\(?\d+\)?)?$", re.IGNORECASE)


def is_camera_name(filename):
    base = os.path.splitext(filename or "")[0]
    return 1 if CAMERA_NAME.match(base) else 0


def nfc(s):
    return unicodedata.normalize("NFC", s) if s else s


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Catalog:
    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._conns = []
        self.closed = False
        self.write_lock = threading.RLock()
        with self.write_lock:
            self.db.executescript(SCHEMA)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(photos)")}
            if "rotation" not in cols:      # cataloghi creati con versioni precedenti
                self.db.execute("ALTER TABLE photos ADD COLUMN rotation INTEGER DEFAULT 0")
                self.db.commit()
            if "gps_read" not in cols:
                # posizione GPS; gps_read=1 quando il file è già stato letto (anche se senza posizione)
                self.db.execute("ALTER TABLE photos ADD COLUMN gps_lat REAL")
                self.db.execute("ALTER TABLE photos ADD COLUMN gps_lon REAL")
                self.db.execute("ALTER TABLE photos ADD COLUMN gps_read INTEGER DEFAULT 0")
                self.db.execute("CREATE INDEX IF NOT EXISTS ix_photos_gps ON photos(gps_lat, gps_lon)")
                self.db.commit()
            if "place" not in cols:         # nome del luogo dalle coordinate (luoghi.py)
                self.db.execute("ALTER TABLE photos ADD COLUMN place TEXT")
                self.db.commit()
            self.db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
            # nome senza estensione per cartella: trova subito il gemello JPG/RAW (server.pair_query).
            # L'espressione deve restare identica a quella usata nelle query.
            self.db.execute("CREATE INDEX IF NOT EXISTS ix_photos_stem ON photos(folder_id, "
                            "lower(substr(filename, 1, length(filename) - length(ext) - 1)))")
            self.db.commit()

    @property
    def db(self):
        if self.closed:
            raise RuntimeError("Il catalogo è stato chiuso")
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=60, check_same_thread=False)
            self._conns.append(c)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=OFF")
            c.create_function("fc_camname", 1, is_camera_name, deterministic=True)
            self._local.conn = c
        return c

    def close(self):
        """Chiude tutte le connessioni (necessario prima di bloccare un catalogo protetto)."""
        with self.write_lock:
            self.closed = True
            for c in self._conns:
                try:
                    c.rollback()                          # niente modifiche lasciate a metà
                    c.execute("PRAGMA busy_timeout=2000")  # la chiusura non aspetta all'infinito
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                try:
                    c.close()
                except Exception:
                    pass
            self._conns.clear()

    # ---------- percorsi ----------
    def root_path(self, root_id):
        r = self.db.execute("SELECT path FROM roots WHERE id=?", (root_id,)).fetchone()
        return r["path"] if r else None

    def rotation(self, photo_id):
        r = self.db.execute("SELECT rotation FROM photos WHERE id=?", (photo_id,)).fetchone()
        return (r[0] or 0) if r else 0

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
    def scan_root(self, root_id, job=None, sub=""):
        """Sincronizza una cartella radice, oppure solo la sottocartella `sub`."""
        root = self.root_path(root_id)
        start = resolve_path(os.path.join(root, sub)) if sub else root
        if not root or not os.path.isdir(start):
            raise RuntimeError(f"Cartella non raggiungibile (NAS collegato?): {start}")
        db = self.db
        known = {}
        for r in db.execute(
                """SELECT p.id, p.filename, p.size, p.mtime, p.capture_time, f.rel_path
                   FROM photos p JOIN folders f ON f.id=p.folder_id WHERE f.root_id=?""",
                (root_id,)):
            if sub and r["rel_path"] != sub and not r["rel_path"].startswith(sub + "/"):
                continue
            known[(r["rel_path"], r["filename"])] = r
        seen = set()
        new_rows, updates = [], []
        count = 0
        dir_mtimes = {}
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d not in SKIP_DIRS)
            rel = nfc(os.path.relpath(dirpath, root))
            rel = "" if rel == "." else rel
            try:
                dir_mtimes[rel] = os.stat(dirpath).st_mtime
            except OSError:
                pass
            for fn in filenames:
                if fn.startswith("."):
                    continue
                name = nfc(fn)
                key = (rel, name)
                # "presente" vale per qualsiasi file: una voce del catalogo con un'estensione
                # non riconosciuta (es. importata da Lightroom) non deve risultare mancante
                seen.add(key)
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                if ext not in PHOTO_EXT:
                    continue
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
                ex = read_meta(full, ext)
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
                if not cap:
                    cap = read_meta(full, ext).get("capture")
                db.execute("UPDATE photos SET size=?, mtime=?, missing=0, capture_time=? WHERE id=?",
                           (size, mtime, cap, pid))
            missing = [r["id"] for k, r in known.items() if k not in seen]
            found_again = [r["id"] for k, r in known.items() if k in seen]
            for ids, val in ((missing, 1), (found_again, 0)):
                for i in range(0, len(ids), 5000):
                    chunk = ",".join(map(str, ids[i:i + 5000]))
                    db.execute(f"UPDATE photos SET missing=? WHERE missing<>? AND id IN ({chunk})",
                               (val, val))
            self._save_dir_state(root_id, sub, dir_mtimes)
            db.commit()
        return {"total": count, "new": len(new_rows), "missing": len(missing)}

    def _save_dir_state(self, root_id, sub, dir_mtimes):
        """Ricorda la data di modifica delle cartelle, per la sincronizzazione rapida."""
        if sub:
            self.db.execute("""DELETE FROM dir_state WHERE root_id=? AND
                               (rel_path=? OR substr(rel_path,1,?)=?)""",
                            (root_id, sub, len(sub) + 1, sub + "/"))
        else:
            self.db.execute("DELETE FROM dir_state WHERE root_id=?", (root_id,))
        self.db.executemany("INSERT OR REPLACE INTO dir_state VALUES(?,?,?)",
                            [(root_id, rel, mt) for rel, mt in dir_mtimes.items()])

    # ---------- sincronizzazione rapida ----------
    def quick_sync(self, job=None):
        """Controlla solo la data di modifica delle cartelle (poche migliaia, non
        centinaia di migliaia di file) e rilegge soltanto quelle cambiate.
        La prima volta, non avendo date di riferimento, controlla tutto."""
        db = self.db
        res = {"quick": True, "new": 0, "missing": 0, "cartelle": 0}
        for r in db.execute("SELECT id, path FROM roots").fetchall():
            root_id, root = r["id"], r["path"]
            if not os.path.isdir(root):
                continue          # NAS non collegato: non tocchiamo nulla
            res["ritrovate"] = res.get("ritrovate", 0) + self._recheck_missing(root_id, root)
            known = {row["rel_path"]: row["mtime"] for row in db.execute(
                "SELECT rel_path, mtime FROM dir_state WHERE root_id=?", (root_id,))}
            seen = set()
            for dirpath, dirnames, filenames in os.walk(root):
                if job and job.cancelled:
                    return res
                dirnames[:] = sorted(d for d in dirnames
                                     if not d.startswith(".") and d not in SKIP_DIRS)
                rel = nfc(os.path.relpath(dirpath, root))
                rel = "" if rel == "." else rel
                seen.add(rel)
                if job and len(seen) % 50 == 0:
                    job.update(len(seen), f"Controllo cartelle: {len(seen)}")
                try:
                    mt = os.stat(dirpath).st_mtime
                except OSError:
                    continue
                if known.get(rel) == mt:
                    continue
                n_new, n_miss = self._sync_dir(root_id, rel, dirpath, filenames)
                res["new"] += n_new
                res["missing"] += n_miss
                res["cartelle"] += 1
                with self.write_lock:
                    db.execute("INSERT OR REPLACE INTO dir_state VALUES(?,?,?)", (root_id, rel, mt))
                    db.commit()
            # cartelle sparite (rinominate o cancellate): le loro foto diventano "mancanti"
            gone = [rel for rel in known if rel not in seen]
            if gone:
                with self.write_lock:
                    for rel in gone:
                        cur = db.execute("""UPDATE photos SET missing=1 WHERE missing=0 AND folder_id IN
                                            (SELECT id FROM folders WHERE root_id=? AND rel_path=?)""",
                                         (root_id, rel))
                        res["missing"] += cur.rowcount
                        db.execute("DELETE FROM dir_state WHERE root_id=? AND rel_path=?", (root_id, rel))
                    db.commit()
        return res

    def _recheck_missing(self, root_id, root):
        """Ricontrolla una per una le foto segnate come mancanti: se il file c'è, torna normale."""
        rows = self.db.execute("""SELECT p.id, f.rel_path, p.filename FROM photos p
                                  JOIN folders f ON f.id=p.folder_id
                                  WHERE f.root_id=? AND p.missing=1""", (root_id,)).fetchall()
        back = [r["id"] for r in rows
                if os.path.exists(resolve_path(os.path.join(root, r["rel_path"], r["filename"])))]
        if back:
            with self.write_lock:
                self.db.executemany("UPDATE photos SET missing=0 WHERE id=?", [(i,) for i in back])
                self.db.commit()
        return len(back)

    def add_file(self, root_id, rel, full):
        """Aggiunge al catalogo un file appena copiato o creato (es. importazione)."""
        name = nfc(os.path.basename(full))
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        st = os.stat(full)
        ex = read_meta(full, ext)
        cap = ex.get("capture") or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
        with self.write_lock:
            fid = self.ensure_folder(root_id, rel)
            cur = self.db.execute(
                """INSERT OR IGNORE INTO photos(folder_id, filename, ext, size, mtime, capture_time,
                   width, height, orientation, added_at, gps_lat, gps_lon, gps_read)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, name, ext, st.st_size, st.st_mtime, cap, ex.get("width"), ex.get("height"),
                 ex.get("orientation"), now(), ex.get("lat"), ex.get("lon"), gps_done(ext, ex)))
            self.db.commit()
            return cur.lastrowid if cur.rowcount else None

    def _sync_dir(self, root_id, rel, dirpath, filenames):
        """Allinea il catalogo con il contenuto di una sola cartella (senza sottocartelle)."""
        if any(fn.startswith((".fctmp-", ".fctmpx-")) for fn in filenames):
            from gestione_file import recover_renames
            filenames = recover_renames(self, root_id, rel, dirpath, filenames)
        db = self.db
        files = {}
        present = set()          # tutti i file della cartella, qualunque estensione
        for fn in filenames:
            if fn.startswith("."):
                continue
            present.add(nfc(fn))
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in PHOTO_EXT:
                files[nfc(fn)] = (fn, ext)
        f = db.execute("SELECT id FROM folders WHERE root_id=? AND rel_path=?", (root_id, rel)).fetchone()
        existing = {}
        if f:
            existing = {row["filename"]: (row["id"], row["missing"]) for row in db.execute(
                "SELECT id, filename, missing FROM photos WHERE folder_id=?", (f["id"],))}
        new, back, gone = [], [], []
        for name, (fn, ext) in files.items():
            if name not in existing:
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                ex = read_meta(full, ext)
                cap = ex.get("capture") or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
                new.append((name, ext, st.st_size, st.st_mtime, cap, ex))
        for name, (pid, miss) in existing.items():
            if name in present and miss:
                back.append(pid)
            elif name not in present and not miss:
                gone.append(pid)
        if not (new or back or gone):
            return 0, 0
        with self.write_lock:
            fid = f["id"] if f else self.ensure_folder(root_id, rel)
            ts = now()
            db.executemany(
                """INSERT OR IGNORE INTO photos(folder_id, filename, ext, size, mtime, capture_time,
                   width, height, orientation, added_at, gps_lat, gps_lon, gps_read)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(fid, name, ext, size, mt, cap, ex.get("width"), ex.get("height"),
                  ex.get("orientation"), ts, ex.get("lat"), ex.get("lon"), gps_done(ext, ex))
                 for name, ext, size, mt, cap, ex in new])
            db.executemany("UPDATE photos SET missing=0 WHERE id=?", [(i,) for i in back])
            db.executemany("UPDATE photos SET missing=1 WHERE id=?", [(i,) for i in gone])
            db.commit()
        return len(new), len(gone)


def read_meta(full, ext):
    if ext in EXIF_EXT:
        return read_exif(full)
    if ext in QT_EXT:
        return read_video(full)
    return {}


def gps_done(ext, ex):
    """1 se la posizione GPS è già stata cercata durante la lettura dei metadati;
    0 se resta da fare in background (altri formati, o posizione più avanti nel file)."""
    if "lat" in ex:
        return 1
    return 1 if (ext in EXIF_EXT or ext in QT_EXT) and not ex.get("gps_beyond") else 0


def resolve_path(path):
    """Prova il percorso così com'è e, se non esiste, nella forma Unicode NFD
    (alcuni NAS salvano i nomi accentati in forma decomposta)."""
    if os.path.exists(path):
        return path
    alt = unicodedata.normalize("NFD", path)
    if os.path.exists(alt):
        return alt
    return path
