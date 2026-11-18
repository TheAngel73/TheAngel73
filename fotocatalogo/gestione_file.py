"""Gestione dei file: ricerca duplicati ed eliminazione dal disco.

Duplicati
  - candidati: stesso nome (il confronto si fa solo tra questi, quindi è veloce);
  - "file vuoti": copie da 0 byte di un file che esiste integro altrove
    (tipico avanzo di uno spostamento interrotto): si possono eliminare senza perdite;
  - "duplicati": copie con lo stesso nome e contenuto IDENTICO, verificato
    leggendo i file (impronta SHA-1). Due file diversi non vengono mai confusi.
  Prima di eliminare una copia, raccolte, parole chiave, stelle, etichetta e
  bandierina vengono riunite sulla copia che resta: la catalogazione non si perde.

Eliminazione
  Il file (e il suo .xmp) viene cancellato dal disco: su un NAS con il cestino di
  rete attivo finisce lì. Poi la foto viene tolta dal catalogo.
"""
import hashlib
import os
import stat

from catalog import resolve_path
import thumbs

LAST = {}          # ultimo risultato della ricerca duplicati (per il catalogo aperto)
CHUNK = 1024 * 1024


def _hash(path, limit=None):
    h = hashlib.sha1()
    left = limit
    with open(path, "rb") as f:
        while True:
            n = CHUNK if left is None else min(CHUNK, left)
            if n <= 0:
                break
            b = f.read(n)
            if not b:
                break
            h.update(b)
            if left is not None:
                left -= len(b)
    return h.hexdigest()


def _full_path(r):
    return resolve_path(os.path.join(r["root"], r["rel_path"], r["filename"]))


