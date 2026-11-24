"""Esportazione in JPEG di foto, cartelle o raccolte.

Opzioni:
  mode    "long"  = bordo lungo (stessa dimensione massima per orizzontali e verticali)
          "width" = larghezza fissa, "height" = altezza fissa, "original" = dimensioni originali
  size    pixel (ignorato con "original"); le foto più piccole non vengono mai ingrandite
  quality 1-100
  nogps   toglie la posizione GPS dai dati EXIF
  dest    cartella di destinazione

Applica l'orientamento della fotocamera e la rotazione decisa nel catalogo.
I file originali non vengono mai toccati. Se nella destinazione esiste già un
file con lo stesso nome, il nuovo diventa "nome (2).jpg" e così via.
"""
import os
import sys

from catalog import VIDEO_EXT
import thumbs

IS_MAC = sys.platform == "darwin"
LAST = {}


def _unique(folder, base):
    path = os.path.join(folder, base + ".jpg")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).jpg")
        n += 1
    return path


def _is_jpeg(path):
    try:
        with open(path, "rb") as f:
            return os.path.getsize(path) > 100 and f.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _export_one(src, dst, rot, opts):
    mode, size = opts["mode"], int(opts.get("size") or 0)
    q = max(1, min(100, int(opts.get("quality") or 90)))
    helper = thumbs.find_imghelper()
    if helper:
        cmd = [helper, "export", src, dst, "--quality", f"{q / 100:.2f}", "--rotate", str(rot)]
        if mode != "original" and size:
            cmd += [{"long": "--max", "width": "--width", "height": "--height"}[mode], str(size)]
        if opts.get("nogps"):
            cmd.append("--nogps")
        return thumbs._run(cmd, 300)
    if IS_MAC:
        # senza l'app (avvio dal browser): sips
        cmd = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(q)]
        if mode != "original" and size:
            cmd += {"long": ["-Z", str(size)], "width": ["--resampleWidth", str(size)],
                    "height": ["--resampleHeight", str(size)]}[mode]
        if rot:
            cmd += ["-r", str(rot)]
        return thumbs._run(cmd + [src, "--out", dst], 300)
    try:                                   # solo per sviluppo, fuori da macOS
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            exif = im.getexif()
            im = ImageOps.exif_transpose(im)
            if rot:
                im = im.rotate(-rot, expand=True)
            w, h = im.size
            scale = 1.0
            if mode == "long" and size:
                scale = min(1.0, size / max(w, h))
            elif mode == "width" and size:
                scale = min(1.0, size / w)
            elif mode == "height" and size:
                scale = min(1.0, size / h)
            if scale < 1:
                im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[3])
                im = bg
            exif[0x0112] = 1
            if opts.get("nogps") and 0x8825 in exif:
                del exif[0x8825]
            im.convert("RGB").save(dst, "JPEG", quality=q, exif=exif.tobytes())
        return True
    except Exception:  # noqa: BLE001
        return False


def export(cat, ids, opts, job):
    dest = os.path.expanduser(opts.get("dest") or "")
    if not dest or not os.path.isdir(dest):
        raise RuntimeError(f"Cartella di destinazione non trovata: {dest}")
    if opts.get("mode") not in ("long", "width", "height", "original"):
        raise RuntimeError("Modalità di ridimensionamento sconosciuta")
    LAST.clear()
    LAST["dest"] = dest
    res = {"export": True, "esportate": 0, "byte": 0, "saltate": [], "errori": [], "dest": dest}
    job.update(0, f"Esportazione: 0 / {len(ids)}", total=len(ids))
    for n, pid in enumerate(ids, 1):
        if job.cancelled:
            break
        src = cat.photo_path(pid)
        name = os.path.basename(src or str(pid))
        base, ext = os.path.splitext(name)
        if ext.lower().lstrip(".") in VIDEO_EXT:
            res["saltate"].append([name, "è un video"])
        elif not src or not os.path.exists(src):
            res["saltate"].append([name, "file non trovato (il NAS è collegato?)"])
        else:
            dst = _unique(dest, base)
            tmp = os.path.join(dest, f".{base}.fotocatalogo-tmp.jpg")
            ok = _export_one(src, tmp, cat.rotation(pid), opts) and _is_jpeg(tmp)
            if ok:
                os.replace(tmp, dst)
                res["esportate"] += 1
                res["byte"] += os.path.getsize(dst)
            else:
                if os.path.exists(tmp):
                    os.remove(tmp)
                res["errori"].append([name, "esportazione non riuscita (formato non leggibile?)"])
        job.update(n, f"Esportazione: {n} / {len(ids)}")
    res["interrotta"] = job.cancelled
    return res
