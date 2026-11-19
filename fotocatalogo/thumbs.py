"""Miniature, anteprime e immagini a piena risoluzione, salvate in cache sul Mac.

Su macOS usa gli strumenti di sistema:
  - qlmanage (QuickLook): veloce, legge RAW/PSD/HEIC usando l'anteprima
    incorporata e rispetta l'orientamento;
  - sips: per convertire in JPEG e ruotare, e come piano B;
  - fcimg (incluso nell'app): conversioni a piena qualità con orientamento,
    rotazione e ridimensionamento in un solo passaggio.
Su altri sistemi (solo per sviluppo) usa Pillow se installato.

La rotazione decisa nel catalogo (0/90/180/270, in senso orario) non modifica
mai i file: si applica alle copie in cache e alle esportazioni.

Preparazione in background: quando l'interfaccia carica una pagina di foto, le
loro miniature vengono create in anticipo da due lavoratori a bassa priorità,
così scorrendo sono già pronte. Le richieste di chi guarda passano davanti.
"""
import collections
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from catalog import VIDEO_EXT  # noqa: E402

SIZES = {"m": 480, "l": 2048}
IS_MAC = sys.platform == "darwin"
HERE = os.path.dirname(os.path.abspath(__file__))
_gen_slots = threading.Semaphore(4)       # richieste di chi sta guardando
_failed = set()
_locks = {}
_locks_guard = threading.Lock()


def find_imghelper():
    """Il convertitore 'fcimg' incluso nell'app per Mac (Contents/Resources/bin)."""
    for p in (os.environ.get("FOTOCAT_IMGHELPER"), os.path.join(HERE, "..", "bin", "fcimg")):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return os.path.abspath(p)
    return None


def reset():
    """Da chiamare quando si cambia catalogo (gli id delle foto ricominciano)."""
    _failed.clear()
    with _locks_guard:
        _locks.clear()
    PREFETCH.clear()


def thumb_file(cache_dir, photo_id, size, rot=0):
    suffix = f"_r{rot}" if rot else ""
    return os.path.join(cache_dir, str(photo_id % 1000).zfill(3), f"{photo_id}_{size}{suffix}.jpg")


def drop_cached(cache_dir, photo_id):
    """Cancella tutte le copie in cache di una foto (miniature, anteprime, ruotate, piena risoluzione)."""
    folder = os.path.dirname(thumb_file(cache_dir, photo_id, "m"))
    for f in glob.glob(os.path.join(folder, f"{photo_id}_*.jpg")) + \
            glob.glob(os.path.join(cache_dir, "_piena_risoluzione", f"{photo_id}.jpg")) + \
            glob.glob(os.path.join(cache_dir, "_piena_risoluzione", f"{photo_id}_r*.jpg")):
        try:
            os.remove(f)
        except OSError:
            pass


def _run(cmd, timeout=90):
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _make_mac(src, dst, px):
    tmp = tempfile.mkdtemp(prefix="fotocat_")
    try:
        _run(["qlmanage", "-t", "-s", str(px), "-o", tmp, src], timeout=60)
        pngs = glob.glob(os.path.join(tmp, "*.png"))
        if pngs and _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                          pngs[0], "--out", dst]):
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                 "-Z", str(px), src, "--out", dst])


def _make_pillow(src, dst, px, rot=0):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if rot:
                im = im.rotate(-rot, expand=True)
            im.thumbnail((px, px))
            im.convert("RGB").save(dst, "JPEG", quality=85)
        return True
    except Exception:
        return False


def _rotate_copy(src, dst, rot):
    """Ruota (in senso orario) una copia già orientata, es. una miniatura."""
    if IS_MAC:
        return _run(["sips", "-r", str(rot), src, "--out", dst], 60)
    return _make_pillow(src, dst, 100000, rot)


# ---------------------------------------------------------------- miniature e anteprime
def _base_thumb(cat, cache_dir, photo_id, size, slots):
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
        part = dst + f".{threading.get_ident()}.part.jpg"
        if slots:
            with slots:
                ok = (_make_mac if IS_MAC else _make_pillow)(src, part, SIZES[size])
        else:
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


