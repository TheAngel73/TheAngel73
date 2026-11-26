"""Importazione da un catalogo Lightroom Classic (.lrcat) ed esportazione verso un catalogo.

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
import time
import uuid

from catalog import nfc, now

LABELS = {
    "red": "red", "rosso": "red", "rossa": "red", "gialla": "yellow", "rot": "red", "rouge": "red",
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
            "SELECT id_local, name, parent, creationId FROM AgLibraryCollection "
            "WHERE NOT (systemOnly = 1 OR systemOnly = '1')")}       # niente «quick collection»
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


# ======================================================================== esportazione
# Provinia non crea un catalogo Lightroom da zero: lo schema interno (oltre 130 tabelle,
# impostazioni di sviluppo che dipendono dalla fotocamera) non è documentato. Le foto le
# importa Lightroom stesso («Aggiungi», senza copiarle); poi questa funzione riporta nel
# suo catalogo quello che sta solo in Provinia: stelle, etichette, bandierine, rotazione,
# parole chiave, raccolte e set. Si toccano solo righe che Lightroom sa già gestire.
# Prima di scrivere si fa una copia del catalogo accanto all'originale.

# etichette di Lightroom in italiano (il catalogo salva il nome visibile, non il colore)
LR_LABELS = {"red": "Rossa", "yellow": "Gialla", "green": "Verde", "blue": "Blu", "purple": "Viola"}
# orientamento di Lightroom: ogni passo è una rotazione di 90° in senso orario
LR_ORIENT = ["AB", "BC", "CD", "DA"]
EXIF_TO_STEP = {1: 0, 6: 1, 3: 2, 8: 3}
COCOA_EPOCH = 978307200          # Lightroom conta i secondi dal 1° gennaio 2001


def _genealogy(parent_gen, lid):
    s = str(lid)
    return f"{parent_gen or ''}/{len(s)}{s}"


def _key(path):
    return nfc(path).lower()


def export_lrcat(cat, lrcat_path, job=None):
    def step(msg):
        if job:
            job.update(None, msg)

    if os.path.isdir(lrcat_path) or not lrcat_path.lower().endswith(".lrcat"):
        raise LightroomError("Scegli il file del catalogo, quello che termina con «.lrcat»")
    if os.path.exists(lrcat_path + ".lock"):
        raise LightroomError("Il catalogo è aperto in Lightroom: chiudi Lightroom e riprova")

    # copia di sicurezza accanto all'originale
    base = lrcat_path[:-6]
    stamp = time.strftime("%Y-%m-%d %H.%M.%S")
    backup = f"{base} (prima di Provinia {stamp}).lrcat"
    n = 2
    while os.path.exists(backup):              # mai sovrascrivere una copia precedente
        backup = f"{base} (prima di Provinia {stamp} {n}).lrcat"
        n += 1
    step("Copia di sicurezza del catalogo Lightroom…")
    try:
        shutil.copyfile(lrcat_path, backup)
        if os.path.exists(lrcat_path + "-wal"):
            shutil.copyfile(lrcat_path + "-wal", backup + "-wal")
    except PermissionError:
        raise LightroomError(
            "macOS non permette a Provinia di leggere il catalogo. Apri Impostazioni di Sistema › "
            "Privacy e sicurezza › File e cartelle (oppure Accesso completo al disco), attiva "
            "Provinia e riprova.")
    except OSError as e:
        raise LightroomError(f"Impossibile copiare il catalogo: {e.strerror or e}")

    dst = sqlite3.connect(lrcat_path, timeout=5, isolation_level=None)
    dst.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"Adobe_images", "AgLibraryFile", "AgLibraryFolder", "AgLibraryRootFolder",
                "AgLibraryKeyword", "AgLibraryCollection", "Adobe_variablesTable"} <= tables:
            raise LightroomError("Il file scelto non sembra un catalogo di Lightroom Classic")
        dst.execute("BEGIN IMMEDIATE")
        try:
            stats = _export(cat, dst, step)
            dst.execute("COMMIT")
        except BaseException:
            dst.execute("ROLLBACK")
            for f in (backup, backup + "-wal"):      # niente scritto: la copia non serve
                try:
                    os.remove(f)
                except OSError:
                    pass
            raise
        try:
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            raise LightroomError("Il catalogo è aperto in Lightroom: chiudi Lightroom e riprova")
        raise
    finally:
        dst.close()
    stats["copia"] = backup
    return stats


def _export(cat, dst, step):
    db = cat.db
    stats = {"foto_lr": 0, "aggiornate": 0, "non_trovate": 0, "cartelle_non_trovate": [],
             "parole_chiave": 0, "raccolte": 0, "set": 0}

    # --- numeratore degli identificativi di Lightroom (comune a quasi tutte le tabelle)
    var = dst.execute("SELECT value FROM Adobe_variablesTable WHERE name='Adobe_entityIDCounter'").fetchone()
    top = int(float(var[0])) if var else 0
    for t in ("Adobe_images", "AgLibraryFile", "AgLibraryKeyword", "AgLibraryCollection",
              "AgLibraryCollectionImage", "AgLibraryCollectionContent", "AgLibraryFolder"):
        m = dst.execute(f"SELECT MAX(id_local) FROM {t}").fetchone()[0]
        top = max(top, int(m or 0))
    counter = [top]

    def new_id():
        counter[0] += 1
        return counter[0]

    cocoa_now = time.time() - COCOA_EPOCH

    # --- 1. foto di Lightroom per percorso
    step("Lettura delle foto nel catalogo Lightroom…")
    lr = {}
    for r in dst.execute("""SELECT i.id_local AS img, i.orientation, i.masterImage,
                                   rf.absolutePath, fo.pathFromRoot, f.baseName, f.extension
                            FROM Adobe_images i JOIN AgLibraryFile f ON f.id_local=i.rootFile
                            JOIN AgLibraryFolder fo ON fo.id_local=f.folder
                            JOIN AgLibraryRootFolder rf ON rf.id_local=fo.rootFolder"""):
        if r["masterImage"] is not None:       # copie virtuali: restano come sono
            continue
        name = r["baseName"] + ("." + r["extension"] if r["extension"] else "")
        p = os.path.join(r["absolutePath"] or "/", r["pathFromRoot"] or "", name)
        lr[_key(os.path.normpath(p))] = (r["img"], r["orientation"])
    stats["foto_lr"] = len(lr)

    # --- 2. foto di Provinia -> immagine di Lightroom
    step("Abbinamento delle foto…")
    photo_img = {}
    missing_dirs = {}
    updates = []
    for r in db.execute("""SELECT p.id, p.rating, p.label, p.flag, p.orientation,
                                  COALESCE(p.rotation, 0) AS rotation,
                                  r.path AS root, f.rel_path, p.filename
                           FROM photos p JOIN folders f ON f.id=p.folder_id
                           JOIN roots r ON r.id=f.root_id"""):
        full = os.path.normpath(os.path.join(r["root"], r["rel_path"], r["filename"]))
        hit = lr.get(_key(full))
        if not hit:
            stats["non_trovate"] += 1
            d = os.path.dirname(full)
            missing_dirs[d] = missing_dirs.get(d, 0) + 1
            continue
        img, cur_orient = hit
        photo_img[r["id"]] = img
        orient = cur_orient
        rot = (r["rotation"] or 0) // 90 % 4
        if rot and cur_orient in LR_ORIENT:
            # rotazione di Provinia sopra all'orientamento originale del file
            base = EXIF_TO_STEP.get(r["orientation"] or 1, 0)
            orient = LR_ORIENT[(base + rot) % 4]
        updates.append((int(r["rating"]) if r["rating"] else None,
                        LR_LABELS.get(r["label"] or "", ""),
                        float(r["flag"] or 0), orient, img))
    stats["cartelle_non_trovate"] = sorted(missing_dirs.items(), key=lambda x: -x[1])[:8]
    if not updates:
        raise LightroomError(
            "Nessuna foto di Provinia è nel catalogo scelto: prima importa in Lightroom le cartelle "
            "del NAS (con «Aggiungi»), poi ripeti l'esportazione. Il catalogo non è stato modificato.")

    step("Stelle, etichette, bandierine…")
    dst.executemany("UPDATE Adobe_images SET rating=?, colorLabels=?, pick=?, orientation=? "
                    "WHERE id_local=?", updates)
    stats["aggiornate"] = len(updates)

    # --- 3. parole chiave (albero, sotto la radice invisibile di Lightroom)
    step("Parole chiave…")
    rv = dst.execute("SELECT value FROM Adobe_variablesTable WHERE name='AgLibraryKeyword_rootTagID'").fetchone()
    root_id = int(float(rv[0])) if rv and rv[0] is not None else None
    root = dst.execute("SELECT id_local, genealogy FROM AgLibraryKeyword WHERE id_local=?",
                       (root_id,)).fetchone() if root_id else None
    if not root:
        root = dst.execute("SELECT id_local, genealogy FROM AgLibraryKeyword "
                           "WHERE parent IS NULL AND name IS NULL").fetchone()
    if not root:
        rid = new_id()
        gen = _genealogy("", rid)
        dst.execute("INSERT INTO AgLibraryKeyword(id_local, id_global, dateCreated, genealogy) "
                    "VALUES(?,?,?,?)", (rid, str(uuid.uuid4()).upper(), cocoa_now, gen))
        root = {"id_local": rid, "genealogy": gen}
    if rv is None:
        dst.execute("INSERT INTO Adobe_variablesTable(id_global, name, value) VALUES(?,?,?)",
                    (str(uuid.uuid4()).upper(), "AgLibraryKeyword_rootTagID", float(root["id_local"])))
    elif root_id != root["id_local"]:
        dst.execute("UPDATE Adobe_variablesTable SET value=? WHERE name='AgLibraryKeyword_rootTagID'",
                    (float(root["id_local"]),))

    kws = {r["id"]: r for r in db.execute("SELECT id, name, parent_id FROM keywords")}
    kw_map = {0: (root["id_local"], root["genealogy"])}

    def kw_lr(kid, depth=0):
        if kid in kw_map:
            return kw_map[kid]
        k = kws.get(kid)
        if k is None or depth > 50:
            return kw_map[0]
        par_id, par_gen = kw_lr(k["parent_id"] or 0, depth + 1)
        name = nfc(k["name"])
        ex = dst.execute("SELECT id_local, genealogy FROM AgLibraryKeyword WHERE parent=? AND lc_name=?",
                         (par_id, name.lower())).fetchone()
        if ex:
            kw_map[kid] = (ex["id_local"], ex["genealogy"])
        else:
            lid = new_id()
            gen = _genealogy(par_gen, lid)
            dst.execute("""INSERT INTO AgLibraryKeyword(id_local, id_global, dateCreated, genealogy,
                             lc_name, name, parent) VALUES(?,?,?,?,?,?,?)""",
                        (lid, str(uuid.uuid4()).upper(), cocoa_now, gen, name.lower(), name, par_id))
            kw_map[kid] = (lid, gen)
            stats["parole_chiave"] += 1
        return kw_map[kid]

    have = {(r[0], r[1]) for r in dst.execute("SELECT image, tag FROM AgLibraryKeywordImage")}
    rows = []
    for r in db.execute("SELECT photo_id, keyword_id FROM photo_keywords"):
        img = photo_img.get(r[0])
        if img is None or r[1] not in kws:
            continue
        tag = kw_lr(r[1])[0]
        if (img, tag) not in have:
            have.add((img, tag))
            rows.append((img, tag))
    dst.executemany("INSERT INTO AgLibraryKeywordImage(image, tag) VALUES(?,?)", rows)

    # --- 4. raccolte e set
    step("Raccolte…")
    colls = {r["id"]: r for r in db.execute("SELECT id, name, parent_id, is_set FROM collections")}
    coll_map = {}
    cc = dst.execute("SELECT MAX(changeCounter) FROM AgLibraryCollectionChangeCounter").fetchone()[0]
    change = [int(cc or 0)]

    def coll_lr(cid, depth=0):
        if cid in coll_map:
            return coll_map[cid]
        c = colls.get(cid)
        if c is None or depth > 50:
            return None
        parent = coll_lr(c["parent_id"], depth + 1) if c["parent_id"] else None
        par_id, par_gen = parent if parent else (None, "")
        name = nfc(c["name"])
        kind = "com.adobe.ag.library.group" if c["is_set"] else "com.adobe.ag.library.collection"
        ex = dst.execute("SELECT id_local, genealogy FROM AgLibraryCollection "
                         "WHERE parent IS ? AND name=? AND creationId=?", (par_id, name, kind)).fetchone()
        if ex:
            coll_map[cid] = (ex["id_local"], ex["genealogy"])
            return coll_map[cid]
        lid = new_id()
        gen = _genealogy(par_gen, lid)
        dst.execute("""INSERT INTO AgLibraryCollection(id_local, creationId, genealogy, name, parent,
                         systemOnly) VALUES(?,?,?,?,?,0)""", (lid, kind, gen, name, par_id))
        change[0] += 1
        dst.execute("INSERT OR REPLACE INTO AgLibraryCollectionChangeCounter(collection, changeCounter, "
                    "lastSyncedChangeCounter) VALUES(?,?,0)", (lid, change[0]))
        if not c["is_set"]:
            for content, module in (("captureTime", "com.adobe.ag.library.sortType"),
                                    ("ascending", "com.adobe.ag.library.sortDirection")):
                dst.execute("INSERT INTO AgLibraryCollectionContent(id_local, collection, content, "
                            "owningModule) VALUES(?,?,?,?)", (new_id(), lid, content, module))
        stats["set" if c["is_set"] else "raccolte"] += 1
        coll_map[cid] = (lid, gen)
        return coll_map[cid]

    for cid, c in colls.items():
        # la «raccolta rapida» di Lightroom, se arrivata con l'importazione, non va ricreata
        if not c["parent_id"] and c["name"].strip().lower() == "quick collection":
            continue
        coll_lr(cid)
    have = {(r[0], r[1]) for r in dst.execute("SELECT collection, image FROM AgLibraryCollectionImage")}
    rows = []
    for r in db.execute("SELECT collection_id, photo_id FROM collection_photos"):
        target = coll_map.get(r[0])
        img = photo_img.get(r[1])
        if not target or img is None or colls[r[0]]["is_set"]:
            continue
        if (target[0], img) not in have:
            have.add((target[0], img))
            rows.append((new_id(), target[0], img))
    dst.executemany("INSERT INTO AgLibraryCollectionImage(id_local, collection, image, pick) "
                    "VALUES(?,?,?,0.0)", rows)

    # --- 5. il numeratore riparte dopo gli identificativi usati
    dst.execute("UPDATE Adobe_variablesTable SET value=? WHERE name='Adobe_entityIDCounter'",
                (float(counter[0] + 1),))
    return stats
