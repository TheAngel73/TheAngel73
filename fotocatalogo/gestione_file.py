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
import errno
import hashlib
import re
import os
import shutil
import stat

from catalog import nfc, resolve_path
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
        thumbs.drop_cached(thumb_dir, pid)


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


# ---------------------------------------------------------------- sposta
def _move_file(src, dst):
    """Sposta un file; tra dischi/volumi diversi copia, verifica e poi cancella l'originale."""
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    tmp = os.path.join(os.path.dirname(dst), "." + os.path.basename(dst) + ".fotocatalogo-tmp")
    shutil.copy2(src, tmp)
    if os.path.getsize(tmp) != os.path.getsize(src):
        os.remove(tmp)
        raise OSError("copia incompleta")
    os.replace(tmp, dst)
    os.remove(src)


def move(cat, ids, folder_id, job):
    db = cat.db
    f = db.execute("""SELECT f.root_id, f.rel_path, r.path FROM folders f JOIN roots r ON r.id=f.root_id
                      WHERE f.id=?""", (folder_id,)).fetchone()
    if not f:
        raise RuntimeError("Cartella di destinazione sconosciuta")
    dest = resolve_path(os.path.join(f["path"], f["rel_path"]))
    if not os.path.isdir(dest):
        raise RuntimeError(f"Cartella non raggiungibile (NAS collegato?): {dest}")
    res = {"moved": True, "spostate": 0, "saltate": [], "errori": [], "dest": os.path.basename(dest) or dest}
    job.update(0, f"Spostamento: 0 / {len(ids)}", total=len(ids))
    for n, pid in enumerate(ids, 1):
        if job.cancelled:
            break
        src = cat.photo_path(pid)
        name = os.path.basename(src or str(pid))
        try:
            if not src or not os.path.exists(src):
                res["saltate"].append([name, "file non trovato (il NAS è collegato?)"])
            elif os.path.dirname(os.path.abspath(src)) == os.path.abspath(dest):
                res["saltate"].append([name, "è già in questa cartella"])
            elif os.path.exists(os.path.join(dest, name)):
                res["saltate"].append([name, "nella destinazione esiste già un file con questo nome"])
            else:
                _move_file(src, os.path.join(dest, name))
                base = os.path.splitext(src)[0]
                for x in (".xmp", ".XMP"):
                    side = base + x
                    target = os.path.join(dest, os.path.basename(side))
                    if os.path.exists(side) and not os.path.exists(target):
                        try:
                            _move_file(side, target)
                        except OSError:
                            pass
                with cat.write_lock:
                    db.execute("UPDATE photos SET folder_id=?, missing=0 WHERE id=?", (folder_id, pid))
                    db.commit()
                res["spostate"] += 1
        except Exception as e:  # noqa: BLE001
            res["errori"].append([name, getattr(e, "strerror", None) or str(e)])
        job.update(n, f"Spostamento: {n} / {len(ids)}")
    res["interrotta"] = job.cancelled
    return res