def get_thumb(cat, cache_dir, photo_id, size="m", slots=_gen_slots):
    """Percorso della miniatura (già ruotata se serve), generandola se necessario."""
    base = _base_thumb(cat, cache_dir, photo_id, size, slots)
    rot = cat.rotation(photo_id) if base else 0
    if not base or not rot:
        return base
    dst = thumb_file(cache_dir, photo_id, size, rot)
    if not os.path.exists(dst):
        part = dst + f".{threading.get_ident()}.part.jpg"
        if not _rotate_copy(base, part, rot) or not os.path.exists(part):
            return base
        os.replace(part, dst)
    return dst


# ---------------------------------------------------------------- preparazione in background
class Prefetcher:
    WORKERS = 2
    MAX_QUEUE = 3000

    def __init__(self):
        self.q = collections.deque()
        self.queued = set()
        self.cv = threading.Condition()
        self.gen = 0
        self.started = False

    def _start(self):
        if not self.started:
            self.started = True
            for _ in range(self.WORKERS):
                threading.Thread(target=self._work, daemon=True).start()

    def add(self, cat, cache_dir, ids):
        """Le ultime richieste hanno la precedenza (sono le foto che stai per vedere)."""
        with self.cv:
            self._start()
            todo = [i for i in ids if i not in self.queued and (i, "m") not in _failed
                    and not os.path.exists(thumb_file(cache_dir, i, "m"))]
            for i in reversed(todo):
                self.q.appendleft((self.gen, cat, cache_dir, i))
                self.queued.add(i)
            while len(self.q) > self.MAX_QUEUE:
                self.queued.discard(self.q.pop()[3])
            self.cv.notify_all()

    def clear(self):
        with self.cv:
            self.gen += 1
            self.q.clear()
            self.queued.clear()

    def _work(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                gen, cat, cache_dir, pid = self.q.popleft()
                self.queued.discard(pid)
                if gen != self.gen:
                    continue
            try:
                _base_thumb(cat, cache_dir, pid, "m", None)
            except Exception:  # noqa: BLE001  (catalogo chiuso nel frattempo, NAS staccato…)
                pass


PREFETCH = Prefetcher()


# ---------------------------------------------------------------- piena risoluzione (zoom 100%)
WEB_EXT = {"jpg", "jpeg", "jpe", "png", "gif", "webp"}
FULL_KEEP = 20          # quante immagini convertite tenere in cache
_full_slots = threading.Semaphore(2)


def get_full(cat, cache_dir, photo_id):
    """Percorso dell'immagine a piena risoluzione. I JPEG non ruotati si mostrano così
    come sono; RAW, DNG, PSD, TIFF e le foto ruotate vengono convertiti in JPEG
    (e tenuti in una piccola cache)."""
    src = cat.photo_path(photo_id)
    if not src or not os.path.exists(src):
        return None
    ext = src.rsplit(".", 1)[-1].lower()
    if ext in VIDEO_EXT:
        return src            # i video si riproducono direttamente dal NAS
    rot = cat.rotation(photo_id)
    if ext in WEB_EXT and not rot:
        return src
    folder = os.path.join(cache_dir, "_piena_risoluzione")
    dst = os.path.join(folder, f"{photo_id}{f'_r{rot}' if rot else ''}.jpg")
    if os.path.exists(dst):
        os.utime(dst)
        return dst
    os.makedirs(folder, exist_ok=True)
    part = dst + ".part.jpg"
    helper = find_imghelper()
    with _full_slots:
        if helper:
            ok = _run([helper, "export", src, part, "--quality", "0.92", "--rotate", str(rot)], 180)
        elif IS_MAC:
            ok = _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92", src, "--out", part], 180)
            if ok and rot:
                ok = _run(["sips", "-r", str(rot), part, "--out", part], 120)
        else:
            ok = _make_pillow(src, part, 100000, rot)
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
