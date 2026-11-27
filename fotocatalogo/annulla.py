"""Annulla (⌘Z): le ultime operazioni sul catalogo e sui file, dalla più recente.

Prima di ogni operazione annullabile si fotografa lo stato che cambierà (righe del database,
nomi o cartelle dei file); «annulla» lo rimette com'era. Il registro sta in memoria, vale
per il catalogo aperto e tiene le ultime MAX operazioni: chiudendo l'app o cambiando
catalogo si svuota. Non si annullano eliminazioni dal disco e conversioni.
"""
import os
import threading

MAX = 50


class Registro:
    def __init__(self):
        self.lock = threading.Lock()
        self.cat = None
        self.voci = []          # [(etichetta, funzione che annulla)]

    def _voci(self, cat):
        if cat is not self.cat:            # catalogo cambiato: si riparte da zero
            self.cat, self.voci = cat, []
        return self.voci

    def aggiungi(self, cat, etichetta, annulla):
        with self.lock:
            v = self._voci(cat)
            v.append((etichetta, annulla))
            del v[:-MAX]

    def ultima(self, cat):
        with self.lock:
            v = self._voci(cat)
            return v[-1][0] if v else None

    def togli(self, cat):
        with self.lock:
            v = self._voci(cat)
            return v.pop() if v else None


REGISTRO = Registro()


def _q(ids):
    return ",".join(str(int(i)) for i in ids) or "NULL"


# ---------------------------------------------------------------- istantanee del database
def righe(db, sql, args=()):
    return [tuple(r) for r in db.execute(sql, args).fetchall()]


def foto_colonne(cat, ids, colonne):
    """Valori attuali di alcune colonne di photos; restituisce la funzione che li rimette."""
    rows = righe(cat.db, f"SELECT id, {', '.join(colonne)} FROM photos WHERE id IN ({_q(ids)})")

    def annulla():
        with cat.write_lock:
            cat.db.executemany(f"UPDATE photos SET {', '.join(c + '=?' for c in colonne)} WHERE id=?",
                               [r[1:] + (r[0],) for r in rows])
            cat.db.commit()
    return annulla


def tabella(cat, tabella_, dove, args=()):
    """Righe di una tabella che soddisfano `dove`: annullare cancella quelle presenti allora
    (stessa condizione) e rimette le vecchie."""
    cols = [r[1] for r in cat.db.execute(f"PRAGMA table_info({tabella_})")]
    rows = righe(cat.db, f"SELECT {', '.join(cols)} FROM {tabella_} WHERE {dove}", args)

    def annulla(db):
        db.execute(f"DELETE FROM {tabella_} WHERE {dove}", args)
        db.executemany(f"INSERT OR REPLACE INTO {tabella_}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                       rows)
    return annulla


def pile(cat, cids):
    """Pile delle raccolte indicate (copertine e componenti)."""
    q = _q(cids)
    stacks = righe(cat.db, f"SELECT id, collection_id, cover_id, created_at FROM stacks WHERE collection_id IN ({q})")
    sp = righe(cat.db, f"""SELECT stack_id, photo_id FROM stack_photos WHERE stack_id IN
                           (SELECT id FROM stacks WHERE collection_id IN ({q}))""")

    def annulla(db):
        db.execute(f"DELETE FROM stack_photos WHERE stack_id IN (SELECT id FROM stacks WHERE collection_id IN ({q}))")
        db.execute(f"DELETE FROM stacks WHERE collection_id IN ({q})")
        db.executemany("INSERT OR REPLACE INTO stacks(id, collection_id, cover_id, created_at) VALUES(?,?,?,?)", stacks)
        db.executemany("INSERT OR IGNORE INTO stack_photos(stack_id, photo_id) VALUES(?,?)", sp)
    return annulla


def insieme(cat, *parti):
    """Più istantanee da rimettere insieme, in un'unica scrittura."""
    def annulla():
        with cat.write_lock:
            for p in parti:
                p(cat.db)
            cat.db.commit()
    return annulla


# ---------------------------------------------------------------- file: nomi e cartelle
def stato_file(cat, ids):
    return {r[0]: (r[1], r[2]) for r in cat.db.execute(
        f"SELECT id, folder_id, filename FROM photos WHERE id IN ({_q(ids)})")}


def rinomina_inversa(cat, prima, gestione_file, job):
    """Rimette i vecchi nomi: a gruppi con lo stesso nome base (coppie JPG + RAW insieme)."""
    adesso = stato_file(cat, prima)
    gruppi = {}
    for pid, (fid, nome) in prima.items():
        if pid in adesso and adesso[pid][1] != nome:
            gruppi.setdefault((fid, os.path.splitext(nome)[0]), []).append(pid)
    res = {"renamed": True, "rinominate": 0, "saltate": [], "errori": [], "annullata": True}
    for (_, base), ids in gruppi.items():
        r = gestione_file.rename(cat, ids, {"name": base}, job)
        res["rinominate"] += r.get("rinominate", 0)
        res["saltate"] += r.get("saltate", [])
        res["errori"] += r.get("errori", [])
    return res


def sposta_inverso(cat, prima, gestione_file, job):
    """Riporta i file nelle cartelle di prima."""
    adesso = stato_file(cat, prima)
    gruppi = {}
    for pid, (fid, _) in prima.items():
        if pid in adesso and adesso[pid][0] != fid:
            gruppi.setdefault(fid, []).append(pid)
    res = {"moved": True, "spostate": 0, "saltate": [], "errori": [], "dest": "cartelle di prima", "annullata": True}
    for fid, ids in gruppi.items():
        r = gestione_file.move(cat, ids, fid, job)
        res["spostate"] += r.get("spostate", 0)
        res["saltate"] += r.get("saltate", [])
        res["errori"] += r.get("errori", [])
    return res


def cambiati(cat, prima, indice):
    """Id i cui file hanno davvero cambiato nome (indice 1) o cartella (indice 0)."""
    adesso = stato_file(cat, prima)
    return {pid: v for pid, v in prima.items() if pid in adesso and adesso[pid][indice] != v[indice]}