# ---------------------------------------------------------------- rinomina
def rename_plan(cat, ids, opts):
    """Calcola i nuovi nomi. opts: prefix, start, digits, sep, order (date|name), lower_ext, keep_pairs."""
    prefix = (opts.get("prefix") or "").strip()
    if any(c in prefix for c in "/:\\") or prefix.startswith("."):
        raise ValueError("Il prefisso non può contenere / : \\ né iniziare con un punto")
    start = max(0, int(opts.get("start") or 1))
    digits = max(1, min(8, int(opts.get("digits") or 4)))
    sep = opts.get("sep", " ")
    sep = sep if sep in (" ", "_", "-", "") else " "
    lower_ext = opts.get("lower_ext", True)
    keep_pairs = opts.get("keep_pairs", True)
    db = cat.db
    rows = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        rows += db.execute(f"""SELECT p.id, p.filename, p.folder_id, p.capture_time, p.missing,
                               r.path AS root, f.rel_path FROM photos p
                               JOIN folders f ON f.id=p.folder_id JOIN roots r ON r.id=f.root_id
                               WHERE p.id IN ({','.join('?' * len(chunk))})""", chunk).fetchall()
    if opts.get("order") == "name":
        rows.sort(key=lambda r: (r["filename"].lower(), r["id"]))
    else:
        rows.sort(key=lambda r: (r["capture_time"] or "", r["filename"].lower(), r["id"]))
    plan, skipped = [], []
    numbers = {}
    n = start
    single = opts.get("name") is not None          # rinomina libera di un solo file
    if single:
        name = nfc((opts.get("name") or "").strip())
        if len(rows) != 1:
            raise ValueError("La rinomina libera vale per un file alla volta")
        if not name or any(c in name for c in "/:\\") or name.startswith("."):
            raise ValueError("Il nome non può essere vuoto, contenere / : \\ né iniziare con un punto")
    for r in rows:
        if r["missing"]:
            skipped.append([r["filename"], "file mancante"])
            continue
        base, ext = os.path.splitext(r["filename"])
        folder_dir = resolve_path(os.path.join(r["root"], r["rel_path"]))
        if single:
            plan.append({"id": r["id"], "old": r["filename"], "new": nfc(name + ext), "dir": folder_dir,
                         "keep_side": _has_sibling(folder_dir, r["filename"])})
            continue
        key = (r["folder_id"], base.lower())
        if keep_pairs and key in numbers:
            num = numbers[key]
        else:
            num, n = n, n + 1
            numbers[key] = num
        new_ext = ext.lower() if lower_ext else ext
        new = nfc(f"{prefix}{sep if prefix else ''}{num:0{digits}d}{new_ext}")
        plan.append({"id": r["id"], "old": r["filename"], "new": new, "dir": folder_dir})
    # nomi di destinazione già occupati da file che NON fanno parte della rinomina
    sources = {(p["dir"], p["old"].lower()) for p in plan}
    batch = {p["id"] for p in plan}
    folders = {r["id"]: r["folder_id"] for r in rows}
    in_catalog = {}
    for fid in set(folders.values()):
        for r in db.execute("SELECT id, filename, missing FROM photos WHERE folder_id=?", (fid,)):
            if r["id"] not in batch:
                in_catalog[(fid, r["filename"].lower())] = r["missing"]
    seen, conflicts = set(), []
    for p in plan:
        k = (p["dir"], p["new"].lower())
        if k in seen:
            conflicts.append([p["new"], "due file riceverebbero lo stesso nome"])
        seen.add(k)
        if k not in sources and os.path.exists(os.path.join(p["dir"], p["new"])):
            conflicts.append([p["new"], "esiste già nella cartella"])
        elif in_catalog.get((folders[p["id"]], p["new"].lower())) == 1:
            conflicts.append([p["new"], "è il nome di una foto mancante nel catalogo: rimuovila prima da «File mancanti»"])
        elif (folders[p["id"]], p["new"].lower()) in in_catalog:
            conflicts.append([p["new"], "è già usato da un'altra foto del catalogo"])
    return plan, skipped, conflicts


def _has_sibling(folder_dir, filename):
    """True se nella cartella c'è un altro file con lo stesso nome base (es. ARW + JPG):
    in quel caso il .xmp resta a lui e non segue il file rinominato da solo."""
    base = os.path.splitext(filename)[0].lower()
    try:
        names = os.listdir(folder_dir)
    except OSError:
        return False
    return any(os.path.splitext(x)[0].lower() == base and x.lower() != filename.lower()
               and os.path.splitext(x)[1].lower() != ".xmp" for x in names)


