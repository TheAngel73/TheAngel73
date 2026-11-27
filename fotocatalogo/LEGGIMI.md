# Provinia

*(fino a novembre 2026 si chiamava FotoCatalogo)* · Versione corrente e novità: [`NOVITA.md`](NOVITA.md)

Catalogo foto per Mac, pensato per sostituire la **Libreria** di Lightroom quando
le foto si modificano in Photoshop. Non ha strumenti di sviluppo o ritocco: solo
catalogazione.

- **Cartelle** come sul NAS, con o senza sottocartelle
- **Raccolte e set di raccolte**: una foto può stare in quante raccolte vuoi, e il
  pannello a destra mostra sempre tutte le raccolte della foto selezionata
- **Dati di scatto** nel pannello a destra: fotocamera, obiettivo, focale, tempo, apertura,
  ISO (quando presenti nel file) e data dell'ultima modifica del file
- **Parole chiave**, **etichette colore**, **bandierine** (scelta/scartata)
- Ricerca per nome file, parola chiave, cartella o luogo, con filtri per etichetta, bandierina, anno, formato
  e per **luogo** sulla mappa delle foto con posizione GPS
- **Importazione dal catalogo Lightroom** (`.lrcat`): cartelle, raccolte, set,
  parole chiave, etichette e bandierine, e **esportazione verso Lightroom**
- **Apri in Photoshop** (⌘E) e **Mostra nel Finder** (⌘R)
- Miniature di JPEG, PSD, TIFF, HEIC e RAW (NEF, CR2/CR3, ARW, RAF, DNG…) generate da macOS
- Pensato per grandi archivi (oltre 170 000 foto): la griglia carica solo ciò che vedi

**Le foto non vengono mai modificate.** L'app legge i file sul NAS e li rinomina,
sposta o elimina solo quando lo chiedi tu, sempre con una conferma. I cataloghi sono salvati sul Mac in **`Documenti/Provinia/`**.

## Requisiti

- Un Mac con processore Apple (M1 o successivi) e macOS 12.3 o più recente.
- Il NAS montato nel Finder (di solito compare in `/Volumes/NomeCartella`).
- Solo per costruire l'app da sé con `Crea app.command`: Python 3 e gli strumenti per
  sviluppatori (vedi sotto). L'app scaricata da **Releases** li ha già dentro.

## Installare l'app già pronta (anche su un altro Mac)

1. Nella pagina **Releases** del repository su GitHub scarica
   `Provinia-<versione>-AppleSilicon.zip` e aprilo: compare **Provinia.app**.
2. Trascinala in **Applicazioni**.
3. Al primo avvio macOS dice che non può verificare lo sviluppatore (l'app non è
   firmata da Apple): chiudi l'avviso, apri **Impostazioni di Sistema › Privacy e
   sicurezza**, scorri in fondo e fai clic su **Apri comunque** accanto a Provinia.
   Serve solo la prima volta.

Se anche dopo non parte, apri il Terminale e scrivi
`xattr -dr com.apple.quarantine /Applications/Provinia.app`, poi riaprila.

## Costruire l'app da sé

Requisiti in più per questa strada:

- macOS con Python 3. Apri il Terminale e scrivi `python3 --version`. Se il Mac
  propone di installare gli «strumenti di sviluppo da riga di comando», accetta:
  bastano quelli, non serve installare nient'altro.

1. Scarica questa cartella `fotocatalogo` sul Mac (per esempio in Documenti).
2. Fai doppio clic su **`Crea app.command`**. La prima volta macOS potrebbe bloccarlo:
   in quel caso fai clic destro › **Apri** › **Apri**.
   - Se mancano gli «strumenti per sviluppatori» di Apple (gratuiti), si apre la
     finestra per installarli: conferma, attendi qualche minuto e rilancia `Crea app.command`.
3. Lo script costruisce **Provinia.app**, la copia in **Applicazioni** e la apre.
   Da lì puoi trascinarla nel Dock come qualsiasi altra app.

L'app ha la sua finestra, la barra dei menu di macOS (Libreria, Modifica, Foto,
Vista…), i menu con il **tasto destro** e le finestre di scelta native del Mac.
Il «motore» Python parte e si ferma da solo insieme all'app: non serve il Terminale.

