"""Importazione da un catalogo Lightroom Classic (.lrcat).

Il file .lrcat è un database SQLite: lo apriamo in sola lettura e copiamo
cartelle, foto, raccolte (con i set), parole chiave, stelle, etichette e
bandierine. Le copie virtuali vengono ricondotte alla foto originale.
Le raccolte smart non vengono importate (le loro regole sono in formato
interno di Lightroom): vengono solo contate nel riepilogo.
"""
import os
import shutil
import sqlite3
import tempfile

from catalog import nfc, now

LABELS = {
    "red": "red", "rosso": "red", "rot": "red", "rouge": "red",
    "yellow": "yellow", "giallo": "yellow", "gelb": "yellow", "jaune": "yellow",
    "green": "green", "verde": "green", "grün": "green", "vert": "green",
    "blue": "blue", "blu": "blue", "blau": "blue", "bleu": "blue",
    "purple": "purple", "viola": "purple", "lila": "purple", "violet": "purple",
}


def _cols(src, table):
    return {r[1] for r in src.execute(f"PRAGMA table_info({table})")}


class LightroomError(Exception):
    pass


def _open_copy(lrcat_path, tmpdir, step):
    """Copia il catalogo (e il suo eventuale file di lavoro -wal) in una cartella
    temporanea e apre la copia. L'originale di Lightroom non viene mai aperto da
    SQLite, quindi non può essere modificato né bloccato."""
    if os.path.isdir(lrcat_path) or not lrcat_path.lower().endswith(".lrcat"):
        raise LightroomError("Scegli il file del catalogo, quello che termina con «.lrcat» "
                             "(non le cartelle «Previews.lrdata» o «.lrcat-data»)")
    copy = os.path.join(tmpdir, "catalogo.lrcat")
    size = os.path.getsize(lrcat_path)
    step(f"Copia temporanea del catalogo Lightroom ({size / 1e6:,.0f} MB)…".replace(",", "."))
    try:
        shutil.copyfile(lrcat_path, copy)
        if os.path.exists(lrcat_path + "-wal"):
            shutil.copyfile(lrcat_path + "-wal", copy + "-wal")
    except PermissionError:
        raise LightroomError(
            "macOS non permette a Provinia di leggere il catalogo. Apri Impostazioni di Sistema › "
            "Privacy e sicurezza › File e cartelle (oppure Accesso completo al disco), attiva "
            "Provinia e riprova.")
    except OSError as e:
        raise LightroomError(f"Impossibile copiare il catalogo: {e.strerror or e}")
    src = sqlite3.connect(copy)
    src.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError:
        src.close()
        raise LightroomError("Il file scelto non è un catalogo Lightroom leggibile")
    if not {"Adobe_images", "AgLibraryFile", "AgLibraryFolder", "AgLibraryRootFolder"} <= tables:
        src.close()
        raise LightroomError("Il file scelto non sembra un catalogo di Lightroom Classic")
    return src


