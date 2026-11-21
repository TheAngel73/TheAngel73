// fcimg — piccolo convertitore di immagini per FotoCatalogo (usa ImageIO di macOS).
//
//   fcimg tojpeg <sorgente> <destinazione> [qualità 0-1]
//       Converte in JPEG alle dimensioni originali (usato per PNG -> JPEG).
//
//   fcimg export <sorgente> <destinazione> [--quality 0-1] [--rotate 0|90|180|270]
//                [--max N | --width N | --height N] [--nogps]
//       Legge qualsiasi formato supportato da macOS (anche RAW, DNG, PSD, HEIC),
//       applica l'orientamento della fotocamera e la rotazione (in senso orario),
//       ridimensiona senza mai ingrandire (--max = bordo lungo) e scrive un JPEG.
//
//   fcimg info <file>
//       Stampa in JSON i dati di scatto (fotocamera, obiettivo, focale, tempo,
//       apertura, ISO) leggendo solo i metadati, senza decodificare l'immagine.
//
// In tojpeg ed export le parti trasparenti vanno su fondo bianco (un JPEG non può
// essere trasparente) e si mantengono profilo colore e dati EXIF/GPS/IPTC.
// Si compila con mac/crea_app.sh.

import Foundation
import ImageIO
import CoreGraphics

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments

if args.count == 3 && args[1] == "info" {
    let url = URL(fileURLWithPath: args[2])
    let opts = [kCGImageSourceShouldCache: false] as CFDictionary
    guard let src = CGImageSourceCreateWithURL(url as CFURL, opts),
          let props = CGImageSourceCopyPropertiesAtIndex(src, 0, opts) as? [String: Any] else {
        fail("metadati non leggibili")
    }
    let tiff = props[kCGImagePropertyTIFFDictionary as String] as? [String: Any] ?? [:]
    let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any] ?? [:]
    let aux = props[kCGImagePropertyExifAuxDictionary as String] as? [String: Any] ?? [:]
    var out: [String: Any] = [:]
    out["make"] = tiff[kCGImagePropertyTIFFMake as String]
    out["model"] = tiff[kCGImagePropertyTIFFModel as String]
    out["lens"] = exif[kCGImagePropertyExifLensModel as String] ?? aux[kCGImagePropertyExifAuxLensModel as String]
    out["lens_make"] = exif[kCGImagePropertyExifLensMake as String]
    out["focal"] = exif[kCGImagePropertyExifFocalLength as String]
    out["focal35"] = exif[kCGImagePropertyExifFocalLenIn35mmFilm as String]
    out["exposure"] = exif[kCGImagePropertyExifExposureTime as String]
    out["fnumber"] = exif[kCGImagePropertyExifFNumber as String]
    if let iso = exif[kCGImagePropertyExifISOSpeedRatings as String] as? [Any], let first = iso.first {
        out["iso"] = first
    }
    let clean = out.filter { JSONSerialization.isValidJSONObject([$0.value]) }
    let data = try! JSONSerialization.data(withJSONObject: clean)
    FileHandle.standardOutput.write(data)
    exit(0)
}

guard args.count >= 4, args[1] == "tojpeg" || args[1] == "export" else {
    fail("uso: fcimg tojpeg <sorgente> <destinazione> [qualità] | fcimg export <sorgente> <destinazione> [opzioni]")
}
let mode = args[1]
let srcURL = URL(fileURLWithPath: args[2])
let dstURL = URL(fileURLWithPath: args[3])

var quality = 0.92
var rotation = 0
var maxEdge: Double? = nil
var fixedWidth: Double? = nil
var fixedHeight: Double? = nil
var noGPS = false
if mode == "tojpeg" {
    if args.count > 4 { quality = Double(args[4]) ?? 0.92 }
} else {
    var i = 4
    func value() -> String {
        i += 1
        guard i < args.count else { fail("valore mancante per \(args[i - 1])") }
        return args[i]
    }
    while i < args.count {
        switch args[i] {
        case "--quality": quality = Double(value()) ?? 0.92
        case "--rotate": rotation = ((Int(value()) ?? 0) % 360 + 360) % 360
        case "--max": maxEdge = Double(value())
        case "--width": fixedWidth = Double(value())
        case "--height": fixedHeight = Double(value())
        case "--nogps": noGPS = true
        default: fail("opzione sconosciuta: \(args[i])")
        }
        i += 1
    }
}