# ---------------------------------------------------------------- ricerca duplicati
def find(cat, job):
    db = cat.db
    LAST.clear()

    # 1. dimensioni mancanti (es. foto importate da Lightroom e mai sincronizzate)
    rows = db.execute("""SELECT p.id, r.path AS root, f.rel_path, p.filename FROM photos p
                         JOIN folders f ON f.id=p.folder_id JOIN roots r ON r.id=f.root_id
                         WHERE p.size IS NULL AND p.missing=0""").fetchall()
    sizes = []
    for i, r in enumerate(rows):
        if job.cancelled:
            return {"duplicates": True, "interrotta": True}
        if i % 200 == 0:
            job.update(i, f"Lettura dimensioni dei file: {i:,} / {len(rows):,}".replace(",", "."),
                       total=len(rows))
        try:
            st = os.stat(_full_path(r))
            sizes.append((st.st_size, st.st_mtime, r["id"]))
        except OSError:
            pass
    if sizes:
        with cat.write_lock:
            db.executemany("UPDATE photos SET size=?, mtime=? WHERE id=?", sizes)
            db.commit()

    # 2. candidati: stesso nome (senza distinguere maiuscole)
    job.update(0, "Ricerca dei nomi ripetuti…", total=0)
    cand = db.execute("""
        SELECT p.id, lower(p.filename) AS k, p.filename, p.size, p.added_at, p.capture_time,
               p.rating, p.label, p.flag, r.path AS root, r.name AS root_name, f.rel_path,
               (SELECT count(*) FROM collection_photos cp WHERE cp.photo_id=p.id) AS nc,
               (SELECT count(*) FROM photo_keywords pk WHERE pk.photo_id=p.id) AS nk
        FROM photos p JOIN folders f ON f.id=p.folder_id JOIN roots r ON r.id=f.root_id
        WHERE p.missing=0 AND lower(p.filename) IN
              (SELECT lower(filename) FROM photos WHERE missing=0 GROUP BY lower(filename) HAVING count(*)>1)
        ORDER BY k, p.id""").fetchall()
    by_name = {}
    for r in cand:
        by_name.setdefault(r["k"], []).append(r)

    folder_birth = {}

    def member(r):
        path = _full_path(r)
        try:
            st = os.stat(path)
        except OSError:
            return None
        folder = os.path.dirname(path)
        if folder not in folder_birth:
            try:
                fs = os.stat(folder)
                folder_birth[folder] = getattr(fs, "st_birthtime", fs.st_mtime)
            except OSError:
                folder_birth[folder] = 0
        return {"id": r["id"], "name": r["filename"], "path": path, "size": st.st_size,
                "folder": "/".join(x for x in (r["root_name"], r["rel_path"]) if x),
                "added": r["added_at"], "capture": r["capture_time"], "rating": r["rating"] or 0,
                "label": r["label"], "flag": r["flag"] or 0, "nc": r["nc"], "nk": r["nk"],
                "hidden": bool(getattr(st, "st_flags", 0) & getattr(stat, "UF_HIDDEN", 0))
                          or r["filename"].startswith("."),
                "fbirth": folder_birth[folder]}

    groups, empties, orphans = [], [], []
    names = list(by_name.items())
    for n, (_, rows) in enumerate(names):
        if job.cancelled:
            return {"duplicates": True, "interrotta": True}
        if n % 20 == 0:
            job.update(n, f"Confronto dei contenuti: {n:,} / {len(names):,} nomi".replace(",", "."),
                       total=len(names))
        ms = [m for m in (member(r) for r in rows) if m]
        good = [m for m in ms if m["size"] > 0]
        for m in ms:
            if m["size"] == 0:
                if good:
                    best = max(good, key=lambda g: (not g["hidden"], g["nc"] + g["nk"], g["fbirth"]))
                    empties.append({"gid": f"e{m['id']}", "empty": m, "good": best})
                else:
                    orphans.append(m)
        # duplicati veri: stessa dimensione, poi stesso contenuto
        by_size = {}
        for m in good:
            by_size.setdefault(m["size"], []).append(m)
        for same in by_size.values():
            if len(same) < 2:
                continue
            by_head = {}
            for m in same:
                try:
                    by_head.setdefault(_hash(m["path"], 256 * 1024), []).append(m)
                except OSError:
                    pass
            for head in by_head.values():
                if len(head) < 2:
                    continue
                by_full = {}
                for m in head:
                    try:
                        by_full.setdefault(_hash(m["path"]), []).append(m)
                    except OSError:
                        pass
                for ident in by_full.values():
                    if len(ident) >= 2:
                        groups.append({"gid": f"g{ident[0]['id']}", "members": ident, "size": ident[0]["size"]})

    LAST.update({"groups": groups, "empties": empties, "orphans": orphans})
    return {"duplicates": True, "gruppi": len(groups), "vuoti": len(empties), "orfani": len(orphans),
            "copie": sum(len(g["members"]) - 1 for g in groups)}


# ---------------------------------------------------------------- operazioni comuni
def forget(cat, thumb_dir, ids):
    """Toglie foto dal catalogo (e le loro miniature). Non tocca i file."""
    db = cat.db
    for i in range(0, len(ids), 900):
        chunk = ",".join(map(str, ids[i:i + 900]))
        db.execute(f"DELETE FROM collection_photos WHERE photo_id IN ({chunk})")
        db.execute(f"DELETE FROM photo_keywords WHERE photo_id IN ({chunk})")
        db.execute(f"DELETE FROM photos WHERE id IN ({chunk})")
    for pid in ids:
        for size in ("m", "l"):
            try:
                os.remove(thumbs.thumb_file(thumb_dir, pid, size))
            except OSError:
                pass


def _delete_file(path):
    """Cancella il file e il suo eventuale .xmp. Un file già sparito non è un errore."""
    if os.path.exists(path):
        os.remove(path)
    base = os.path.splitext(path)[0]
    for ext in (".xmp", ".XMP"):
        if os.path.exists(base + ext):
            try:
                os.remove(base + ext)
            except OSError:
                pass


