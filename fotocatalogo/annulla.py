"""Annulla (⌘Z) e Ripeti (⇧⌘Z): le ultime operazioni sul catalogo e sui file.

Ogni operazione annullabile si registra con una funzione `fotografa()` che salva lo stato
di ciò che cambierà (righe del database, nomi o cartelle dei file) e restituisce la funzione
che lo rimette. Annullare: si fotografa lo stato attuale (servirà per ripetere), poi si
rimette quello di prima. Ripetere è lo stesso al contrario. Ogni voce ricorda anche dove è
stata fatta (vista e foto), così l'interfaccia ci torna.

Il registro sta in memoria e vale per il catalogo aperto: chiudendo l'app o cambiando
catalogo si svuota. Una nuova operazione svuota l'elenco di quelle da ripetere.
Non si annullano eliminazioni dal disco e conversioni.
"""
import os
import threading

MAX = 50
VISTA = threading.local()       # vista dell'interfaccia al momento della richiesta (server.do_POST)


class Voce:
    def __init__(self, etichetta, fotografa, rimetti, dove):
        self.etichetta, self.fotografa, self.rimetti, self.dove = etichetta, fotografa, rimetti, dove


class Registro:
    def __init__(self):
        self.lock = threading.Lock()
        self.cat = None
        self.annullabili, self.ripetibili = [], []

    def _controlla(self, cat):
        if cat is not self.cat:            # catalogo cambiato: si riparte da zero
            self.cat, self.annullabili, self.ripetibili = cat, [], []

    def aggiungi(self, cat, etichetta, fotografa, dove, prima=None):
        """Da chiamare PRIMA dell'operazione: si fotografa lo stato di adesso. Se l'operazione è
        già avvenuta (raccolta nuova, file già spostati) `prima` è la funzione che torna indietro."""
        with self.lock:
            self._controlla(cat)
            self.annullabili.append(Voce(etichetta, fotografa, prima or fotografa(), dove))
            del self.annullabili[:-MAX]
            self.ripetibili.clear()

    def stato(self, cat):
        with self.lock:
            self._controlla(cat)
            return {"undo": self.annullabili[-1].etichetta if self.annullabili else None,
                    "redo": self.ripetibili[-1].etichetta if self.ripetibili else None}

    def esegui(self, cat, ripeti=False):
        """Annulla (o ripete) l'ultima voce. Restituisce (voce, risultato di rimetti())."""
        with self.lock:
            self._controlla(cat)
            da, a = (self.ripetibili, self.annullabili) if ripeti else (self.annullabili, self.ripetibili)
            if not da:
                return None, None
            v = da.pop()
            dopo = v.fotografa()               # stato attuale: servirà per tornare qui
            rimetti, v.rimetti = v.rimetti, dopo
            a.append(v)
        return v, rimetti()


REGISTRO = Registro()


def _q(ids):
    return ",".join(str(int(i)) for i in ids) or "NULL"


# ---------------------------------------------------------------- istantanee del database
def righe(db, sql, args=()):
    return [tuple(r) for r in db.execute(sql, args).fetchall()]


def foto_colonne(cat, ids, colonne):
    """fotografa: alcune colonne di photos per queste foto."""
    def fotografa():
        rows = righe(cat.db, f"SELECT id, {', '.join(colonne)} FROM photos WHERE id IN ({_q(ids)})")

        def rimetti():
            with cat.write_lock:
                cat.db.executemany(f"UPDATE photos SET {', '.join(c + '=?' for c in colonne)} WHERE id=?",
                                   [r[1:] + (r[0],) for r in rows])
                cat.db.commit()
        return rimetti
    return fotografa


def tabella(tabella_, dove, args=()):
    """Parte di istantanea: le righe di una tabella che soddisfano `dove`. Rimettere cancella
    quelle presenti in quel momento (stessa condizione) e reinserisce quelle fotografate."""
    def fotografa(db):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({tabella_})")]
        rows = righe(db, f"SELECT {', '.join(cols)} FROM {tabella_} WHERE {dove}", args)

        def rimetti(db):
            db.execute(f"DELETE FROM {tabella_} WHERE {dove}", args)
            db.executemany(f"INSERT OR REPLACE INTO {tabella_}({', '.join(cols)}) "
                           f"VALUES({', '.join('?' * len(cols))})", rows)
        return rimetti
    return fotografa