def rename(cat, ids, opts, job):
    plan, skipped, conflicts = rename_plan(cat, ids, opts)
    if conflicts:
        raise RuntimeError(f"Rinomina annullata: {len(conflicts)} nomi già occupati (es. {conflicts[0][0]})")
    plan = [p for p in plan if p["old"] != p["new"]]
    res = {"renamed": True, "rinominate": 0, "saltate": skipped, "errori": []}
    total = len(plan)
    job.update(0, f"Rinomina: 0 / {total}", total=total)
    # 1° passaggio: nomi temporanei (così anche gli scambi di nome sono sicuri)
    done1 = []
    for p in plan:
        p["tmp"] = os.path.join(p["dir"], f".fctmp-{p['id']}-{p['new']}")
        try:
            os.rename(resolve_path(os.path.join(p["dir"], p["old"])), p["tmp"])
            done1.append(p)
        except OSError as e:
            for q in reversed(done1):                      # torna tutto come prima
                try:
                    os.rename(q["tmp"], os.path.join(q["dir"], q["old"]))
                except OSError:
                    pass
            raise RuntimeError(f"Rinomina annullata, nessun file cambiato: {p['old']}: {e.strerror or e}")
    # i .xmp seguono il loro file (uno per nome base), anch'essi passando da un nome temporaneo
    seen_bases = set()
    for p in plan:
        old_base = os.path.splitext(p["old"])[0]
        key = (p["dir"], old_base.lower())
        if key in seen_bases or p.get("keep_side"):
            continue
        seen_bases.add(key)
        for x in (".xmp", ".XMP"):
            side = os.path.join(p["dir"], old_base + x)
            if os.path.exists(side):
                tmp_side = os.path.join(p["dir"], f".fctmpx-{os.path.splitext(p['new'])[0]}.xmp")
                try:
                    os.rename(side, tmp_side)
                    p["side_old"], p["side_tmp"] = side, tmp_side
                except OSError:
                    pass
                break
    # anche nel catalogo prima i nomi temporanei, tutti insieme: un nuovo nome può essere
    # ancora assegnato a un altro file del gruppo (es. 0001 -> 0002 mentre 0002 -> 0003)
    db = cat.db
    with cat.write_lock:
        db.executemany("UPDATE photos SET filename=? WHERE id=?", [(f".fctmp-{p['id']}", p["id"]) for p in plan])
        db.commit()
    # 2° passaggio: nomi definitivi, .xmp e catalogo
    done2 = set()
    try:
        for n, p in enumerate(plan, 1):
            final = os.path.join(p["dir"], p["new"])
            try:
                os.rename(p["tmp"], final)
            except OSError as e:
                res["errori"].append([p["old"], e.strerror or str(e)])
                continue          # verrà rimesso a posto qui sotto
            try:
                with cat.write_lock:
                    db.execute("UPDATE photos SET filename=?, ext=? WHERE id=?",
                               (p["new"], os.path.splitext(p["new"])[1].lstrip(".").lower(), p["id"]))
                    db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                try:
                    os.rename(final, p["tmp"])        # torna al nome temporaneo: sotto viene rimesso com'era
                except OSError:
                    pass
                res["errori"].append([p["old"], f"catalogo non aggiornato: {e}"])
                continue
            done2.add(p["id"])
            if p.get("side_tmp"):
                target = os.path.join(p["dir"], os.path.splitext(p["new"])[0] + ".xmp")
                try:
                    os.rename(p["side_tmp"], target if not os.path.exists(target) else p["side_old"])
                except OSError:
                    pass
            res["rinominate"] += 1
            job.update(n, f"Rinomina: {n} / {total}")
    finally:
        # ciò che non è stato completato torna com'era, sul disco e nel catalogo
        for p in plan:
            if p["id"] in done2:
                continue
            try:
                if os.path.exists(p["tmp"]):
                    os.rename(p["tmp"], os.path.join(p["dir"], p["old"]))
                if p.get("side_tmp") and os.path.exists(p["side_tmp"]):
                    os.rename(p["side_tmp"], p["side_old"])
            except OSError:
                pass
            try:
                with cat.write_lock:
                    db.execute("UPDATE photos SET filename=? WHERE id=?", (p["old"], p["id"]))
                    db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
    return res


def recover_renames(cat, root_id, rel, dirpath, filenames):
    """Rete di sicurezza: completa le rinomine interrotte (file '.fctmp-<id>-<nome finale>'
    rimasti da una chiusura improvvisa). Restituisce l'elenco dei nomi aggiornato."""
    names = list(filenames)
    for fn in filenames:
        mx = TMPX_RE.match(fn)
        if mx and not os.path.exists(os.path.join(dirpath, mx.group(1))):
            try:
                os.rename(os.path.join(dirpath, fn), os.path.join(dirpath, mx.group(1)))
            except OSError:
                pass
            continue
        m = TMP_RE.match(fn)
        if not m:
            continue
        pid, final = int(m.group(1)), m.group(2)
        target = os.path.join(dirpath, final)
        if os.path.exists(target):
            continue
        try:
            os.rename(os.path.join(dirpath, fn), target)
        except OSError:
            continue
        names.remove(fn)
        names.append(final)
        with cat.write_lock:
            row = cat.db.execute("""SELECT p.id FROM photos p JOIN folders f ON f.id=p.folder_id
                                    WHERE p.id=? AND f.root_id=? AND f.rel_path=?""", (pid, root_id, rel)).fetchone()
            if row:
                cat.db.execute("UPDATE photos SET filename=?, ext=?, missing=0 WHERE id=?",
                               (nfc(final), final.rsplit(".", 1)[-1].lower() if "." in final else "", pid))
                cat.db.commit()
    return names


