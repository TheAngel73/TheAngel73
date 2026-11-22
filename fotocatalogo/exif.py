"""Lettore EXIF minimo, senza librerie esterne.

Legge data di scatto, orientamento, dimensioni e posizione GPS da JPEG e dai RAW
basati su TIFF (NEF, CR2, ARW, DNG, ORF, RW2, PEF, TIFF...), e data e posizione dai
video MP4/MOV. Legge solo l'inizio del file, quindi è veloce anche via rete sul NAS.
"""
import re
import struct

HEAD_BYTES = 256 * 1024


def _parse_tiff(buf, shooting=False):
    out = {}
    if len(buf) < 8:
        return out
    if buf[:2] == b"II":
        e = "<"
    elif buf[:2] == b"MM":
        e = ">"
    else:
        return out

    def u16(o):
        return struct.unpack_from(e + "H", buf, o)[0]

    def u32(o):
        return struct.unpack_from(e + "I", buf, o)[0]

    def read_ifd(off):
        tags = {}
        if off <= 0 or off + 2 > len(buf):
            return tags
        n = u16(off)
        for i in range(min(n, 500)):
            p = off + 2 + i * 12
            if p + 12 > len(buf):
                break
            tag, typ, cnt = u16(p), u16(p + 2), u32(p + 4)
            if typ == 3 and cnt == 1:
                val = u16(p + 8)
            elif typ == 4 and cnt == 1:
                val = u32(p + 8)
            elif typ == 2:
                vo = p + 8 if cnt <= 4 else u32(p + 8)
                val = buf[vo:vo + cnt].split(b"\0")[0].decode("utf-8", "ignore").strip()
            elif typ in (5, 10) and cnt >= 1:          # frazioni (tempo, apertura, focale, GPS)
                vo = u32(p + 8)
                vals = []
                for k in range(min(cnt, 3)):
                    if vo + 8 * k + 8 > len(buf):
                        break
                    num, den = struct.unpack_from(e + ("II" if typ == 5 else "ii"), buf, vo + 8 * k)
                    vals.append(num / den if den else 0.0)
                val = (vals[0] if cnt == 1 else vals) if vals else None
            elif typ == 3 and cnt > 1:
                val = u16(p + 8)
            else:
                val = None
            tags[tag] = val
        return tags

    try:
        ifd0 = read_ifd(u32(4))
        if ifd0.get(0x0112):
            out["orientation"] = ifd0[0x0112]
        date = None
        ex = {}
        if ifd0.get(0x8769):
            ex = read_ifd(ifd0[0x8769])
            date = ex.get(0x9003) or ex.get(0x9004)
            if ex.get(0xA002) and ex.get(0xA003):
                out["width"], out["height"] = ex[0xA002], ex[0xA003]
        date = date or ifd0.get(0x0132)
        gps_off = ifd0.get(0x8825)
        if gps_off:
            if gps_off >= len(buf):
                out["gps_beyond"] = True             # la posizione è più avanti nel file
            else:
                pos = _gps(read_ifd(gps_off))
                if pos:
                    out["lat"], out["lon"] = pos
        if shooting:
            for key, tag, d in (("make", 0x010F, ifd0), ("model", 0x0110, ifd0), ("exposure", 0x829A, ex),
                                ("fnumber", 0x829D, ex), ("iso", 0x8827, ex), ("focal", 0x920A, ex),
                                ("focal35", 0xA405, ex), ("lens", 0xA434, ex), ("lens_make", 0xA433, ex)):
                if d.get(tag) not in (None, "", 0):
                    out[key] = d[tag]
        if date and len(date) >= 19 and date[:4].isdigit() and date[:4] != "0000":
            out["capture"] = date[:10].replace(":", "-") + "T" + date[11:19]
    except (struct.error, IndexError):
        pass
    return out


def _gps(g):
    """(lat, lon) in gradi dai tag GPS EXIF, oppure None."""
    lat, lon = g.get(2), g.get(4)
    if not isinstance(lat, list) or not isinstance(lon, list) or len(lat) < 3 or len(lon) < 3:
        return None
    la = lat[0] + lat[1] / 60 + lat[2] / 3600
    lo = lon[0] + lon[1] / 60 + lon[2] / 3600
    if str(g.get(1) or "N").upper().startswith("S"):
        la = -la
    if str(g.get(3) or "E").upper().startswith("W"):
        lo = -lo
    if la == 0 and lo == 0 or abs(la) > 90 or abs(lo) > 180:
        return None                                # posizione assente o non valida
    return round(la, 6), round(lo, 6)


