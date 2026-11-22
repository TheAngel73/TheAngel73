"""Crea static/mondo.json (contorni del mondo per la mappa dei luoghi) da un file
TopoJSON di world-atlas (Natural Earth, pubblico dominio; world-atlas: licenza ISC).

Uso (una volta, non serve all'app):
    npm pack world-atlas@2.0.2 && tar xzf world-atlas-2.0.2.tgz
    python3 -I strumenti/crea_mappa.py package/countries-50m.json static/mondo.json
    python3 -I strumenti/crea_mappa.py package/countries-10m.json static/mondo-dettaglio.json 65536

Uscita: proiezione di Mercatore in un quadrato di W×W unità intere (x da ovest a est,
y da nord a sud), come le mappe web. "land" sono i paesi (riempimento), "borders" i
confini interni (archi condivisi da due paesi). L'Antartide è esclusa.
"""
import json
import math
import sys

W = 8192
MAX_LAT = 85.0


def project(lon, lat):
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    x = (lon + 180) / 360 * W
    y = (1 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi) / 2 * W
    return round(x), round(y)


def unwrap(pts):
    """Longitudini continue: chi attraversa la linea del cambio di data (Russia orientale,
    Figi…) non deve saltare da un bordo all'altro della mappa, disegnando una striscia."""
    out, off, prev = [], 0.0, None
    for lon, lat in pts:
        if prev is not None:
            d = lon + off - prev
            if d > 180:
                off -= 360
            elif d < -180:
                off += 360
        out.append((lon + off, lat))
        prev = lon + off
    return out


def copies(pts):
    """La figura e, se esce dal bordo della mappa, la sua copia dall'altra parte."""
    res = [pts]
    lons = [p[0] for p in pts]
    if max(lons) > 180:
        res.append([(lo - 360, la) for lo, la in pts])
    if min(lons) < -180:
        res.append([(lo + 360, la) for lo, la in pts])
    return res


def projected(pts):
    out, last = [], None
    for lon, lat in pts:
        p = project(lon, lat)
        if p != last:
            out.append(p)
            last = p
    return out


def path_of(pts, close):
    """Percorso SVG compatto: primo punto assoluto, poi spostamenti relativi ("l")."""
    (x0, y0), rest = pts[0], pts[1:]
    out, px, py = [f"M{x0} {y0}l"], x0, y0
    for x, y in rest:
        out.append(f"{x - px} {y - py} ")
        px, py = x, y
    return "".join(out).rstrip() + ("z" if close else "")


def main(src, dst):
    topo = json.load(open(src))
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def arc_points(i):
        return arcs[i] if i >= 0 else arcs[~i][::-1]

    def ring_path(ring):
        pts = []
        for i in ring:
            seg = arc_points(i)
            pts.extend(seg if not pts else seg[1:])
        res = []
        for c in copies(unwrap(pts)):
            out = projected(c)
            if len(out) >= 3:
                res.append(path_of(out, True))
        return "".join(res)

    geoms = topo["objects"]["countries"]["geometries"]
    land, uses = [], {}
    for g in geoms:
        if str(g.get("id")) == "010":           # Antartide
            continue
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]] if g["type"] == "Polygon" else []
        for poly in polys:
            for ring in poly:
                land.append(ring_path(ring))
                for i in ring:
                    k = i if i >= 0 else ~i
                    uses.setdefault(k, set()).add(id(g))
    borders = []
    for k, who in uses.items():
        if len(who) > 1:
            for c in copies(unwrap(arcs[k])):
                pts = projected(c)
                if len(pts) > 1:
                    borders.append(path_of(pts, False))
    out = {"w": W, "fonte": "Natural Earth (pubblico dominio) tramite world-atlas 2.0.2 (ISC)",
           "land": "".join(land), "borders": "".join(borders)}
    with open(dst, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"{dst}: {len(out['land']) + len(out['borders'])} caratteri")


if __name__ == "__main__":
    if len(sys.argv) > 3:
        W = int(sys.argv[3])         # griglia più fine per i contorni dettagliati
    main(sys.argv[1], sys.argv[2])
