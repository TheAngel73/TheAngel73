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
così scorrendo sono già pronte.

Priorità: le miniature richieste da chi guarda passano davanti a tutto.
- Hanno posti riservati (_gen_slots) che la preparazione in background e il lavoro
  «Genera anteprime» non usano; questi ultimi si fermano finché c'è una richiesta
  di chi guarda in corso o in attesa.
- I posti si assegnano all'ultima richiesta arrivata (LIFO): cambiando cartella, le
  miniature della nuova vista passano davanti a quelle della vista precedente.
- Una richiesta il cui browser ha già rinunciato (cella sparita) viene scartata
  prima di generare (alive).
"""
import collections
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from time import monotonic as time_now

from catalog import VIDEO_EXT  # noqa: E402

SIZES = {"m": 480, "l": 2048}
IS_MAC = sys.platform == "darwin"
HERE = os.path.dirname(os.path.abspath(__file__))
class LifoSlots:
    """Come un semaforo, ma il posto libero va all'ultima richiesta arrivata.
    busy conta le richieste in corso o in attesa (per fermare il lavoro in background)."""

    def __init__(self, n):
        self.free = n
        self.busy = 0
        self.lock = threading.Lock()
        self.waiters = []

    def __enter__(self):
        with self.lock:
            self.busy += 1
            if self.free > 0 and not self.waiters:
                self.free -= 1
                return self
            ev = threading.Event()
            self.waiters.append(ev)
        ev.wait()                       # il posto viene passato da chi esce
        return self

    def __exit__(self, *exc):
        with self.lock:
            self.busy -= 1
            if self.waiters:
                self.waiters.pop().set()
            else:
                self.free += 1


_gen_slots = LifoSlots(4)                 # richieste di chi sta guardando
BG_SLOTS = threading.Semaphore(3)         # lavoro «Genera anteprime»


def wait_viewer_idle(limit=3.0):
    """Il lavoro in background aspetta che chi guarda abbia ricevuto le sue miniature,
    ma al massimo `limit` secondi: non deve mai fermarsi del tutto (es. una miniatura
    che non si riesce a creare e che la pagina continua a chiedere)."""
    end = time_now() + limit
    while (_gen_slots.busy or PREFETCH.busy()) and time_now() < end:
        threading.Event().wait(0.15)

_failed = set()
_unavailable = {}      # id -> momento: file non raggiungibile (NAS?), si riprova dopo UNAVAILABLE_S
UNAVAILABLE_S = 300
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
    _unavailable.clear()
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


# solo per le prove fuori dal Mac: simula un NAS lento (secondi per miniatura)
_TEST_DELAY = float(os.environ.get("FOTOCAT_TEST_THUMB_DELAY", "0") or 0)


def _make_pillow(src, dst, px, rot=0):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    if _TEST_DELAY:
        threading.Event().wait(_TEST_DELAY)
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
def _base_thumb(cat, cache_dir, photo_id, size, slots, alive=None):
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
            _unavailable[photo_id] = time_now()   # NAS non collegato o file spostato:
            return None                           # non è un errore definitivo, si riproverà
        _unavailable.pop(photo_id, None)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        part = dst + f".{threading.get_ident()}.part.jpg"
        if slots:
            with slots:
                if alive and not alive():
                    return None          # chi l'ha chiesta non la aspetta più
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


def get_thumb(cat, cache_dir, photo_id, size="m", slots=_gen_slots, alive=None):
    """Percorso della miniatura (già ruotata se serve), generandola se necessario."""
    base = _base_thumb(cat, cache_dir, photo_id, size, slots, alive)
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
    """Generatore delle miniature della griglia, con due code:
    - prio: le miniature mancanti che sono sullo schermo ADESSO. La pagina la sostituisce
      a ogni cambio di vista o scorrimento (want), quindi si lavora sempre su ciò che si
      sta guardando;
    - q: tutte le altre della vista e delle pagine caricate, in background (le più recenti
      davanti). Al massimo BG_MAX lavoratori e solo quando prio è vuota.
    """
    WORKERS = 4
    BG_MAX = 2
    MAX_QUEUE = 3000

    def __init__(self):
        self.q = collections.deque()
        self.queued = set()
        self.prio = collections.deque()
        self.bg_active = 0
        self.cv = threading.Condition()
        self.done_cv = threading.Condition()
        self.gen = 0
        self.want_seq = 0
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

    @staticmethod
    def state(cache_dir, pid):
        if os.path.exists(thumb_file(cache_dir, pid, "m")):
            return 1
        if (pid, "m") in _failed:
            return -1
        t = _unavailable.get(pid)
        if t is not None and time_now() - t < UNAVAILABLE_S:
            return -1                      # per ora non si può: la pagina smette di chiederla
        return 0

    def want(self, cat, cache_dir, ids, timeout=8.0):
        """Le miniature sullo schermo: sostituisce la coda prioritaria e aspetta (al massimo
        timeout secondi) che almeno una sia pronta. Restituisce lo stato di ciascuna:
        1 pronta, -1 impossibile da creare, 0 non ancora."""
        todo = [i for i in ids if self.state(cache_dir, i) == 0]
        with self.cv:
            self._start()
            self.prio = collections.deque((self.gen, cat, cache_dir, i) for i in todo)
            self.cv.notify_all()
        with self.done_cv:
            self.want_seq += 1               # la richiesta precedente risponde subito
            mine = self.want_seq
            self.done_cv.notify_all()
        end = time_now() + timeout
        with self.done_cv:
            while todo and self.want_seq == mine and all(self.state(cache_dir, i) == 0 for i in todo):
                left = end - time_now()
                if left <= 0:
                    break
                self.done_cv.wait(min(left, 1.0))
        return {i: self.state(cache_dir, i) for i in ids}

    def busy(self):
        return bool(self.prio)

    def clear(self):
        with self.cv:
            self.gen += 1
            self.q.clear()
            self.queued.clear()
            self.prio.clear()

    def _work(self):
        while True:
            with self.cv:
                while True:
                    if self.prio:
                        gen, cat, cache_dir, pid = self.prio.popleft()
                        background = False
                        break
                    if self.q and self.bg_active < self.BG_MAX:
                        gen, cat, cache_dir, pid = self.q.popleft()
                        self.queued.discard(pid)
                        background = True
                        self.bg_active += 1
                        break
                    self.cv.wait()
            try:
                if gen == self.gen:
                    if background:
                        wait_viewer_idle()
                    _base_thumb(cat, cache_dir, pid, "m", None)
            except Exception:  # noqa: BLE001  (catalogo chiuso nel frattempo, NAS staccato…)
                pass
            finally:
                with self.cv:
                    if background:
                        self.bg_active -= 1
                    self.cv.notify_all()
                with self.done_cv:
                    self.done_cv.notify_all()


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
