"""Conversione dei RAW proprietari in DNG con Adobe DNG Converter (gratuito).

Impostazioni fisse, come quelle predefinite di Lightroom: compressione senza
perdita, anteprima media incorporata, originale non incorporato.

Per ogni file:
  1. il DNG viene scritto con un nome temporaneo nascosto, nella stessa cartella;
  2. si controlla che sia un DNG valido, poi prende il nome definitivo;
  3. nel catalogo il DNG prende il posto del RAW (raccolte, parole chiave,
     stelle, etichette e bandierine restano);
  4. solo a quel punto il RAW (e il suo eventuale .xmp) viene cancellato.
Se esiste già un DNG con lo stesso nome il file viene saltato, senza toccare nulla.
"""
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from catalog import nfc
import thumbs

RAW_PROP = {"nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "raf", "orf", "rw2", "pef", "srw",
            "x3f", "3fr", "iiq", "erf", "mos", "mrw", "raw", "rwl", "kdc", "dcr", "mef"}
CANDIDATES = [
    "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter",
    "~/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter",
]
PARALLEL = 2


def find_converter():
    paths = [os.environ.get("FOTOCAT_DNG")] + [os.path.expanduser(c) for c in CANDIDATES]
    for p in paths:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _is_dng(path):
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        return os.path.getsize(path) > 1024 and head in (b"II*\0", b"MM\0*")
    except OSError:
        return False


def _drop_cached(thumb_dir, pid):
    for size in ("m", "l"):
        try:
            os.remove(thumbs.thumb_file(thumb_dir, pid, size))
        except OSError:
            pass
    try:
        os.remove(os.path.join(thumb_dir, "_piena_risoluzione", f"{pid}.jpg"))
    except OSError:
        pass


def _convert_one(cat, conv, pid, thumb_dir):
    src = cat.photo_path(pid)
    if not src or not os.path.exists(src):
        return "saltata", "file non trovato (il NAS è collegato?)"
    folder = os.path.dirname(src)
    base = os.path.splitext(os.path.basename(src))[0]
    dest = os.path.join(folder, base + ".dng")
    if os.path.exists(dest) or os.path.exists(os.path.join(folder, base + ".DNG")):
        return "saltata", "esiste già un DNG con lo stesso nome"
    tmp_name = f".{base}.fotocatalogo-tmp.dng"
    tmp = os.path.join(folder, tmp_name)
    try:
        r = subprocess.run([conv, "-c", "-p1", "-d", folder, "-o", tmp_name, src],
                           capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        r = None
    if not _is_dng(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        detail = ""
        if r is not None:
            detail = (r.stderr or r.stdout or b"").decode("utf-8", "ignore").strip()[:150]
        return "errore", "conversione non riuscita" + (f": {detail}" if detail else "")
    os.rename(tmp, dest)
    st = os.stat(dest)
    new_name = nfc(base + ".dng")

    with cat.write_lock:
        db = cat.db
        row = db.execute("SELECT folder_id FROM photos WHERE id=?", (pid,)).fetchone()
        stale = db.execute("SELECT id FROM photos WHERE folder_id=? AND filename=? AND id<>?",
                           (row["folder_id"], new_name, pid)).fetchone()
        if stale:   # una vecchia voce con lo stesso nome (file sparito): ne raccogliamo i dati
            old = stale["id"]
            db.execute("""INSERT OR IGNORE INTO collection_photos(collection_id, photo_id, added_at)
                          SELECT collection_id, ?, added_at FROM collection_photos WHERE photo_id=?""", (pid, old))
            db.execute("""INSERT OR IGNORE INTO photo_keywords(photo_id, keyword_id)
                          SELECT ?, keyword_id FROM photo_keywords WHERE photo_id=?""", (pid, old))
            for t in ("collection_photos", "photo_keywords"):
                db.execute(f"DELETE FROM {t} WHERE photo_id=?", (old,))
            db.execute("DELETE FROM photos WHERE id=?", (old,))
        db.execute("UPDATE photos SET filename=?, ext='dng', size=?, mtime=?, missing=0 WHERE id=?",
                   (new_name, st.st_size, st.st_mtime, pid))
        db.commit()
    _drop_cached(thumb_dir, pid)

    try:
        os.remove(src)
    except OSError as e:
        return "ok", f"DNG creato, ma l'originale non è stato cancellato: {e.strerror or e}"
    for ext in (".xmp", ".XMP"):
        sidecar = os.path.join(folder, base + ext)
        if os.path.exists(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass
    return "ok", None


def convert(cat, ids, thumb_dir, job):
    conv = find_converter()
    if not conv:
        raise RuntimeError("Adobe DNG Converter non trovato: installalo nella cartella Applicazioni")
    todo = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        todo += [r["id"] for r in cat.db.execute(
            f"SELECT id, ext FROM photos WHERE id IN ({','.join('?' * len(chunk))}) ORDER BY id", chunk)
            if (r["ext"] or "").lower() in RAW_PROP]
    res = {"dng": True, "da_convertire": len(todo), "convertite": 0, "saltate": [], "errori": [], "avvisi": []}
    job.update(0, f"Conversione in DNG: 0 / {len(todo)}", total=len(todo))
    lock = threading.Lock()
    done = [0]

    def one(pid):
        if job.cancelled:
            return
        name = os.path.basename(cat.photo_path(pid) or str(pid))
        try:
            outcome, note = _convert_one(cat, conv, pid, thumb_dir)
        except Exception as e:  # noqa: BLE001
            outcome, note = "errore", str(e)
        with lock:
            done[0] += 1
            if outcome == "ok":
                res["convertite"] += 1
                if note:
                    res["avvisi"].append([name, note])
            elif outcome == "saltata":
                res["saltate"].append([name, note])
            else:
                res["errori"].append([name, note])
            job.update(done[0], f"Conversione in DNG: {done[0]} / {len(todo)}")

    with ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(one, todo))
    res["interrotta"] = job.cancelled
    return res
