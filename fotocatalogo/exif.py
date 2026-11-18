"""Lettore EXIF minimo, senza librerie esterne.

Legge solo data di scatto, orientamento e dimensioni da JPEG e dai RAW basati
su TIFF (NEF, CR2, ARW, DNG, ORF, RW2, PEF, TIFF...). Legge solo l'inizio del
file, quindi è veloce anche via rete sul NAS.
"""
import struct

HEAD_BYTES = 256 * 1024


def _parse_tiff(buf):
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
                val = buf[vo:vo + cnt].split(b"\0")[0].decode("ascii", "ignore")
            else:
                val = None
            tags[tag] = val
        return tags

    try:
        ifd0 = read_ifd(u32(4))
        if ifd0.get(0x0112):
            out["orientation"] = ifd0[0x0112]
        date = None
        if ifd0.get(0x8769):
            ex = read_ifd(ifd0[0x8769])
            date = ex.get(0x9003) or ex.get(0x9004)
            if ex.get(0xA002) and ex.get(0xA003):
                out["width"], out["height"] = ex[0xA002], ex[0xA003]
        date = date or ifd0.get(0x0132)
        if date and len(date) >= 19 and date[:4].isdigit() and date[:4] != "0000":
            out["capture"] = date[:10].replace(":", "-") + "T" + date[11:19]
    except (struct.error, IndexError):
        pass
    return out


def read_exif(path):
    try:
        with open(path, "rb") as f:
            buf = f.read(HEAD_BYTES)
    except OSError:
        return {}
    if buf[:2] == b"\xff\xd8":  # JPEG: cerca il segmento APP1 Exif
        i = 2
        while i + 4 < len(buf) and buf[i] == 0xFF:
            marker = buf[i + 1]
            ln = struct.unpack_from(">H", buf, i + 2)[0]
            if marker == 0xE1 and buf[i + 4:i + 10] == b"Exif\0\0":
                return _parse_tiff(buf[i + 10:i + 2 + ln])
            if marker == 0xDA:
                break
            i += 2 + ln
        return {}
    if buf[:2] in (b"II", b"MM"):
        return _parse_tiff(buf)
    return {}


# ---------------------------------------------------------------- video (MP4, MOV, M4V, 3GP)
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