guard let source = CGImageSourceCreateWithURL(srcURL as CFURL, nil) else { fail("immagine non leggibile") }
let props = (CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]) ?? [:]

var image: CGImage
if mode == "tojpeg" {
    guard let img = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("immagine non leggibile") }
    image = img
} else {
    // dimensioni già orientate e poi ruotate: servono per calcolare la scala
    let pw = (props[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue ?? 0
    let ph = (props[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue ?? 0
    let orientation = (props[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
    let swapped = [5, 6, 7, 8].contains(orientation)
    let ow = swapped ? ph : pw, oh = swapped ? pw : ph
    let fw = (rotation == 90 || rotation == 270) ? oh : ow
    let fh = (rotation == 90 || rotation == 270) ? ow : oh
    var scale = 1.0
    if fw > 0 && fh > 0 {
        if let m = maxEdge { scale = min(scale, m / max(fw, fh)) }
        if let w = fixedWidth { scale = min(scale, w / fw) }
        if let h = fixedHeight { scale = min(scale, h / fh) }
    }
    let longest = max(ow, oh)
    let maxPixels = longest > 0 ? max(1, Int((longest * scale).rounded())) : 100_000
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,   // dall'immagine vera, non dall'anteprima
        kCGImageSourceCreateThumbnailWithTransform: true,     // applica l'orientamento della fotocamera
        kCGImageSourceThumbnailMaxPixelSize: maxPixels,
        kCGImageSourceShouldCacheImmediately: true,
    ]
    guard let img = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
        fail("immagine non leggibile")
    }
    image = img
}

// disegno su fondo bianco, con la rotazione richiesta
let iw = image.width, ih = image.height
let turned = rotation == 90 || rotation == 270
let cw = turned ? ih : iw, ch = turned ? iw : ih
var space = CGColorSpace(name: CGColorSpace.sRGB)!
if let own = image.colorSpace, own.model == .rgb { space = own }   // es. Display P3, Adobe RGB
guard let ctx = CGContext(data: nil, width: cw, height: ch, bitsPerComponent: 8, bytesPerRow: 0,
                          space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
    fail("memoria insufficiente")
}
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
ctx.fill(CGRect(x: 0, y: 0, width: cw, height: ch))
ctx.interpolationQuality = .high
switch rotation {
case 90:  ctx.translateBy(x: 0, y: CGFloat(ch)); ctx.rotate(by: -.pi / 2)
case 180: ctx.translateBy(x: CGFloat(cw), y: CGFloat(ch)); ctx.rotate(by: .pi)
case 270: ctx.translateBy(x: CGFloat(cw), y: 0); ctx.rotate(by: .pi / 2)
default: break
}
ctx.draw(image, in: CGRect(x: 0, y: 0, width: iw, height: ih))
guard let flat = ctx.makeImage() else { fail("conversione non riuscita") }

guard let dest = CGImageDestinationCreateWithURL(dstURL as CFURL, "public.jpeg" as CFString, 1, nil) else {
    fail("impossibile scrivere il file")
}
var out: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: quality]
var keys = [kCGImagePropertyExifDictionary, kCGImagePropertyIPTCDictionary, kCGImagePropertyTIFFDictionary]
if !noGPS { keys.append(kCGImagePropertyGPSDictionary) }
for key in keys {
    if let value = props[key] { out[key] = value }
}
if mode == "tojpeg" {
    if let o = props[kCGImagePropertyOrientation] { out[kCGImagePropertyOrientation] = o }
} else {
    // i pixel sono già orientati: l'orientamento nel file deve essere "normale"
    out[kCGImagePropertyOrientation] = 1
    if var tiff = out[kCGImagePropertyTIFFDictionary] as? [CFString: Any] {
        tiff[kCGImagePropertyTIFFOrientation] = 1
        out[kCGImagePropertyTIFFDictionary] = tiff
    }
}
CGImageDestinationAddImage(dest, flat, out as CFDictionary)
guard CGImageDestinationFinalize(dest) else { fail("scrittura non riuscita") }