**Aggiornamenti:** quando il codice cambia, rilancia `Crea app.command` per
ricostruire l'app. Il catalogo non viene toccato.

**Senza app**, in alternativa: doppio clic su `Avvia Provinia.command`, che apre
l'interfaccia nel browser (con le stesse funzioni, menu col tasto destro compresi).

## Passare da Lightroom

1. **Chiudi Lightroom.**
2. In Provinia: **Libreria › Importa catalogo Lightroom…** e scegli il file `.lrcat`
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

## Tornare a Lightroom (esportazione)

Provinia non crea da sola un catalogo Lightroom (il formato interno non è pubblico e
dipende dalla fotocamera): le foto le importa Lightroom, poi Provinia ci riporta sopra
il tuo lavoro.

1. In Lightroom: **File › Nuovo catalogo**, poi importa le cartelle del NAS con
   **Aggiungi** (le foto restano dove sono). Aspetta la fine e **chiudi Lightroom**.
   Le cartelle da aggiungere sono elencate nella finestra dell'esportazione: devono essere
   le stesse di Provinia, con lo stesso percorso, altrimenti quelle foto vengono saltate.
2. In Provinia: **Libreria › Esporta nel catalogo Lightroom…** e scegli quel `.lrcat`.
3. Provinia fa una **copia di sicurezza** accanto («… (prima di Provinia data ora).lrcat»)
   e poi riporta nel catalogo etichette colore, bandierine, rotazioni, parole
   chiave, raccolte e set. Alla fine dice quante foto ha aggiornato e quali cartelle
   mancano in Lightroom.

Si può ripetere: le raccolte e le parole chiave già presenti non vengono duplicate.
L'esportazione aggiunge e aggiorna, non toglie: una parola chiave tolta in Provinia resta
in Lightroom. Le regolazioni dello Sviluppo non ci sono (Provinia non le conserva).

## Uso quotidiano

