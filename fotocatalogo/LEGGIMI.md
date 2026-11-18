# FotoCatalogo

Catalogo foto per Mac, pensato per sostituire la **Libreria** di Lightroom quando
le foto si modificano in Photoshop. Non ha strumenti di sviluppo o ritocco: solo
catalogazione.

- **Cartelle** come sul NAS, con o senza sottocartelle
- **Raccolte e set di raccolte**: una foto può stare in quante raccolte vuoi, e il
  pannello a destra mostra sempre tutte le raccolte della foto selezionata
- **Parole chiave**, **stelle**, **etichette colore**, **bandierine** (scelta/scartata)
- Ricerca per nome file, parola chiave o cartella, con filtri per anno, formato, stelle ed etichetta
- **Importazione dal catalogo Lightroom** (`.lrcat`): cartelle, raccolte, set,
  parole chiave, stelle, etichette e bandierine
- **Apri in Photoshop** (⌘E) e **Mostra nel Finder** (⌘R)
- Miniature di JPEG, PSD, TIFF, HEIC e RAW (NEF, CR2/CR3, ARW, RAF, DNG…) generate da macOS
- Pensato per grandi archivi (oltre 170 000 foto): la griglia carica solo ciò che vedi

**Le foto non vengono mai modificate, spostate o cancellate.** L'app legge soltanto
i file sul NAS. Catalogo e miniature sono salvati sul Mac in
`~/Library/Application Support/FotoCatalogo/`.

## Requisiti

- macOS con Python 3. Apri il Terminale e scrivi `python3 --version`. Se il Mac
  propone di installare gli «strumenti di sviluppo da riga di comando», accetta:
  bastano quelli, non serve installare nient'altro.
- Il NAS montato nel Finder (di solito compare in `/Volumes/NomeCartella`).

## Avvio

1. Scarica questa cartella `fotocatalogo` sul Mac (per esempio in Documenti).
2. Fai doppio clic su **`Avvia FotoCatalogo.command`**.
   La prima volta macOS potrebbe bloccarlo: in quel caso fai clic destro → **Apri** → **Apri**.
3. Si apre una finestra del Terminale (lasciala aperta: è il «motore») e il browser
   su `http://localhost:8765`.

In alternativa, da Terminale: `cd` nella cartella, poi `python3 server.py`.

**Consiglio:** in Safari, con la pagina aperta, usa **File › Aggiungi al Dock**.
FotoCatalogo diventa un'app con la sua icona e la sua finestra, senza le barre del browser.

## Passare da Lightroom

1. **Chiudi Lightroom.**
2. In FotoCatalogo: **Libreria › Importa catalogo Lightroom…** e scegli il file `.lrcat`
   (di solito in `Immagini/Lightroom/`).
3. Al termine compare un riepilogo. Le **raccolte smart** non vengono importate,
   perché le loro regole sono in un formato interno di Lightroom: si possono
   ricreare con le ricerche e i filtri.
4. Se il NAS ora è montato con un nome diverso da quello registrato in Lightroom
   (pallino rosso accanto alla cartella), usa **Libreria › Cambia percorso del NAS…**.
5. Fai clic su **⟳** accanto a ogni cartella per sincronizzare: legge dimensioni e
   date dei file e trova foto aggiunte dopo l'ultimo uso di Lightroom.
6. Facoltativo: **Libreria › Genera anteprime della vista corrente** (con «Tutte le foto»
   selezionato) per creare tutte le miniature in anticipo. Con 170 000 foto può
   richiedere diverse ore: conviene lanciarlo la sera. Altrimenti le miniature
   vengono create man mano che scorri.

Il catalogo di Lightroom viene solo letto, quindi puoi continuare a usare Lightroom
finché non sei sicuro del passaggio. Puoi anche ripetere l'importazione: le foto già
presenti non vengono duplicate.

## Uso quotidiano

| Azione | Come |
|---|---|
| Aggiungere foto a una raccolta | trascinale sulla raccolta, oppure tasto **C** |
| Nuova raccolta / nuovo set | **＋** / **▤** accanto a «Raccolte» |
| Spostare una raccolta in un set | trascinala sul set (sul titolo «Raccolte» per riportarla al primo livello) |
| Togliere foto dalla raccolta aperta | **⌫ Backspace** |
| Stelle / etichette / bandierine | **0–5** / **6 7 8 9** / **P X U** |
| Vista ingrandita | doppio clic, **Spazio** o **Invio**, poi **← →** |
| Selezionare tutto | **⌘A** |
| Nuove foto sul NAS | **⟳** accanto alla cartella |

## Backup

Tutto il lavoro di catalogazione sta in un unico file:
`~/Library/Application Support/FotoCatalogo/catalogo.db`.
Includilo in Time Machine o copialo ogni tanto sul NAS.
La cartella `miniature` accanto si può cancellare: viene ricreata automaticamente.

## Struttura del codice

| File | Contenuto |
|---|---|
| `server.py` | server web locale e API |
| `catalog.py` | database SQLite e scansione delle cartelle |
| `lightroom.py` | importazione da `.lrcat` |
| `thumbs.py` | miniature (QuickLook/sips su macOS) |
| `exif.py` | lettura della data di scatto da JPEG e RAW |
| `static/` | interfaccia (HTML, CSS, JavaScript) |