def import_lrcat(cat, lrcat_path, job=None):
    tmpdir = tempfile.mkdtemp(prefix="fotocatalogo_lr_")
    try:
        src = _open_copy(lrcat_path, tmpdir, lambda m: job and job.update(None, m))
        try:
            return _import(cat, src, job)
        finally:
            src.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _import(cat, src, job=None):
    db = cat.db
    stats = {"foto": 0, "raccolte": 0, "set": 0, "raccolte_smart_saltate": 0,
             "parole_chiave": 0, "cartelle": 0}

    def step(msg):
        if job:
            job.update(None, msg)

    with cat.write_lock:
        # 1. Cartelle radice e sottocartelle
        step("Lettura cartelle…")
        root_map = {}
        for r in src.execute("SELECT id_local, absolutePath, name FROM AgLibraryRootFolder"):
            root_map[r["id_local"]] = cat.get_or_create_root(r["absolutePath"].rstrip("/") or "/", r["name"])
        folder_map, fcache = {}, {}
        for r in src.execute("SELECT id_local, rootFolder, pathFromRoot FROM AgLibraryFolder"):
            if r["rootFolder"] in root_map:
                folder_map[r["id_local"]] = cat.ensure_folder(
                    root_map[r["rootFolder"]], r["pathFromRoot"] or "", fcache)
        stats["cartelle"] = len(folder_map)

        # 2. Foto
        step("Lettura foto…")
        icols = _cols(src, "Adobe_images")
        sel = ["i.id_local AS img", "i.rootFile AS file", "f.folder", "f.baseName", "f.extension"]
        for c in ("captureTime", "rating", "colorLabels", "pick", "masterImage",
                  "fileWidth", "fileHeight", "orientation"):
            sel.append(f"i.{c}" if c in icols else f"NULL AS {c}")
        rows = src.execute(f"""SELECT {', '.join(sel)} FROM Adobe_images i
                               JOIN AgLibraryFile f ON f.id_local=i.rootFile""").fetchall()
        file_to_img = {}
        batch = []
        for r in rows:
            fid = folder_map.get(r["folder"])
            if fid is None:
                continue
            name = nfc(r["baseName"] + ("." + r["extension"] if r["extension"] else ""))
            # Le copie virtuali condividono il file: teniamo i dati del master
            if r["masterImage"] is not None and r["file"] in file_to_img:
                continue
            file_to_img[r["file"]] = (fid, name)
            cap = (r["captureTime"] or "")[:19] or None
            label = LABELS.get((r["colorLabels"] or "").strip().lower())
            orient = {"AB": 1, "BC": 6, "CD": 3, "DA": 8}.get(r["orientation"] or "", None)
            batch.append((fid, name, (r["extension"] or "").lower(), cap,
                          r["fileWidth"], r["fileHeight"], orient,
                          int(round(r["rating"] or 0)), label, int(r["pick"] or 0), now()))
        db.executemany(
            """INSERT INTO photos(folder_id, filename, ext, capture_time, width, height,
                 orientation, rating, label, flag, added_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(folder_id, filename) DO UPDATE SET
                 capture_time=COALESCE(excluded.capture_time, capture_time),
                 rating=excluded.rating, label=excluded.label, flag=excluded.flag""", batch)
        stats["foto"] = len(batch)

        step("Collegamento foto…")
        pid_by_key = {(r["folder_id"], r["filename"]): r["id"]
                      for r in db.execute("SELECT id, folder_id, filename FROM photos")}
        img_to_photo = {}
        for r in rows:
            key = file_to_img.get(r["file"])
            if key and key in pid_by_key:
                img_to_photo[r["img"]] = pid_by_key[key]

        # 3. Parole chiave (albero)
        step("Parole chiave…")
        kws = {r["id_local"]: r for r in src.execute(
            "SELECT id_local, name, parent FROM AgLibraryKeyword")}
        kw_map = {}

        def kw_id(lid):
            if lid in kw_map:
                return kw_map[lid]
            r = kws.get(lid)
            if r is None or r["name"] is None:   # radice invisibile di Lightroom
                kw_map[lid] = 0
                return 0
            parent = kw_id(r["parent"]) if r["parent"] is not None else 0
            name = nfc(r["name"])
            db.execute("INSERT OR IGNORE INTO keywords(name, parent_id) VALUES(?,?)", (name, parent))
            kw_map[lid] = db.execute("SELECT id FROM keywords WHERE name=? AND parent_id=?",
                                     (name, parent)).fetchone()["id"]
            return kw_map[lid]

        for lid in kws:
            kw_id(lid)
        stats["parole_chiave"] = sum(1 for v in kw_map.values() if v)
        db.executemany("INSERT OR IGNORE INTO photo_keywords(photo_id, keyword_id) VALUES(?,?)",
                       ((img_to_photo[r["image"]], kw_map[r["tag"]])
                        for r in src.execute("SELECT image, tag FROM AgLibraryKeywordImage")
                        if r["image"] in img_to_photo and kw_map.get(r["tag"])))

        # 4. Raccolte e set di raccolte
        step("Raccolte…")
        colls = {r["id_local"]: r for r in src.execute(
            "SELECT id_local, name, parent, creationId FROM AgLibraryCollection")}
        coll_map = {}

        def coll_id(lid):
            if lid in coll_map:
                return coll_map[lid]
            r = colls.get(lid)
            if r is None:
                return None
            kind = r["creationId"] or ""
            if "smart" in kind:
                stats["raccolte_smart_saltate"] += 1
                coll_map[lid] = None
                return None
            is_set = 1 if kind == "com.adobe.ag.library.group" else 0
            parent = coll_id(r["parent"]) if r["parent"] is not None else None
            name = nfc(r["name"] or "Senza nome")
            ex = db.execute("""SELECT id FROM collections WHERE name=? AND parent_id IS ?
                               AND is_set=?""", (name, parent, is_set)).fetchone()
            if ex:
                coll_map[lid] = ex["id"]
            else:
                coll_map[lid] = db.execute(
                    "INSERT INTO collections(name, parent_id, is_set, created_at) VALUES(?,?,?,?)",
                    (name, parent, is_set, now())).lastrowid
            stats["set" if is_set else "raccolte"] += 1
            return coll_map[lid]

        for lid in colls:
            coll_id(lid)
        ts = now()
        db.executemany(
            "INSERT OR IGNORE INTO collection_photos(collection_id, photo_id, added_at) VALUES(?,?,?)",
            ((coll_map[r["collection"]], img_to_photo[r["image"]], ts)
             for r in src.execute("SELECT collection, image FROM AgLibraryCollectionImage")
             if coll_map.get(r["collection"]) and r["image"] in img_to_photo))
        db.commit()
    return stats