def pile(cids):
    """Parte di istantanea: le pile delle raccolte indicate (copertine e componenti)."""
    q = _q(cids)

    def fotografa(db):
        stacks = righe(db, f"SELECT id, collection_id, cover_id, created_at FROM stacks WHERE collection_id IN ({q})")
        sp = righe(db, f"""SELECT stack_id, photo_id FROM stack_photos WHERE stack_id IN
                           (SELECT id FROM stacks WHERE collection_id IN ({q}))""")

        def rimetti(db):
            db.execute(f"DELETE FROM stack_photos WHERE stack_id IN (SELECT id FROM stacks WHERE collection_id IN ({q}))")
            db.execute(f"DELETE FROM stacks WHERE collection_id IN ({q})")
            db.executemany("INSERT OR REPLACE INTO stacks(id, collection_id, cover_id, created_at) VALUES(?,?,?,?)", stacks)
            db.executemany("INSERT OR IGNORE INTO stack_photos(stack_id, photo_id) VALUES(?,?)", sp)
        return rimetti
    return fotografa


def insieme(cat, *parti):
    """fotografa: più parti di istantanea, rimesse insieme in un'unica scrittura."""
    def fotografa():
        rimetti_parti = [p(cat.db) for p in parti]

        def rimetti():
            with cat.write_lock:
                for r in rimetti_parti:
                    r(cat.db)
                cat.db.commit()
        return rimetti
    return fotografa


# ---------------------------------------------------------------- file: nomi e cartelle
def stato_file(cat, ids):
    return {r[0]: (r[1], r[2]) for r in cat.db.execute(
        f"SELECT id, folder_id, filename FROM photos WHERE id IN ({_q(ids)})")}


def cambiati(cat, prima, indice):
    """Id i cui file hanno davvero cambiato cartella (indice 0) o nome (indice 1)."""
    adesso = stato_file(cat, prima)
    return [pid for pid, v in prima.items() if pid in adesso and adesso[pid][indice] != v[indice]]


def rimetti_file(cat, stato, indice, gestione_file, avvia_lavoro):
    """Funzione che riporta i file ai nomi (indice 1) o alle cartelle (indice 0) di `stato`,
    con un lavoro in background."""
    def rimetti():
        fn = _rinomina_a if indice == 1 else _sposta_a
        etichetta = "Rinomina dei file" if indice == 1 else "Spostamento dei file"
        return avvia_lavoro(etichetta, lambda job: fn(cat, stato, gestione_file, job))
    return rimetti


def file(cat, ids, indice, gestione_file, avvia_lavoro):
    """fotografa: nomi o cartelle attuali di questi file."""
    return lambda: rimetti_file(cat, stato_file(cat, ids), indice, gestione_file, avvia_lavoro)


def _rinomina_a(cat, stato, gestione_file, job):
    """Porta i file ai nomi di `stato`: a gruppi con lo stesso nome base (coppie JPG + RAW insieme)."""
    adesso = stato_file(cat, stato)
    gruppi = {}
    for pid, (fid, nome) in stato.items():
        if pid in adesso and adesso[pid][1] != nome:
            gruppi.setdefault((fid, os.path.splitext(nome)[0]), []).append(pid)
    res = {"renamed": True, "rinominate": 0, "saltate": [], "errori": [], "annullata": True}
    for (_, base), ids in gruppi.items():
        r = gestione_file.rename(cat, ids, {"name": base}, job)
        res["rinominate"] += r.get("rinominate", 0)
        res["saltate"] += r.get("saltate", [])
        res["errori"] += r.get("errori", [])
    return res


def _sposta_a(cat, stato, gestione_file, job):
    """Porta i file nelle cartelle di `stato`."""
    adesso = stato_file(cat, stato)
    gruppi = {}
    for pid, (fid, _) in stato.items():
        if pid in adesso and adesso[pid][0] != fid:
            gruppi.setdefault(fid, []).append(pid)
    res = {"moved": True, "spostate": 0, "saltate": [], "errori": [], "dest": "", "annullata": True}
    for fid, ids in gruppi.items():
        r = gestione_file.move(cat, ids, fid, job)
        res["spostate"] += r.get("spostate", 0)
        res["saltate"] += r.get("saltate", [])
        res["errori"] += r.get("errori", [])
    return res
