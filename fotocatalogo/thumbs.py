"""Generazione delle miniature, salvate in cache sul Mac (non sul NAS).

Su macOS usa gli strumenti di sistema:
  - qlmanage (QuickLook): veloce, legge RAW/PSD/HEIC usando l'anteprima
    incorporata e rispetta l'orientamento;
  - sips: usato per convertire in JPEG e come piano B.
Su altri sistemi (solo per sviluppo) usa Pillow se installato.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import threading

SIZES = {"m": 480, "l": 2048}
IS_MAC = sys.platform == "darwin"
_gen_slots = threading.Semaphore(4)
_failed = set()
_locks = {}
_locks_guard = threading.Lock()


def reset():
    """Da chiamare quando si cambia catalogo (gli id delle foto ricominciano)."""
    _failed.clear()
    with _locks_guard:
        _locks.clear()


def thumb_file(cache_dir, photo_id, size):
    return os.path.join(cache_dir, str(photo_id % 1000).zfill(3), f"{photo_id}_{size}.jpg")


def _run(cmd, timeout=90):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=timeout).returncode == 0


def _make_mac(src, dst, px):
    tmp = tempfile.mkdtemp(prefix="fotocat_")
    try:
        try:
            _run(["qlmanage", "-t", "-s", str(px), "-o", tmp, src], timeout=60)
        except subprocess.TimeoutExpired:
            pass
        pngs = glob.glob(os.path.join(tmp, "*.png"))
        if pngs and _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                          pngs[0], "--out", dst]):
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                 "-Z", str(px), src, "--out", dst])


def _make_pillow(src, dst, px):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((px, px))
            im.convert("RGB").save(dst, "JPEG", quality=80)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- piena risoluzione (zoom 100%)
WEB_EXT = {"jpg", "jpeg", "jpe", "png", "gif", "webp"}
FULL_KEEP = 20          # quante immagini convertite tenere in cache
_full_slots = threading.Semaphore(2)


def get_full(cat, cache_dir, photo_id):
    """Percorso dell'immagine a piena risoluzione. I JPEG si mostrano così come sono;
    RAW, DNG, PSD e TIFF vengono convertiti in JPEG (e tenuti in una piccola cache)."""
    src = cat.photo_path(photo_id)
    if not src or not os.path.exists(src):
        return None
    if src.rsplit(".", 1)[-1].lower() in WEB_EXT:
        return src
    folder = os.path.join(cache_dir, "_piena_risoluzione")
    dst = os.path.join(folder, f"{photo_id}.jpg")
    if os.path.exists(dst):
        os.utime(dst)
        return dst
    os.makedirs(folder, exist_ok=True)
    part = dst + ".part.jpg"
    with _full_slots:
        if IS_MAC:
            ok = _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92", src, "--out", part], 180)
        else:
            ok = _make_pillow(src, part, 100000)
    if not ok or not os.path.exists(part):
        return None
    os.replace(part, dst)
    old = sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".jpg")),
                 key=os.path.getmtime, reverse=True)
    for f in old[FULL_KEEP:]:
        try:
            os.remove(f)
        except OSError:
            pass
    return dst


def get_thumb(cat, cache_dir, photo_id, size="m"):
    """Restituisce il percorso della miniatura, generandola se serve. None se impossibile."""
    dst = thumb_file(cache_dir, photo_id, size)
    if os.path.exists(dst):
        return dst
    if (photo_id, size) in _failed:
        return None
    with _locks_guard:
        lock = _locks.setdefault((photo_id, size), threading.Lock())
    with lock:
        if os.path.exists(dst):
            return dst
        src = cat.photo_path(photo_id)
        if not src or not os.path.exists(src):
            return None   # NAS non collegato o file spostato: non segnare come fallita
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        part = dst + ".part.jpg"
        with _gen_slots:
            ok = (_make_mac if IS_MAC else _make_pillow)(src, part, SIZES[size])
        if ok and os.path.exists(part):
            os.replace(part, dst)
            with _locks_guard:
                _locks.pop((photo_id, size), None)
            return dst
        if os.path.exists(part):
            os.remove(part)
        _failed.add((photo_id, size))
        return None