def _merge_into(db, keep, other):
    """Riunisce sulla copia tenuta le informazioni di catalogo della copia eliminata."""
    db.execute("""INSERT OR IGNORE INTO collection_photos(collection_id, photo_id, added_at)
                  SELECT collection_id, ?, added_at FROM collection_photos WHERE photo_id=?""", (keep, other))
    db.execute("""INSERT OR IGNORE INTO photo_keywords(photo_id, keyword_id)
                  SELECT ?, keyword_id FROM photo_keywords WHERE photo_id=?""", (keep, other))
    o = db.execute("SELECT rating, label, flag FROM photos WHERE id=?", (other,)).fetchone()
    if o:
        db.execute("""UPDATE photos SET rating=max(COALESCE(rating,0), ?), label=COALESCE(label, ?),
                      flag=CASE WHEN COALESCE(flag,0)=0 THEN ? ELSE flag END WHERE id=?""",
                   (o["rating"] or 0, o["label"], o["flag"] or 0, keep))


# ---------------------------------------------------------------- applicazione della revisione duplicati
def apply(cat, thumb_dir, plan, job):
    """plan: [{"keep": id | None, "remove": [id, ...]}]"""
    res = {"dupapply": True, "eliminati": 0, "byte": 0, "errori": []}
    total = sum(len(p.get("remove", [])) for p in plan)
    job.update(0, f"Eliminazione copie: 0 / {total}", total=total)
    done = 0
    db = cat.db
    for p in plan:
        if job.cancelled:
            break
        keep = p.get("keep")
        keep_path, keep_size = None, None
        if keep:
            keep_path = cat.photo_path(keep)
            try:
                keep_size = os.path.getsize(keep_path)
            except (OSError, TypeError):
                keep_size = None
            if not keep_size:
                res["errori"].append([os.path.basename(keep_path or str(keep)),
                                      "la copia da tenere non è più disponibile: gruppo saltato"])
                done += len(p.get("remove", []))
                continue
        for rid in p.get("remove", []):
            if rid == keep:
                continue
            path = cat.photo_path(rid)
            name = os.path.basename(path or str(rid))
            try:
                size = os.path.getsize(path) if path and os.path.exists(path) else 0
                # verifica finale: si elimina solo una copia vuota o della stessa dimensione
                if keep and size not in (0, keep_size):
                    raise ValueError("il file è cambiato dopo la ricerca: non eliminato")
                if keep and path and os.path.abspath(path) == os.path.abspath(keep_path):
                    raise ValueError("è lo stesso file della copia da tenere")
                with cat.write_lock:
                    if keep:
                        _merge_into(db, keep, rid)
                    _delete_file(path)
                    forget(cat, thumb_dir, [rid])
                    db.commit()
                res["eliminati"] += 1
                res["byte"] += size
            except Exception as e:  # noqa: BLE001
                res["errori"].append([name, getattr(e, "strerror", None) or str(e)])
            done += 1
            job.update(done, f"Eliminazione copie: {done} / {total}")
    res["interrotta"] = job.cancelled
    LAST.clear()
    return res


# ---------------------------------------------------------------- elimina dal disco
def delete(cat, thumb_dir, ids, job):
    res = {"deleted": True, "eliminati": 0, "byte": 0, "errori": []}
    job.update(0, f"Eliminazione: 0 / {len(ids)}", total=len(ids))
    for n, pid in enumerate(ids, 1):
        if job.cancelled:
            break
        path = cat.photo_path(pid)
        name = os.path.basename(path or str(pid))
        try:
            size = os.path.getsize(path) if path and os.path.exists(path) else 0
            with cat.write_lock:
                if path:
                    _delete_file(path)
                forget(cat, thumb_dir, [pid])
                cat.db.commit()
            res["eliminati"] += 1
            res["byte"] += size
        except Exception as e:  # noqa: BLE001
            res["errori"].append([name, getattr(e, "strerror", None) or str(e)])
        job.update(n, f"Eliminazione: {n} / {len(ids)}")
    res["interrotta"] = job.cancelled
    LAST.clear()
    return res
