"""«Salva raccolta…»: copia gli ORIGINALI di una raccolta (o di un set, con tutte le
raccolte e i set che contiene) in una cartella, ricreando la struttura come cartelle.

Esempio: il set «Gite» con le raccolte «Torino» e «Napoli» diventa
    <destinazione>/Gite/Torino/…   <destinazione>/Gite/Napoli/…

I file del catalogo non vengono mai toccati: si legge e si copia soltanto.
- se la cartella principale esiste già si usa «Gite (2)» e così via (nulla viene sovrascritto);
- due foto con lo stesso nome nella stessa raccolta diventano «nome.jpg» e «nome (2).jpg»;
- ogni file viene copiato con un nome temporaneo e rinominato solo a copia completata,
  così un'interruzione non lascia file a metà;
- a richiesta si copiano anche i .xmp accanto ai file.
"""
import os
import shutil

LAST = {}


def clean_name(name):
    """Nome di raccolta -> nome di cartella valido sul Mac e sul NAS."""
    name = (name or "").replace("/", "-").replace(":", "-").replace("\\", "-").strip()
    name = name.lstrip(".").strip()
    return name[:200] or "Senza nome"


def _unique_dir(parent, name):
    path, n = os.path.join(parent, name), 2
    while os.path.exists(path):
        path = os.path.join(parent, f"{name} ({n})")
        n += 1
    return path


def _unique_file(folder, filename, taken):
    base, ext = os.path.splitext(filename)
    cand, n = filename, 2
    while cand.lower() in taken or os.path.exists(os.path.join(folder, cand)):
        cand = f"{base} ({n}){ext}"
        n += 1
    taken.add(cand.lower())
    return cand


def _sidecar(src):
    base = os.path.splitext(src)[0]
    for x in (".xmp", ".XMP"):
        if os.path.exists(base + x):
            return base + x
    return None


def plan(cat, cid):
    """Struttura da creare: lista di (percorso relativo, [photo_id]) e il nome della radice."""
    db = cat.db
    rows = db.execute("SELECT id, name, parent_id, is_set FROM collections").fetchall()
    by_id = {r["id"]: r for r in rows}
    if cid not in by_id:
        raise ValueError("Raccolta non trovata")
    children = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r)
    out = []

    def walk(node, rel, seen):
        if node["id"] in seen:                 # protezione da cicli (non dovrebbero esistere)
            return
        seen.add(node["id"])
        ids = [x[0] for x in db.execute(
            """SELECT cp.photo_id FROM collection_photos cp JOIN photos p ON p.id=cp.photo_id
               WHERE cp.collection_id=? ORDER BY p.capture_time, p.filename""", (node["id"],))]
        out.append((rel, ids))
        used = set()
        for ch in sorted(children.get(node["id"], []), key=lambda r: r["name"].lower()):
            sub = clean_name(ch["name"])
            n, cand = 2, sub
            while cand.lower() in used:          # due raccolte sorelle con lo stesso nome
                cand = f"{sub} ({n})"
                n += 1
            used.add(cand.lower())
            walk(ch, os.path.join(rel, cand), seen)

    walk(by_id[cid], "", set())
    return clean_name(by_id[cid]["name"]), out


def summary(cat, cid):
    """Per la finestra di conferma: cartelle, numero di file e dimensione totale."""
    root, items = plan(cat, cid)
    total = sum(len(ids) for _, ids in items)
    size = 0
    ids = {i for _, l in items for i in l}
    q = list(ids)
    for i in range(0, len(q), 900):
        chunk = q[i:i + 900]
        size += cat.db.execute(f"SELECT coalesce(sum(size),0) FROM photos WHERE id IN ({','.join('?' * len(chunk))})",
                               chunk).fetchone()[0] or 0
    folders = [[os.path.join(root, rel) if rel else root, len(l)] for rel, l in items]
    return {"root": root, "files": total, "bytes": size, "folders": folders[:200], "nfolders": len(folders)}


def save(cat, cid, opts, job):
    dest = os.path.expanduser(opts.get("dest") or "")
    if not dest or not os.path.isdir(dest):
        raise RuntimeError(f"Cartella di destinazione non trovata: {dest}")
    xmp = bool(opts.get("xmp", True))
    root_name, items = plan(cat, cid)
    total = sum(len(ids) for _, ids in items)
    info = summary(cat, cid)
    try:
        free = shutil.disk_usage(dest).free
        if info["bytes"] and free < info["bytes"] * 1.02:
            raise RuntimeError(f"Spazio insufficiente nella destinazione: servono {info['bytes'] / 1e9:.1f} GB, "
                               f"liberi {free / 1e9:.1f} GB")
    except OSError:
        pass
    top = _unique_dir(dest, root_name)
    os.makedirs(top)
    LAST.clear()
    LAST["dest"] = top
    res = {"saved": True, "dest": top, "copiate": 0, "byte": 0, "cartelle": 0, "saltate": [], "errori": []}
    job.update(0, f"Salvataggio: 0 / {total}", total=total)
    n = 0
    for rel, ids in items:
        if job.cancelled:
            break
        folder = os.path.join(top, rel) if rel else top
        os.makedirs(folder, exist_ok=True)
        res["cartelle"] += 1
        taken = set()
        for pid in ids:
            if job.cancelled:
                break
            n += 1
            src = cat.photo_path(pid)
            name = os.path.basename(src or str(pid))
            if not src or not os.path.isfile(src):
                res["saltate"].append([name, "file non trovato (il NAS è collegato?)"])
                job.update(n, f"Salvataggio: {n} / {total}")
                continue
            final = _unique_file(folder, name, taken)
            tmp = os.path.join(folder, f".{final}.provinia-tmp")
            try:
                shutil.copy2(src, tmp)
                os.replace(tmp, os.path.join(folder, final))
                res["copiate"] += 1
                res["byte"] += os.path.getsize(os.path.join(folder, final))
                side = _sidecar(src) if xmp else None
                if side:
                    side_name = os.path.splitext(final)[0] + ".xmp"
                    if side_name.lower() not in taken and not os.path.exists(os.path.join(folder, side_name)):
                        taken.add(side_name.lower())
                        shutil.copy2(side, os.path.join(folder, side_name))
            except OSError as e:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
                res["errori"].append([name, e.strerror or str(e)])
            job.update(n, f"Salvataggio: {n} / {total}")
    res["interrotta"] = job.cancelled
    return res