# ---------------------------------------------------------------- cartelle
TMP_RE = re.compile(r"^\.fctmp-(\d+)-(.+)$")
TMPX_RE = re.compile(r"^\.fctmpx-(.+)$")
IGNORABLE = {".DS_Store", "Thumbs.db", "desktop.ini", "@eaDir", ".localized"}


def _folder(cat, fid):
    f = cat.db.execute("""SELECT f.id, f.root_id, f.rel_path, r.path FROM folders f
                          JOIN roots r ON r.id=f.root_id WHERE f.id=?""", (fid,)).fetchone()
    if not f:
        raise ValueError("Cartella sconosciuta")
    return f, resolve_path(os.path.join(f["path"], f["rel_path"]))


def _clean_name(name):
    name = nfc((name or "").strip())
    if not name or name in (".", "..") or any(c in name for c in "/:\\") or name.startswith("."):
        raise ValueError("Nome di cartella non valido")
    return name


def folder_create(cat, fid, name):
    f, path = _folder(cat, fid)
    name = _clean_name(name)
    target = os.path.join(path, name)
    if os.path.exists(target):
        raise ValueError("Esiste già una cartella con questo nome")
    os.mkdir(target)
    with cat.write_lock:
        new = cat.ensure_folder(f["root_id"], f"{f['rel_path']}/{name}".strip("/"))
        cat.db.commit()
    return {"id": new}


def folder_rename(cat, fid, name):
    f, path = _folder(cat, fid)
    if not f["rel_path"]:
        raise ValueError("La cartella principale non si rinomina da qui: usa «Cambia percorso del NAS»")
    name = _clean_name(name)
    parent = os.path.dirname(f["rel_path"])
    new_rel = f"{parent}/{name}".strip("/")
    target = os.path.join(os.path.dirname(path), name)
    if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(path):
        raise ValueError("Esiste già una cartella con questo nome")
    os.rename(path, target)
    old = f["rel_path"]
    with cat.write_lock:
        db = cat.db
        for table in ("folders", "dir_state"):
            db.execute(f"""UPDATE {table} SET rel_path = ? || substr(rel_path, ?)
                           WHERE root_id=? AND (rel_path=? OR substr(rel_path,1,?)=?)""",
                       (new_rel, len(old) + 1, f["root_id"], old, len(old) + 1, old + "/"))
        db.commit()
    return None


def folder_delete(cat, fid):
    f, path = _folder(cat, fid)
    if not f["rel_path"]:
        raise ValueError("La cartella principale si toglie con «Rimuovi dal catalogo»")
    db = cat.db
    sub = db.execute("SELECT count(*) FROM folders WHERE parent_id=?", (fid,)).fetchone()[0]
    photos = db.execute("SELECT count(*) FROM photos WHERE folder_id=? AND missing=0", (fid,)).fetchone()[0]
    if os.path.isdir(path):
        rest = [n for n in os.listdir(path) if n not in IGNORABLE and not n.startswith("._")]
        if rest or sub or photos:
            raise ValueError("La cartella non è vuota: sposta o elimina prima il suo contenuto")
        for n in os.listdir(path):
            p = os.path.join(path, n)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        os.rmdir(path)
    elif sub or photos:
        raise ValueError("La cartella non è vuota nel catalogo")
    with cat.write_lock:
        missing = [r[0] for r in db.execute("SELECT id FROM photos WHERE folder_id=?", (fid,))]
        if missing:
            ph = ",".join(map(str, missing))
            db.execute(f"DELETE FROM collection_photos WHERE photo_id IN ({ph})")
            db.execute(f"DELETE FROM photo_keywords WHERE photo_id IN ({ph})")
            db.execute(f"DELETE FROM photos WHERE id IN ({ph})")
        db.execute("DELETE FROM dir_state WHERE root_id=? AND rel_path=?", (f["root_id"], f["rel_path"]))
        db.execute("DELETE FROM folders WHERE id=?", (fid,))
        db.commit()
    return None


# ---------------------------------------------------------------- importazione (file trascinati)
def safe_subpath(rel):
    """Percorso relativo inviato dall'interfaccia: niente '..', niente percorsi assoluti."""
    parts = [nfc(p) for p in (rel or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        raise ValueError("Percorso non valido")
    return parts
