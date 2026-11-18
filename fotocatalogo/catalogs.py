"""Gestione di più cataloghi, come in Lightroom.

Ogni catalogo è una cartella in ~/Documents/FotoCatalogo/<Nome>/ che contiene
il database (catalogo.db) e le miniature.

Un catalogo protetto da password è invece un'immagine disco cifrata di macOS
(<Nome>.sparsebundle, AES-256, creata con lo strumento di sistema hdiutil).
Si sblocca solo con la password: finché è bloccato, raccolte, parole chiave e
miniature non sono leggibili da nessuno. Se la password viene dimenticata, il
catalogo non è recuperabile.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

from catalog import Catalog

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    SUPPORT = os.path.expanduser("~/Library/Application Support/FotoCatalogo")
    DEFAULT_BASE = os.path.expanduser("~/Documents/FotoCatalogo")
else:
    SUPPORT = os.path.expanduser("~/.fotocatalogo")
    DEFAULT_BASE = os.path.expanduser("~/FotoCatalogo")
SUPPORT = os.environ.get("FOTOCAT_DATA", SUPPORT)
BASE = os.environ.get("FOTOCAT_BASE", DEFAULT_BASE)
SETTINGS = os.path.join(SUPPORT, "impostazioni.json")
MOUNTS = os.path.join(SUPPORT, "sbloccati")
DB_NAME = "catalogo.db"
BUNDLE_EXT = ".sparsebundle"
HDIUTIL = os.environ.get("FOTOCAT_HDIUTIL", "hdiutil")


class CatalogError(Exception):
    pass


# ---------------------------------------------------------------- impostazioni
def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    os.makedirs(SUPPORT, exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS)


def uid(path):
    return hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:10]


def is_protected(path):
    return path.endswith(BUNDLE_EXT)


def display_name(path):
    n = os.path.basename(path.rstrip("/"))
    return n[:-len(BUNDLE_EXT)] if n.endswith(BUNDLE_EXT) else n


def clean_name(name):
    name = re.sub(r'[/:\\\0]', "-", (name or "").strip()).strip(". ")
    if not name:
        raise CatalogError("Scrivi un nome per il catalogo")
    return name[:80]


# ---------------------------------------------------------------- elenco
def list_catalogs():
    s = load_settings()
    paths = set()
    if os.path.isdir(BASE):
        for n in os.listdir(BASE):
            p = os.path.join(BASE, n)
            if n.endswith(BUNDLE_EXT) or os.path.isfile(os.path.join(p, DB_NAME)):
                paths.add(p)
    for p in s.get("known", []):
        if os.path.exists(p):
            paths.add(p)
    return sorted(paths, key=lambda p: display_name(p).lower())


def remember(path, **extra):
    s = load_settings()
    known = [p for p in s.get("known", []) if p != path and os.path.exists(p)]
    if not path.startswith(BASE + os.sep):
        known.append(path)          # catalogo fuori dalla cartella standard
    s["known"] = known
    s["last"] = path
    s.update(extra)
    save_settings(s)


def startup_catalog():
    """Il catalogo da aprire all'avvio: il predefinito, altrimenti l'ultimo usato."""
    s = load_settings()
    for key in ("default", "last"):
        p = s.get(key)
        if p and os.path.exists(p):
            return p
    found = list_catalogs()
    return found[0] if found else None


# ---------------------------------------------------------------- migrazione dalla versione precedente
def migrate_old():
    """Sposta il catalogo della prima versione (in Application Support) in Documenti."""
    old_db = os.path.join(SUPPORT, DB_NAME)
    if not os.path.exists(old_db):
        return None
    dest = os.path.join(BASE, "Catalogo principale")
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(BASE, f"Catalogo principale {n}")
        n += 1
    os.makedirs(dest)
    # consolida il file di lavoro (WAL) nel database prima di spostarlo
    try:
        c = sqlite3.connect(old_db)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
    except sqlite3.Error:
        pass
    for name in (DB_NAME, DB_NAME + "-wal", DB_NAME + "-shm", "miniature"):
        src = os.path.join(SUPPORT, name)
        if os.path.exists(src):
            shutil.move(src, os.path.join(dest, name))
    s = load_settings()
    s.setdefault("default", dest)
    s["last"] = dest
    save_settings(s)
    return dest


# ---------------------------------------------------------------- immagini cifrate (hdiutil)
def _hdiutil(args, password_bytes=b""):
    try:
        r = subprocess.run([HDIUTIL] + args, input=password_bytes, capture_output=True, timeout=180)
    except FileNotFoundError:
        raise CatalogError("La protezione con password è disponibile solo su macOS")
    return r.returncode, (r.stderr or b"").decode("utf-8", "ignore") + (r.stdout or b"").decode("utf-8", "ignore")


def _mountpoint(path):
    return os.path.join(MOUNTS, uid(path))


def attach(path, password):
    mp = _mountpoint(path)
    if os.path.isfile(os.path.join(mp, DB_NAME)):
        detach(path)   # residuo di una chiusura improvvisa: si richiede comunque la password
    os.makedirs(mp, exist_ok=True)
    code, out = _hdiutil(["attach", path, "-stdinpass", "-nobrowse", "-noautoopen", "-noverify",
                          "-mountpoint", mp], password.encode() + b"\0")
    if code != 0:
        try:
            os.rmdir(mp)
        except OSError:
            pass
        if "uthentication" in out or "assword" in out:
            raise CatalogError("Password errata")
        raise CatalogError("Impossibile aprire il catalogo protetto: " + out.strip()[:200])
    return mp


def detach(path):
    mp = _mountpoint(path)
    if not os.path.exists(mp):
        return
    code, _ = _hdiutil(["detach", mp, "-quiet"])
    if code != 0:
        _hdiutil(["detach", mp, "-force", "-quiet"])
    try:
        os.rmdir(mp)
    except OSError:
        pass


def lock_leftovers():
    """All'avvio: richiude i cataloghi rimasti sbloccati (es. dopo un blocco del Mac)."""
    if not os.path.isdir(MOUNTS):
        return
    for n in os.listdir(MOUNTS):
        mp = os.path.join(MOUNTS, n)
        _hdiutil(["detach", mp, "-force", "-quiet"])
        try:
            os.rmdir(mp)
        except OSError:
            pass