def read_exif(path, shooting=False, head=HEAD_BYTES):
    """Data, orientamento, dimensioni e posizione GPS (lat, lon); con shooting=True anche
    fotocamera, obiettivo, focale, tempo, apertura e ISO."""
    try:
        with open(path, "rb") as f:
            buf = f.read(head)
    except OSError:
        return {}
    if buf[:2] == b"\xff\xd8":  # JPEG: cerca il segmento APP1 Exif
        i = 2
        while i + 4 < len(buf) and buf[i] == 0xFF:
            marker = buf[i + 1]
            ln = struct.unpack_from(">H", buf, i + 2)[0]
            if marker == 0xE1 and buf[i + 4:i + 10] == b"Exif\0\0":
                out = _parse_tiff(buf[i + 10:i + 2 + ln], shooting)
                if i + 2 + ln > len(buf):
                    out["gps_beyond"] = "lat" not in out
                return out
            if marker == 0xDA:
                break
            i += 2 + ln
        return {}
    if buf[:2] in (b"II", b"MM"):
        return _parse_tiff(buf, shooting)
    return {}


# ---------------------------------------------------------------- video (MP4, MOV, M4V, 3GP)
ISO6709 = re.compile(rb"([+-]\d{1,2}\.\d{2,})([+-]\d{1,3}\.\d{2,})")


def read_video(path):
    """Data di ripresa e posizione (lat, lon) di un video MP4/MOV."""
    out = {"capture": read_video_date(path)}
    moov = _moov(path)
    if moov:
        # posizione in formato ISO 6709 ("+45.0703+007.6869/"): iPhone, Android, molte videocamere
        for tag in (b"com.apple.quicktime.location.ISO6709", b"\xa9xyz", b"loci"):
            i = moov.find(tag)
            if i >= 0:
                m = ISO6709.search(moov, i, i + 400)
                if m:
                    la, lo = float(m.group(1)), float(m.group(2))
                    if abs(la) <= 90 and abs(lo) <= 180 and (la or lo):
                        out["lat"], out["lon"] = round(la, 6), round(lo, 6)
                        break
    return out


def _moov(path):
    """Il blocco 'moov' (metadati) di un MP4/MOV, al massimo 4 MB, oppure None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            pos = 0
            while pos + 8 <= end:
                f.seek(pos)
                hdr = f.read(16)
                size, kind = struct.unpack(">I4s", hdr[:8])
                head = 8
                if size == 1:
                    size, head = struct.unpack(">Q", hdr[8:16])[0], 16
                elif size == 0:
                    size = end - pos
                if size < head:
                    return None
                if kind == b"moov":
                    f.seek(pos + head)
                    return f.read(min(size - head, 4 * 1024 * 1024))
                pos += size
    except (OSError, struct.error):
        return None
    return None


def read_video_date(path):
    """Data di ripresa dall'intestazione 'mvhd' dei file MP4/MOV.
    Salta da un blocco all'altro senza leggere il filmato, quindi è veloce anche sul NAS."""
    import time as _t
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            pos = 0
            while pos + 8 <= end:
                f.seek(pos)
                hdr = f.read(16)
                size, kind = struct.unpack(">I4s", hdr[:8])
                head = 8
                if size == 1:
                    size, head = struct.unpack(">Q", hdr[8:16])[0], 16
                elif size == 0:
                    size = end - pos
                if size < head:
                    return None
                if kind == b"moov":
                    f.seek(pos + head)
                    moov = f.read(min(size - head, 4 * 1024 * 1024))
                    i = moov.find(b"mvhd")
                    if i < 4:
                        return None
                    version = moov[i + 4]
                    if version == 1:
                        secs = struct.unpack(">Q", moov[i + 8:i + 16])[0]
                    else:
                        secs = struct.unpack(">I", moov[i + 8:i + 12])[0]
                    unix = secs - 2082844800          # dal 1904 al 1970
                    if unix <= 0:
                        return None                   # data non impostata dalla videocamera
                    return _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime(unix))
                pos += size
    except (OSError, struct.error, IndexError, OverflowError, ValueError):
        return None
    return None