| Azione | Come |
|---|---|
| Aggiungere foto a una raccolta | trascinale sulla raccolta, oppure tasto **C** |
| Nuova raccolta / nuovo set | **＋** / **▤** accanto a «Raccolte» |
| Spostare una raccolta in un set | trascinala sul set (sul titolo «Raccolte» per riportarla al primo livello) |
| Togliere foto dalla raccolta aperta | **⌫ Backspace** |
| Etichette / bandierine | **6 7 8 9** / **P X U** |
| Vista ingrandita | doppio clic, **Spazio** o **Invio**, poi **← →** |
| Ritrovare le foto scelte in un'altra cartella o raccolta | seleziona le foto e clicca su un'altra cartella o raccolta: restano selezionate quelle presenti anche lì (es. raccolta «Pippo» → cartella «Vacanza con Pippo») |
| JPG ritoccato e RAW con lo stesso nome | si vedono come **una foto sola** (il JPG) con le etichette **JPG DNG** in basso; nel pannello «Anche in DNG» apre il RAW, nella vista ingrandita **V** o il pulsante JPG/DNG passa dall'uno all'altro. Etichette, bandierine, raccolte, parole chiave, rotazione, rinomina, spostamento ed eliminazione valgono per tutti e due; Photoshop ed esportazione per quello mostrato. Si disattiva da **Libreria › JPG e RAW con lo stesso nome insieme** |
| Saltare a una foto vicina | nella vista ingrandita porta il mouse sulla barra in basso: compare la **striscia delle foto vicine** (rotellina per scorrerla, clic per aprire). Tornando sulla foto sparisce dopo un paio di secondi |
| Zoom nella vista ingrandita | **clic** (o **Z**): adatta ↔ 100% · **doppio clic** o **Esc**: torna alla griglia · **rotellina** o pizzico sul trackpad · **+ −** · trascina per spostarti |
| Selezionare tutto | **⌘A** |
| Nuove foto sul NAS | trovate da sole all'apertura del catalogo; in qualsiasi momento: **⟳** o tasto destro › Sincronizza |
| Vedere i file ancora da convertire | **Catalogo › Da convertire** (tutto ciò che non è DNG, JPEG, HEIC o PSD) |
| Togliere dal catalogo foto cancellate | **Catalogo › File mancanti › Rimuovi tutte dal catalogo…**, oppure tasto destro › Rimuovi dal catalogo (o ⌫ in quella vista) |
| Ruotare | **⌘←** / **⌘→** (anche ⌘[ e ⌘]), tasto destro, pannello a destra o ⟲ ⟳ nella vista ingrandita |
| Schiarire o scurire per vedere meglio | cursore ☀ nella vista ingrandita (da −3 a +3 EV); solo a video, il file non cambia. Resta uguale scorrendo le foto, doppio clic per azzerare, si azzera chiudendo la vista |
| Allargare il pannello di sinistra | trascina il suo bordo destro (da 200 px in su); doppio clic sul bordo per la larghezza normale. La larghezza viene ricordata |
| Esportare in JPEG | **⇧⌘E** sulla selezione, oppure tasto destro su cartella o raccolta › Esporta in JPEG… |
| Importare dal desktop | trascina file o cartelle dal Finder su una cartella della barra laterale (o sulla griglia della cartella aperta) |
| Spostare foto | trascinale su una cartella, oppure tasto destro › Sposta in… |
| Rinominare una foto | **clic sul nome** della foto già selezionata (come nel Finder), oppure **F2**, oppure clic sul nome nel pannello a destra · **Invio** conferma, **Esc** annulla |
| Rinominare con nome e numero | seleziona più foto e **F2**, oppure tasto destro › Rinomina con nome e numero…; **Catalogo › Da rinominare** per i nomi della fotocamera |
| Copiare foto nel Finder | trascina le foto selezionate sulla scrivania o in una cartella del Finder (solo nell'app) |
| Copiare foto negli appunti | **⌘C** sulle foto selezionate, poi **⌘V** nel Finder, in Mail, Messaggi, Photoshop… (solo nell'app) |
| Cercare per luogo | pulsante **Luogo** in alto (o **Vista › Mappa dei luoghi…**, ⌥⌘M): clic su un cerchio, oppure **⇧ + trascina** per un'area, oppure «Mostra le foto di quest'area» |
| Eliminare tutte le scartate | **Libreria › Elimina dal disco tutte le scartate…**, oppure **Catalogo › Scartate** › Elimina tutte dal disco… |
| Salvare una raccolta | tasto destro su una raccolta o un set › **Salva raccolta…** |
| Tutte le altre azioni | **tasto destro** su foto, raccolte, cartelle e parole chiave |

## Conversione (RAW → DNG, PNG → JPEG)

Tasto destro su una o più foto › **Converti…**, oppure **Catalogo › Da convertire ›
Converti tutti (RAW e PNG)…**, oppure menu **Foto** dell'app.

- **RAW → DNG**: serve **Adobe DNG Converter** (gratuito, sul sito Adobe) in Applicazioni.
  Impostazioni predefinite di Lightroom: compressione senza perdita, anteprima media.
- **PNG → JPEG**: con gli strumenti di macOS, qualità 92%. Le parti trasparenti diventano
  bianche (un JPEG non può essere trasparente), data del file e dati EXIF/GPS restano.
- Il nuovo file viene salvato nella stessa cartella, verificato, e nel catalogo **prende il
  posto dell'originale** con raccolte, parole chiave, etichette e bandierine.
- Poi l'originale (e il suo eventuale `.xmp`) viene **cancellato**: sul NAS finisce nel
  cestino di rete, se attivo. Se la conversione non riesce, l'originale non viene toccato.
- Se esiste già un file con lo stesso nome di destinazione, l'originale viene saltato.
- Si può interrompere con **Interrompi** nella barra in basso; al termine compare un riepilogo.

## Duplicati ed eliminazione

**Libreria › Trova duplicati…** (anche dal menu dell'app):

- confronta le foto con lo stesso nome in cartelle diverse e ne verifica il **contenuto**
  (impronta SHA-1): sono duplicati solo i file identici byte per byte;
- trova anche i **file vuoti** (0 byte) che hanno una copia integra altrove, tipici di uno
  spostamento interrotto;
- nella revisione ogni gruppo ha una copia proposta **da tenere**: mai vuota o nascosta,
  poi quella nella **cartella preferita** (se l'hai indicata), poi quella con più
  informazioni di catalogo, poi quella nella cartella creata più di recente.
  Clic su una copia per tenerla, «Salta» per lasciare il gruppo com'è;
- prima di eliminare, raccolte, parole chiave, etichetta e bandierina delle copie
  eliminate vengono **riunite sulla copia che resta**.

**Elimina dal disco**: tasto destro › Elimina dal disco… (⌘⌫). Cancella i file (e i loro
`.xmp`) e li toglie dal catalogo, con conferma esplicita. Sul NAS finiscono nel cestino di
rete, se attivo; non passano dal Cestino del Mac.

**Foto scartate**: **Catalogo › Scartate** mostra tutte le foto con la bandierina ✕ (tasto
**X**), in qualunque cartella. Da lì, o da **Libreria › Elimina dal disco tutte le
scartate…**, si eliminano tutte insieme dal disco, con la stessa conferma di sopra. I filtri
in alto non contano: vengono eliminate tutte le scartate del catalogo.

**Luoghi (GPS)**: il pulsante **Luogo** in alto apre una mappa del mondo con i punti dove
sono state scattate le foto della vista corrente (cartella, raccolta, ricerca…). I punti vicini
si raggruppano in cerchi con il numero di foto. Si sposta trascinando e si ingrandisce con la
rotellina o il pizzico. Un **clic su un cerchio** mostra le sue foto; **⇧ + trascina** sceglie
un'area; **Mostra le foto di quest'area** usa ciò che si vede. L'area diventa un filtro, come gli
altri: il pulsante si colora e la ✕ in alto lo toglie. Nel pannello informazioni, le foto con
posizione hanno la riga **Posizione** con «Sulla mappa» e «Apri in Mappe».
Le posizioni si leggono dai file (JPEG, RAW, HEIC, video di smartphone…). Per le foto già in
catalogo la lettura avviene una volta, in background («Posizioni GPS» nella barra in basso):
si può interrompere e riprende da dove era rimasta. La mappa funziona senza internet.

**Nomi dei luoghi**: dalle coordinate si ricava la località più vicina, anche piccola (tutte quelle
con almeno 1000 abitanti): «Cuceglio, Piemonte», «Reykjavík, Islanda». Compare nel pannello
informazioni (un clic cerca tutte le foto di quel luogo) e passando sopra i cerchi della mappa.
Si può anche **cercare**: scrivere «Napoli» o «Piemonte» nella ricerca trova le foto scattate lì.
Anche questo funziona senza internet (dati GeoNames, CC BY 4.0; vedi `dati/LICENZE.txt`).

**Copiare negli appunti** (⌘C, solo nell'app): le foto selezionate vanno negli appunti come
file, come nel Finder: ⌘V nel Finder ne crea una copia, in Mail o Messaggi le allega. Con una
sola foto, le app che incollano immagini (es. Photoshop › Incolla) ricevono la foto a piena
risoluzione, con la rotazione del catalogo. Se stai scrivendo in un campo, ⌘C copia il testo.

## Rotazione ed esportazione

**Rotazione**: viene salvata nel catalogo, **senza modificare il file** (come in Lightroom).
Vale per griglia, vista ingrandita ed esportazioni. Aprendo il file originale in Photoshop
la foto appare con l'orientamento di prima.

**Esporta in JPEG** (selezione, cartella con le sottocartelle, raccolta o tutta la vista):
- **bordo lungo** (stessa misura massima per foto orizzontali e verticali), **larghezza**,
  **altezza** oppure **dimensioni originali**; le foto più piccole non vengono ingrandite;
- qualità regolabile; opzione per **togliere la posizione GPS**;
- orientamento della fotocamera e rotazione del catalogo applicati, profilo colore e dati
  EXIF mantenuti; i video vengono saltati;
- nomi già presenti nella destinazione: «nome (2).jpg»; le impostazioni vengono ricordate.

## Gestione dei file

**Importare dal desktop**: trascina file o intere cartelle dal Finder su una cartella della
barra laterale, oppure sulla griglia quando stai guardando una cartella. I file vengono
**copiati** sul NAS (gli originali restano dove sono) e catalogati subito; una cartella
trascinata viene ricreata con le sue sottocartelle. I file che non sono foto o video, e
quelli con un nome già presente nella destinazione, vengono saltati.

**Spostare**: trascina le foto su una cartella della barra laterale, oppure tasto destro ›
**Sposta in…**. Il file si sposta sul disco con il suo `.xmp`; raccolte, parole chiave ed
etichette restano. Se nella destinazione esiste già un file con lo stesso nome viene saltato.

**Cartelle** (tasto destro): Nuova cartella…, Rinomina cartella…, Elimina cartella vuota….

**Rinominare una sola foto**: clic sul nome di una foto già selezionata (come nel Finder),
oppure **F2**, oppure clic sul nome nel pannello a destra. Si modifica solo il nome:
l'estensione resta la stessa. **Invio** conferma, **Esc** annulla. Il `.xmp` segue la foto,
tranne quando c'è un altro file con lo stesso nome (es. RAW + JPEG): allora resta al RAW.

**Rinominare con nome e numero** (più foto + F2, o tasto destro › Rinomina con nome e numero…): prefisso, primo numero, cifre, separatore,
ordine (data di scatto o nome attuale), estensione minuscola; i file con lo stesso nome
(RAW + JPEG) ricevono lo stesso numero. Anteprima dal vivo dei nuovi nomi. Se un nome è già
occupato (sul disco o nel catalogo) la rinomina non parte. I `.xmp` seguono il loro file.
La rinomina passa da nomi temporanei: anche gli scambi di nome sono sicuri e, se l'app si
chiudesse a metà, al riavvio la rinomina viene completata.

**Copiare trascinando** (solo nell'app): trascina una o più foto dalla griglia sulla scrivania
o in una cartella del Finder (o su Mail, AirDrop…): viene creata una **copia** dei file
originali. Fuori dall'app il trascinamento è sempre una copia, anche sullo stesso NAS:
gli originali restano dove sono. Dentro l'app, trascinare su una cartella continua a
*spostare* e su una raccolta ad *aggiungere*.

**Salva raccolta…** (tasto destro su una raccolta o un set): copia i file **originali** in
una nuova cartella, ricreando la struttura delle raccolte. Esempio: il set «Gite» con le
raccolte «Torino» e «Napoli» diventa `Gite/Torino/…` e `Gite/Napoli/…`; salvando il set
«2021» si ottiene anche il livello `2021/`. Comodo per passare a qualcuno le foto originali.
- prima di partire mostra la struttura, il numero di file e lo spazio necessario;
- se la cartella esiste già ne crea una nuova («Gite (2)»): nulla viene sovrascritto;
- due foto con lo stesso nome nella stessa raccolta diventano «nome.jpg» e «nome (2).jpg»;
- a scelta copia anche i `.xmp`; una foto in più raccolte viene copiata in ognuna;
- si può interrompere; i file non trovati (NAS scollegato) vengono elencati alla fine.

**Catalogo › Da rinominare** mostra i file che hanno ancora il nome dato dalla fotocamera
(`_DSC0001`, `DSC_0001`, `IMG_1234`, `DSCF…`, `P1010001`, `DJI_…`, `GOPR…`, `PXL_…`).

## Sincronizzazione automatica

All'apertura di un catalogo parte una **sincronizzazione rapida**, in background: controlla
solo la data di modifica delle cartelle e rilegge esclusivamente quelle cambiate. Trova così
le foto aggiunte (anche in cartelle nuove), quelle cancellate o spostate (segnate come
«mancanti») e quelle tornate al loro posto. Se trova qualcosa, compare un messaggio in basso.

La prima volta non ha date di riferimento, quindi controlla tutte le cartelle e può
richiedere qualche minuto. Le volte successive bastano pochi secondi. Se il NAS non è
collegato, non tocca nulla.

## Miniature

Le miniature vengono create la prima volta che una foto compare e poi restano in cache.
Mentre guardi una pagina, il motore prepara **in background** anche quelle delle foto
successive, così scorrendo sono già pronte. Comandi e clic hanno sempre la precedenza.

## Come leggere le miniature

- **Triangolino in alto a sinistra**: verde = JPEG o HEIC, giallo = PSD, rosso = RAW proprietario
  (ARW, NEF, CR2…) da convertire. I DNG non hanno il triangolino.
- **Striscia colorata sotto la foto**: l'etichetta colore.
- **▶ MOV / MP4…** in alto a sinistra: video. Si riproducono nella vista ingrandita
  (MP4, MOV, M4V); gli altri formati si aprono con l'app predefinita. Li trovi tutti in
  **Catalogo › Video**.
- ⚑ in alto a destra: foto scelta. Foto sbiadita con il nome barrato: scartata.

## Cataloghi

Come in Lightroom, puoi avere più cataloghi. Stanno tutti in `Documenti/Provinia/`:

```
Documenti/Provinia/
├── Catalogo principale/          ← catalogo normale: una cartella
│   ├── catalogo.db               ← raccolte, parole chiave, etichette… (il file importante)
│   └── miniature/                ← si possono cancellare, vengono ricreate
└── Personale.sparsebundle        ← catalogo protetto: immagine disco cifrata
```

- **Cambiare catalogo:** pulsante con il nome del catalogo in alto a sinistra,
  oppure **Libreria › Cambia catalogo…** (⌥⌘O).
- **Catalogo all'avvio:** dallo stesso menu, «Apri questo catalogo all'avvio».
  Se non ne scegli uno, si apre l'ultimo usato. Se è protetto, all'avvio viene chiesta la password.
- **Nuovo catalogo:** scegli se proteggerlo con password al momento della creazione.
- **Proteggere un catalogo esistente:** aprilo e scegli «Proteggi con password…».
  Viene copiato in un'immagine cifrata e la copia non cifrata viene cancellata.

**Cataloghi protetti.** Usano la cifratura AES-256 di macOS, la stessa delle immagini
disco cifrate di Utility Disco. Finché il catalogo è bloccato, raccolte, parole chiave
e miniature non sono leggibili, nemmeno aprendo i file con altri programmi.
Si richiude da solo quando passi a un altro catalogo o chiudi l'app, oppure subito con
**Blocca catalogo** (⌘L). Tieni presente che:
- **se dimentichi la password il catalogo non è recuperabile**;
- la password protegge il *catalogo*, non le foto sul NAS: chi accede al NAS vede i file
  come prima.

La prima volta macOS chiede il permesso di accedere alla cartella Documenti: concedilo.

Alla prima apertura di questa versione, il catalogo creato con la versione precedente
viene spostato automaticamente in `Documenti/Provinia/Catalogo principale`.

**Cambio di nome**: alla prima apertura di Provinia la cartella `Documenti/FotoCatalogo`
diventa `Documenti/Provinia` (e così le impostazioni in *Libreria/Application Support*):
cataloghi, password e preferenze restano quelli di prima. La vecchia `FotoCatalogo.app`
in Applicazioni viene sostituita da `Provinia.app` quando ricostruisci l'app.

## Backup

Basta copiare la cartella `Documenti/Provinia` (con l'app chiusa), oppure lasciare
che se ne occupi Time Machine. Un catalogo protetto resta cifrato anche nel backup.

## Struttura del codice

| File | Contenuto |
|---|---|
| `server.py` | server web locale e API |
| `catalog.py` | database SQLite e scansione delle cartelle |
| `catalogs.py` | più cataloghi, cataloghi protetti (immagini cifrate) |
| `lightroom.py` | importazione da `.lrcat` |
| `thumbs.py` | miniature (QuickLook/sips su macOS) |
| `exif.py` | lettura della data di scatto da JPEG e RAW |
| `conversione.py` | conversione RAW → DNG e PNG → JPEG |
| `gestione_file.py` | ricerca duplicati ed eliminazione dal disco |
| `esporta.py` | esportazione in JPEG |
| `static/` | interfaccia (HTML, CSS, JavaScript) |
| `mac/` | app nativa per macOS (Swift), convertitore/esportatore `fcimg` e script `crea_app.sh` |

Per chi sviluppa: architettura, decisioni e prove sono descritte in `SVILUPPO.md`.
