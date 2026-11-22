"""Nomi dei luoghi dalle coordinate GPS, senza internet.

Dati: dati/luoghi.tsv.gz (GeoNames tramite all-the-cities: località con almeno 1000
abitanti, CC BY 4.0) e dati/paesi.json (nomi dei paesi in italiano). Si cerca la località
più vicina; il risultato è «Cuceglio, Piemonte» in Italia e «Reykjavík, Islanda» altrove.
Lontano da tutto (oltre MAX_KM) si dice «vicino a …».
Generati con strumenti/crea_luoghi.js.
"""
import gzip
import json
import math
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "dati")
VERSION = "3"            # cambiandolo i nomi già salvati nel catalogo vengono ricalcolati
MAX_KM = 15

REGIONI = {"01": "Abruzzo", "02": "Basilicata", "03": "Calabria", "04": "Campania", "05": "Emilia-Romagna",
           "06": "Friuli-Venezia Giulia", "07": "Lazio", "08": "Liguria", "09": "Lombardia", "10": "Marche",
           "11": "Molise", "12": "Piemonte", "13": "Puglia", "14": "Sardegna", "15": "Sicilia", "16": "Toscana",
           "17": "Trentino-Alto Adige", "18": "Umbria", "19": "Valle d'Aosta", "20": "Veneto"}

# GeoNames usa i nomi inglesi per le città più note: qui quelli italiani
ITALIANO = {
    "Turin": "Torino", "Milan": "Milano", "Rome": "Roma", "Naples": "Napoli", "Florence": "Firenze",
    "Venice": "Venezia", "Genoa": "Genova", "Mantova": "Mantova", "Padua": "Padova", "Syracuse": "Siracusa",
    "Leghorn": "Livorno", "Bolzano-Bozen": "Bolzano", "Bozen": "Bolzano", "Brixen": "Bressanone",
    "Meran": "Merano", "Bruneck": "Brunico", "Sterzing": "Vipiteno", "Aosta": "Aosta",
    "Paris": "Parigi", "London": "Londra", "Munich": "Monaco di Baviera", "Vienna": "Vienna",
    "Lisbon": "Lisbona", "Brussels": "Bruxelles", "Athens": "Atene", "Prague": "Praga",
    "Warsaw": "Varsavia", "Moscow": "Mosca", "Saint Petersburg": "San Pietroburgo", "Geneva": "Ginevra",
    "Zurich": "Zurigo", "Lucerne": "Lucerna", "Basel": "Basilea", "Bern": "Berna", "Cologne": "Colonia",
    "Nice": "Nizza", "Marseille": "Marsiglia", "Lyon": "Lione", "Toulouse": "Tolosa", "Seville": "Siviglia",
    "Copenhagen": "Copenaghen", "Stockholm": "Stoccolma", "Edinburgh": "Edimburgo", "Dublin": "Dublino",
    "The Hague": "L'Aia", "Antwerp": "Anversa", "Bruges": "Bruges", "Ghent": "Gand", "Belgrade": "Belgrado",
    "Bucharest": "Bucarest", "Cracow": "Cracovia", "Krakow": "Cracovia", "Kiev": "Kiev", "Kyiv": "Kiev",
    "Istanbul": "Istanbul", "Cairo": "Il Cairo", "Jerusalem": "Gerusalemme", "Beijing": "Pechino",
    "Tokyo": "Tokyo", "New York City": "New York", "Mexico City": "Città del Messico",
    "Cape Town": "Città del Capo", "Havana": "L'Avana", "Algiers": "Algeri", "Tunis": "Tunisi",
    "Valletta": "La Valletta", "Vatican City": "Città del Vaticano", "San Marino": "San Marino",
    "Monaco": "Monaco", "Luxembourg": "Lussemburgo", "Andorra la Vella": "Andorra la Vella", "Ljubljana": "Lubiana", "Zagreb": "Zagabria", "Split": "Spalato",
    "Dubrovnik": "Ragusa", "Rijeka": "Fiume", "Pula": "Pola", "Koper": "Capodistria", "Piran": "Pirano",
    "Corfu": "Corfù", "Crete": "Creta", "Rhodes": "Rodi", "Santorini": "Santorini",
}

BREVI = {"US": "Stati Uniti", "GB": "Regno Unito", "AE": "Emirati Arabi", "VA": "Città del Vaticano"}

_lock = threading.Lock()
_data = None          # (nomi, lat, lon, paesi, regioni, griglia)
_paesi = {}


def _load():
    global _data, _paesi
    with _lock:
        if _data is not None:
            return _data
        names, lat, lon, cc, adm = [], [], [], [], []
        grid = {}
        with gzip.open(os.path.join(DATA, "luoghi.tsv.gz"), "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                n, la, lo, c, a, _pop = line.rstrip("\n").split("\t")
                la, lo = float(la), float(lo)
                names.append(n); lat.append(la); lon.append(lo); cc.append(c); adm.append(a)
                grid.setdefault((math.floor(la), math.floor(lo)), []).append(i)
        try:
            with open(os.path.join(DATA, "paesi.json"), encoding="utf-8") as f:
                _paesi = json.load(f)
        except (OSError, ValueError):
            _paesi = {}
        _data = (names, lat, lon, cc, adm, grid)
        return _data


def nearest(la, lo):
    """(indice, km) della località più vicina, oppure None se non ce n'è entro ~2°."""
    names, lat, lon, cc, adm, grid = _load()
    best, best_d = None, None
    cy, cx = math.floor(la), math.floor(lo)
    k = max(0.2, math.cos(math.radians(la)))
    for dy in (-1, 0, 1):
        for dx in (-2, -1, 0, 1, 2):
            x = (cx + dx + 180) % 360 - 180
            for i in grid.get((cy + dy, x), ()):
                dlo = (lon[i] - lo + 540) % 360 - 180
                d = (lat[i] - la) ** 2 + (dlo * k) ** 2
                if best_d is None or d < best_d:
                    best, best_d = i, d
    if best is None:
        return None
    return best, math.sqrt(best_d) * 111.2


def place_name(la, lo):
    """«Cuceglio, Piemonte» / «Reykjavík, Islanda» / «vicino a …», oppure None."""
    if la is None or lo is None:
        return None
    try:
        r = nearest(float(la), float(lo))
    except (OSError, ValueError):
        return None
    if not r:
        return None
    i, km = r
    names, _lat, _lon, cc, adm, _grid = _data
    name = ITALIANO.get(names[i], names[i])
    where = REGIONI.get(adm[i]) if cc[i] == "IT" else BREVI.get(cc[i]) or _paesi.get(cc[i], cc[i])
    label = f"{name}, {where}" if where and where != name else name
    return label if km <= MAX_KM else f"vicino a {label}"
