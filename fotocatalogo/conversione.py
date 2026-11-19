"""Conversione dei file "da convertire":
  - RAW proprietari -> DNG, con Adobe DNG Converter (gratuito), impostazioni
    predefinite di Lightroom: compressione senza perdita, anteprima media;
  - PNG -> JPEG (qualità 92%), con gli strumenti di macOS: le parti trasparenti
    diventano bianche, data del file e dati EXIF/GPS vengono mantenuti.

Per ogni file:
  1. il nuovo file viene scritto con un nome temporaneo nascosto, nella stessa cartella;
  2. si controlla che sia valido, poi prende il nome definitivo;
  3. nel catalogo prende il posto dell'originale (raccolte, parole chiave,
     stelle, etichette e bandierine restano);
  4. solo a quel punto l'originale (e il suo eventuale .xmp) viene cancellato.
Se esiste già un file con lo stesso nome di destinazione, l'originale viene saltato.
"""
import os
import subprocess
import sys
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
PNG_EXT = {"png"}
JPEG_QUALITY = 0.92
HERE = os.path.dirname(os.path.abspath(__file__))
IS_MAC = sys.platform == "darwin"


find_imghelper = thumbs.find_imghelper


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
    thumbs.drop_cached(thumb_dir, pid)


def _is_jpeg(path):
    try:
        with open(path, "rb") as f:
            return os.path.getsize(path) > 100 and f.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _run(cmd, timeout=900):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, (r.stderr or r.stdout or b"").decode("utf-8", "ignore").strip()[:150]
    except subprocess.TimeoutExpired:
        return -1, "tempo scaduto"
    except OSError as e:
        return -1, str(e)


def _make_dng(conv, src, folder, tmp_name):
    if not conv:
        return "Adobe DNG Converter non installato"
    _, detail = _run([conv, "-c", "-p1", "-d", folder, "-o", tmp_name, src])
    return None if _is_dng(os.path.join(folder, tmp_name)) else (detail or "conversione non riuscita")


def _make_jpeg(src, tmp):
    helper = find_imghelper()
    if helper:
        _, detail = _run([helper, "tojpeg", src, tmp, str(JPEG_QUALITY)], 300)
    elif IS_MAC:
        # senza l'app (avvio dal browser): sips, ma solo per i PNG senza trasparenza
        code, out = _run(["sips", "-g", "hasAlpha", src], 60)
        if "hasAlpha: yes" in out:
            return "ha parti trasparenti: convertilo dall'app FotoCatalogo"
        _, detail = _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions",
                          str(int(JPEG_QUALITY * 100)), src, "--out", tmp], 300)
    else:
        try:                          # solo per sviluppo, fuori da macOS
            from PIL import Image
            with Image.open(src) as im:
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[3])
                bg.save(tmp, "JPEG", quality=int(JPEG_QUALITY * 100), exif=im.info.get("exif", b""))
            detail = ""
        except Exception as e:  # noqa: BLE001
            detail = str(e)
    return None if _is_jpeg(tmp) else (detail or "conversione non riuscita")


TARGETS = {"dng": (".dng", ".DNG"), "jpg": (".jpg", ".JPG", ".jpeg", ".JPEG")}


def _convert_one(cat, conv, pid, thumb_dir):
    src = cat.photo_path(pid)
    if not src or not os.path.exists(src):
        return "saltata", "file non trovato (il NAS è collegato?)"
    folder = os.path.dirname(src)
    base, src_ext = os.path.splitext(os.path.basename(src))
    target = "jpg" if src_ext.lower().lstrip(".") in PNG_EXT else "dng"
    if any(os.path.exists(os.path.join(folder, base + e)) for e in TARGETS[target]):
        return "saltata", f"esiste già un {target.upper()} con lo stesso nome"
    tmp_name = f".{base}.fotocatalogo-tmp.{target}"
    tmp = os.path.join(folder, tmp_name)
    problem = _make_dng(conv, src, folder, tmp_name) if target == "dng" else _make_jpeg(src, tmp)
    if problem:
        if os.path.exists(tmp):
            os.remove(tmp)
        skip = problem.startswith(("Adobe DNG Converter non", "ha parti trasparenti"))
        return ("saltata" if skip else "errore"), problem
    orig = os.stat(src)
    if target == "jpg":
        os.utime(tmp, (orig.st_atime, orig.st_mtime))     # mantiene la data del file originale
    dest = os.path.join(folder, base + "." + target)
    os.rename(tmp, dest)
    st = os.stat(dest)
    new_name = nfc(base + "." + target)

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
        db.execute("UPDATE photos SET filename=?, ext=?, size=?, mtime=?, missing=0 WHERE id=?",
                   (new_name, target, st.st_size, st.st_mtime, pid))
        db.commit()
    _drop_cached(thumb_dir, pid)

    try:
        os.remove(src)
    except OSError as e:
        return "ok", f"{target.upper()} creato, ma l'originale non è stato cancellato: {e.strerror or e}"
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
    todo = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        todo += [r["id"] for r in cat.db.execute(
            f"SELECT id, ext FROM photos WHERE id IN ({','.join('?' * len(chunk))}) ORDER BY id", chunk)
            if (r["ext"] or "").lower() in RAW_PROP | PNG_EXT]
    res = {"dng": True, "da_convertire": len(todo), "convertite": 0, "saltate": [], "errori": [], "avvisi": []}
    job.update(0, f"Conversione: 0 / {len(todo)}", total=len(todo))
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
            job.update(done[0], f"Conversione: {done[0]} / {len(todo)}")

    with ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(one, todo))
    res["interrotta"] = job.cancelled
    return res
