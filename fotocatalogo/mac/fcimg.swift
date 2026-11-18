// fcimg — piccolo convertitore di immagini per FotoCatalogo (usa ImageIO di macOS).
//
//   fcimg tojpeg <sorgente> <destinazione> [qualità 0-1]
//
// Converte in JPEG appoggiando le parti trasparenti su fondo bianco (un JPEG non
// può avere trasparenza: senza questo passaggio diventerebbero nere).
// Mantiene profilo colore, orientamento e dati EXIF/GPS/IPTC della sorgente.
// Si compila con mac/crea_app.sh.

import Foundation
import ImageIO
import CoreGraphics

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 4, args[1] == "tojpeg" else {
    fail("uso: fcimg tojpeg <sorgente> <destinazione> [qualità 0-1]")
}
let srcURL = URL(fileURLWithPath: args[2])
let dstURL = URL(fileURLWithPath: args[3])
let quality = args.count > 4 ? (Double(args[4]) ?? 0.92) : 0.92

guard let source = CGImageSourceCreateWithURL(srcURL as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("immagine non leggibile")
}
let props = (CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]) ?? [:]

let width = image.width, height = image.height
var space = CGColorSpace(name: CGColorSpace.sRGB)!
if let own = image.colorSpace, own.model == .rgb { space = own }   // es. Display P3 dei telefoni

guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                          space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
    fail("memoria insufficiente")
}
let rect = CGRect(x: 0, y: 0, width: width, height: height)
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
ctx.fill(rect)
ctx.interpolationQuality = .high
ctx.draw(image, in: rect)
guard let flat = ctx.makeImage() else { fail("conversione non riuscita") }

guard let dest = CGImageDestinationCreateWithURL(dstURL as CFURL, "public.jpeg" as CFString, 1, nil) else {
    fail("impossibile scrivere il file")
}
var options: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: quality]
for key in [kCGImagePropertyExifDictionary, kCGImagePropertyGPSDictionary, kCGImagePropertyTIFFDictionary,
            kCGImagePropertyIPTCDictionary, kCGImagePropertyOrientation] {
    if let value = props[key] { options[key] = value }
}
CGImageDestinationAddImage(dest, flat, options as CFDictionary)
guard CGImageDestinationFinalize(dest) else { fail("scrittura non riuscita") }