def create_encrypted(path, name, password):
    if len(password) < 4:
        raise CatalogError("La password deve avere almeno 4 caratteri")
    code, out = _hdiutil(["create", "-size", "200g", "-type", "SPARSEBUNDLE", "-fs", "APFS",
                          "-volname", name, "-encryption", "AES-256", "-stdinpass", path],
                         password.encode() + b"\0")
    if code != 0:
        raise CatalogError("Creazione del catalogo protetto non riuscita: " + out.strip()[:200])


def change_password(path, old, new):
    if len(new) < 4:
        raise CatalogError("La nuova password deve avere almeno 4 caratteri")
    code, out = _hdiutil(["chpass", path, "-oldstdinpass", "-newstdinpass"],
                         old.encode() + b"\0" + new.encode() + b"\0")
    if code != 0:
        raise CatalogError("Password attuale errata" if "uthentication" in out else
                           "Cambio password non riuscito: " + out.strip()[:200])


# ---------------------------------------------------------------- catalogo aperto
class OpenCatalog:
    def __init__(self, path, password=None):
        self.path = path
        self.protected = is_protected(path)
        self.name = display_name(path)
        self.uid = uid(path)
        if self.protected:
            if not password:
                raise CatalogError("Questo catalogo è protetto: serve la password")
            self.folder = attach(path, password)
            # impronta (non la password) per verificare la password attuale prima di cambiarla
            self._salt = os.urandom(16)
            self._pwhash = hashlib.pbkdf2_hmac("sha256", password.encode(), self._salt, 100_000)
        else:
            self.folder = path
        os.makedirs(self.folder, exist_ok=True)
        self.thumb_dir = os.path.join(self.folder, "miniature")
        os.makedirs(self.thumb_dir, exist_ok=True)
        self.cat = Catalog(os.path.join(self.folder, DB_NAME))

    def check_password(self, password):
        return self.protected and hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode(), self._salt, 100_000) == self._pwhash

    def info(self):
        return {"path": self.path, "name": self.name, "protected": self.protected, "uid": self.uid}

    def close(self):
        self.cat.close()
        if self.protected:
            detach(self.path)


def new_catalog_path(name, protected):
    name = clean_name(name)
    os.makedirs(BASE, exist_ok=True)
    path = os.path.join(BASE, name + (BUNDLE_EXT if protected else ""))
    other = os.path.join(BASE, name + ("" if protected else BUNDLE_EXT))
    if os.path.exists(path) or os.path.exists(other):
        raise CatalogError(f"Esiste già un catalogo chiamato «{name}»")
    return name, path


def create(name, password=None):
    name, path = new_catalog_path(name, bool(password))
    if password:
        create_encrypted(path, name, password)
    else:
        os.makedirs(path)
    return path


def protect_existing(oc, password):
    """Trasforma un catalogo normale (già chiuso) in catalogo protetto.
    Copia tutto nell'immagine cifrata, verifica la copia e poi cancella l'originale."""
    src = oc.path
    name = display_name(src)
    dest = src + BUNDLE_EXT
    if os.path.exists(dest):
        raise CatalogError("Esiste già un catalogo protetto con questo nome")
    create_encrypted(dest, name, password)
    mp = attach(dest, password)
    try:
        c = sqlite3.connect(os.path.join(src, DB_NAME))
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        n_src = c.execute("SELECT count(*) FROM photos").fetchone()[0]
        c.close()
        for entry in os.listdir(src):
            s, d = os.path.join(src, entry), os.path.join(mp, entry)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        c = sqlite3.connect(os.path.join(mp, DB_NAME))
        n_dst = c.execute("SELECT count(*) FROM photos").fetchone()[0]
        c.close()
        if n_dst != n_src:
            raise CatalogError("Verifica della copia non riuscita: l'originale non è stato toccato")
    except Exception:
        detach(dest)
        shutil.rmtree(dest, ignore_errors=True)
        raise
    detach(dest)
    shutil.rmtree(src)          # l'originale non cifrato non deve restare sul disco
    s = load_settings()
    for k in ("default", "last"):
        if s.get(k) == src:
            s[k] = dest
    s["known"] = [dest if p == src else p for p in s.get("known", [])]
    save_settings(s)
    return dest
