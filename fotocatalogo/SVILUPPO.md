# Provinia (ex FotoCatalogo) — note per lo sviluppo

Nota per chi riprende il progetto, persone o assistenti, anche in una conversazione
nuova. Per l'uso dell'app vedi `LEGGIMI.md`: qui ci sono architettura, decisioni
prese (con i motivi), trappole già incontrate e come provare le modifiche.

## In breve

Catalogo foto per **macOS** che sostituisce la Libreria di Lightroom Classic per
un archivio di circa **170 000 foto su un NAS**. Nessuno strumento di sviluppo o
ritocco: quello si fa in Photoshop. Fino a novembre 2026 si chiamava **FotoCatalogo**:
la cartella del codice (`fotocatalogo/`), l'identificativo dell'app (`local.fotocatalogo`),
le variabili `FOTOCAT_*` e i suffissi dei file temporanei hanno mantenuto il vecchio nome. L'utente scrive in italiano, e interfaccia,
messaggi, commenti e commit sono **in italiano**.

- **Motore**: Python 3, **solo libreria standard** (niente pip). Server HTTP locale
  su `127.0.0.1:8765`, database SQLite.
- **Interfaccia**: HTML, CSS e JavaScript senza framework, in `static/`.
- **App per Mac**: finestra WebKit in Swift (`mac/Provinia.swift`), più il
  convertitore `mac/fcimg.swift`. Si compilano sul Mac dell'utente con
  `Crea app.command` → `mac/crea_app.sh`, che richiede gli strumenti per
  sviluppatori di Apple. L'app pesa meno di 1 MB e include una copia di `*.py` e `static/`.
  **Dopo ogni modifica l'utente deve rilanciare `Crea app.command`.**
- Schermata di avvio e «Informazioni su»: `Provinia about.png` (dell'utente, 1536×1024 @2x, bordi trasparenti con la finestra già disegnata; il `.psd` è il sorgente), copiata in `Resources/about.png` e mostrata da `Splash` in `mac/Provinia.swift`: all'avvio finché l'interfaccia è caricata (almeno 1,5 s, al massimo 10). Font dell'interfaccia: **Inter** (`static/inter.woff2`, licenza OFL in `static/inter-LICENSE.txt`), incluso nell'app; icone della barra laterale: SVG in `ICON` (`app.js`). Icona: `mac/Provinia.icns` (fornita dall'utente; `static/icona.png` ne è un estratto da 128 px).
- Avvio alternativo nel browser: `Avvia Provinia.command`, cioè `python3 server.py`.

## Principi da non rompere

1. **I file delle foto si toccano solo su richiesta esplicita**: conversione,
   eliminazione, spostamento, rinomina, importazione. Tutto il resto è solo nel
   catalogo: rotazione, stelle, etichette, raccolte, parole chiave.
2. **Prima l'operazione sul disco, poi la verifica, poi il catalogo.** Se qualcosa
   fallisce, l'originale resta intatto. Si scrive su un file temporaneo nascosto
   (`.nome.fotocatalogo-tmp…`), lo si controlla, poi si rinomina.
3. **Cancellazioni**: `os.remove` sul NAS; i file finiscono nel cestino di rete
   del NAS, se attivo. Sempre una conferma esplicita nell'interfaccia.
4. **NAS scollegato**: non si segna niente come mancante e non si cancella niente.
5. **Ogni errore annulla la modifica in sospeso al database** (`rollback()` in
   `server.py`). Senza, il catalogo resta occupato e tutto si blocca: è successo.
6. Testi rivolti all'utente in italiano semplice, senza gergo tecnico.

## Struttura dei file

| File | Ruolo |
|---|---|
| `server.py` | server HTTP (`ThreadingHTTPServer`), API, lavori in background (`Job`, `start_job`), cataloghi aperti |
| `catalog.py` | classe `Catalog`: schema SQLite, connessioni per thread, scansione completa, **sincronizzazione rapida**, riconoscimento dei nomi da fotocamera |
| `catalogs.py` | più cataloghi in `~/Documents/Provinia`, cataloghi **cifrati** (immagini disco `hdiutil`), impostazioni, migrazione dalla prima versione e dal vecchio nome (`migrate_name`) |
| `lightroom.py` | importazione da `.lrcat`, letta da una **copia temporanea** |
| `thumbs.py` | miniature (QuickLook/sips), preparazione in background (`Prefetcher`), piena risoluzione, rotazione, `find_imghelper()` |
| `conversione.py` | RAW → DNG (Adobe DNG Converter), PNG → JPEG (`fcimg`) |
| `gestione_file.py` | duplicati, elimina dal disco, sposta, **rinomina**, cartelle, recupero delle rinomine interrotte |
| `esporta.py` | esportazione in JPEG (bordo lungo, larghezza, altezza, originale; senza GPS) |
| `salva.py` | «Salva raccolta…»: copia gli originali di una raccolta o di un set ricreando la struttura in cartelle |
| `exif.py` | lettore EXIF minimale (JPEG/TIFF/RAW: data, dimensioni, dati di scatto, **GPS**) e data e posizione dei video MP4/MOV |
| `luoghi.py` | nome del luogo dalle coordinate (località più vicina, dati in `dati/`) |
| `static/app.js` | tutta l'interfaccia (circa 2200 righe, divise in sezioni `// ====`) |
| `static/mappa.js` | mappa dei luoghi (canvas, Mercatore, raggruppamento dei punti, filtro per area) |
| `static/mondo*.json` | contorni del mondo per la mappa (Natural Earth), `inter.woff2` font, `icona.png` |
| `dati/` | `luoghi.tsv.gz` (località GeoNames), `paesi.json` (nomi italiani), `LICENZE.txt` |
| `strumenti/` | script da usare una volta per rigenerare i dati: `crea_mappa.py`, `crea_luoghi.js` |
| `mac/Provinia.swift` | finestra, menu di macOS, avvio e arresto del motore, finestre di scelta native, titolo con il nome del catalogo, trascinamento verso il Finder, ⌘C, schermata di avvio |
| `mac/fcimg.swift` | `fcimg info …` (dati di scatto e GPS in JSON, solo metadati), `fcimg tojpeg …` e `fcimg export …` (ImageIO: orientamento, rotazione, ridimensionamento, EXIF) |

## Dati

- **Cataloghi**: `~/Documents/Provinia/<Nome>/catalogo.db` e `miniature/`.
  Un catalogo protetto è `<Nome>.sparsebundle` (AES-256), montato in
  `~/Library/Application Support/Provinia/sbloccati/<uid>`.
- **Impostazioni** (catalogo predefinito, ultimo usato, cataloghi esterni):
  `~/Library/Application Support/Provinia/impostazioni.json`.
- **Registro**: `~/Library/Logs/Provinia.log`.
- Variabili d'ambiente per sviluppo e test: `FOTOCAT_DATA`, `FOTOCAT_BASE`,
  `FOTOCAT_PORT`, `FOTOCAT_HDIUTIL`, `FOTOCAT_DNG`, `FOTOCAT_IMGHELPER`.

**Schema** (`catalog.py`): `roots` (cartelle principali, percorso modificabile),
`folders` (`root_id`, `rel_path`, `parent_id`), `photos` (con `missing`, `rating`,
`label`, `flag`, `rotation`, `size`, `mtime`, `capture_time`, `added_at`; vincolo
UNIQUE su `folder_id, filename`), `collections` (`is_set` = set di raccolte,
`parent_id`), `collection_photos`, `keywords` (albero, `parent_id` 0 = primo
livello), `photo_keywords`, `dir_state` (data di modifica di ogni cartella, per la
sincronizzazione rapida). Le colonne nuove si aggiungono con `ALTER TABLE` in
`Catalog.__init__` (vedi `rotation`), così i cataloghi esistenti continuano a funzionare.

## Meccanismi importanti

- **Sincronizzazione rapida** (`Catalog.quick_sync`), all'apertura del catalogo:
  confronta la data di modifica di ogni cartella con `dir_state` e rilegge solo le
  cartelle cambiate (`_sync_dir`); poi ricontrolla una per una le foto
  `missing=1`. Una foto è «mancante» **solo se nella cartella non c'è nessun file
  con quel nome, qualunque sia l'estensione**. Il vecchio filtro per estensione
  segnava come mancanti i video importati da Lightroom: bug corretto.
- **Due indirizzi**: l'interfaccia si carica da `localhost`, le immagini arrivano
  da `127.0.0.1` (`IMG_BASE` in `app.js`). Il browser apre al massimo 6
  collegamenti per indirizzo: con un indirizzo solo, i comandi restavano in coda
  dietro alle miniature in preparazione e l'app sembrava bloccata.
- **URL delle immagini** con `?c=<uid catalogo>&v=<versione>`. La versione è un
  CRC di `added_at|mtime|rotation`, così il browser non mostra miniature vecchie
  (stesso id in un altro catalogo, dopo una conversione o una rotazione).
- **Rotazione**: colonna `rotation` (0/90/180/270, in senso orario), applicata
  solo alle copie in cache (`thumb_file(..., rot)`) e alle esportazioni. Il file
  originale non cambia: Photoshop lo apre con l'orientamento di prima.
- **Rinomina libera di un file** (clic sul nome, F2 con una sola foto): `POST /api/rename/one`
  usa la stessa `rename` con `opts["name"]` (solo il nome base, l'estensione resta). Il
  `.xmp` non segue se c'è un altro file con lo stesso nome base (`keep_side`).
- **Rinomina** (`gestione_file.rename`), in due passaggi:
  1. i file e i `.xmp` vanno su nomi temporanei (`.fctmp-<id>-<nome finale>`,
     `.fctmpx-<base finale>.xmp`), e **anche il catalogo** viene messo su nomi
     temporanei, tutti insieme;
  2. si assegnano i nomi definitivi. Se l'aggiornamento del catalogo fallisce, il
     file torna al suo nome; il blocco `finally` rimette a posto tutto ciò che non
     è stato completato.

  `recover_renames` (chiamata da `_sync_dir`) completa le rinomine interrotte da
  una chiusura improvvisa. Prima di partire, `rename_plan` controlla i conflitti
  **sia sul disco sia nel catalogo** (anche le voci mancanti con lo stesso nome).
- **Duplicati**: candidati con lo stesso nome (senza distinguere maiuscole),
  confermati dal contenuto (SHA-1 dei primi 256 KB, poi del file intero). Prima di
  eliminare, le informazioni di catalogo vengono riunite sulla copia tenuta
  (`_merge_into`). La proposta di quale copia tenere si calcola nell'interfaccia
  (`suggestKeep`): mai vuota o nascosta, poi cartella preferita, poi più
  informazioni di catalogo, poi cartella creata più di recente.
- **Importazione trascinando**: `PUT /api/upload?folder=&path=&mtime=` con il
  file nel corpo della richiesta (`safe_subpath` blocca `..`). Le cartelle
  trascinate si leggono con `webkitGetAsEntry`.
- **Cataloghi protetti**: la password non viene salvata. Ne resta solo un'impronta
  PBKDF2 in memoria, per verificare la password attuale prima di cambiarla. Le
  miniature dei cataloghi protetti si inviano con `Cache-Control: no-store`.
  Chiusura del motore (SIGTERM) → `close_current()` → l'immagine cifrata viene
  smontata.
- **Priorità delle miniature** (`thumbs.py`, `requestThumbs` in `app.js`). Le pagine di
  `/api/photos` dicono per ogni foto se la miniatura è pronta (`th`: 1 pronta, 0 da creare,
  -1 impossibile). La griglia usa `<img>` **solo per quelle pronte**; per le mancanti
  mostra un segnaposto e chiede `POST /api/thumbs/want` con gli id **sullo schermo**:
  la lista sostituisce la coda prioritaria (`Prefetcher.prio`), così cambiando vista o
  scorrendo si lavora subito su ciò che si guarda. La chiamata aspetta fino a 8 s che
  almeno una sia pronta; una chiamata nuova fa rispondere subito la precedente
  (`want_seq`), così non si accumulano collegamenti in attesa. Quattro lavoratori: la
  coda prioritaria ha la precedenza, il background (tutte le foto delle pagine caricate)
  ne usa al massimo 2 e solo quando la coda prioritaria è vuota; «Genera anteprime»
  (`BG_SLOTS`, interrompibile) si ferma finché c'è lavoro prioritario.
  **Perché così**: WebKit non annulla le immagini che spariscono dalla pagina, quindi
  con le `<img>` le miniature della vista precedente restavano in coda davanti a quelle
  nuove (cartella da 15 foto lentissima dopo una da 400). Chromium invece le annulla:
  per riprodurre il problema nelle prove bisogna simulare le richieste rimaste in coda.
  Le richieste dirette a `/thumb/ID` (pannello info, duplicati) restano sincrone, con
  posti propri assegnati all'ultima arrivata (`LifoSlots`) e il controllo
  `client_alive`. Per le prove: `FOTOCAT_TEST_THUMB_DELAY=0.6` rallenta le miniature
  create con Pillow (solo fuori dal Mac), come su un NAS.
- **⌘C** (solo app): la voce Copia chiama `copyAction` in Swift, che chiede alla pagina
  `fc.copyIds()` (null se si sta scrivendo o c'è del testo selezionato: allora copia il
  testo con `copy:`). Negli appunti vanno file URL; con una foto sola anche TIFF/JPEG a
  piena risoluzione tramite `ClipboardImageProvider`, preparato solo quando un'app incolla
  (`/full/ID`).
- **Posizioni GPS e mappa**: colonne `gps_lat`, `gps_lon`, `gps_read` (1 = file già letto,
  anche se senza posizione). I file nuovi si leggono all'importazione (`read_meta`, `gps_done`);
  il resto con il lavoro «Posizioni GPS» (`start_gps_job`, `quick`, interrompibile, riparte da
  dove era), avviato dopo ogni sincronizzazione (`and_gps`). Lettura: `exif.py` (IFD GPS di
  JPEG/RAW TIFF; ISO 6709 nei video MP4/MOV), sul Mac `fcimg info` per HEIC, CR3, PSD, PNG.
  Filtro `geo=sud,ovest,nord,est` in `build_query` (anche oltre il cambio di data);
  `GET /api/gps` dà i punti della vista (arrotondati a ~100 m). Mappa: `static/mappa.js`, canvas,
  Mercatore; contorni in `static/mondo.json` (1:50M) e `mondo-dettaglio.json` (1:10M, griglia
  più fine, caricato quando si ingrandisce), generati con `strumenti/crea_mappa.py` da
  world-atlas (Natural Earth, pubblico dominio; licenze in `static/mondo-LICENSE.txt`). Il
  filtro «Stelle» è stato tolto dalla barra (l'utente non usa le valutazioni; i dati restano).
  Attenzione: il lavoro in background cede il passo alle miniature (`wait_viewer_idle`) ma
  **al massimo 3 s**: prima aspettava all'infinito e una miniatura impossibile da creare
  (file non raggiungibile, richiesto di continuo dalla pagina) fermava la lettura GPS per
  sempre. Ora quelle miniature restano «non disponibili» per 5 minuti (`_unavailable`,
  stato -1) e la pagina rallenta le richieste. Ogni file è letto dentro un `try`; la
  lettura riparte anche aprendo la mappa e scrive inizio/avanzamento/fine nel registro.
  `crea_mappa.py` rende continue le longitudini (`unwrap`) e duplica le figure oltre il
  bordo: senza, Russia orientale e Figi disegnavano strisce orizzontali.
  **Dopo aver rigenerato `mondo*.json` aumentare `MAP_DATA_V` in `mappa.js`**: Safari e
  Firefox tengono in memoria i file statici e continuerebbero a mostrare quelli vecchi
  (è successo: strisce sparite in Chrome ma non nell'app né in Firefox).
- **Nomi dei luoghi** (`luoghi.py`): località più vicina da `dati/luoghi.tsv.gz` (GeoNames via
  all-the-cities, solo comuni e località: niente quartieri `PPLX`), griglia di 1°, ~1 ms a
  ricerca. Nomi italiani per le città note (`ITALIANO`), regioni italiane (`REGIONI`), paesi da
  `dati/paesi.json`. Salvati in `photos.place` (anche cercabili): li calcola il lavoro
  «Posizioni GPS» (`fill_places`); cambiando `luoghi.VERSION` vengono ricalcolati tutti (tabella
  `meta`). Dati rigenerabili con `strumenti/crea_luoghi.js`; `crea_app.sh` copia `dati/`.
- **Dati di scatto** nel pannello (fotocamera, obiettivo, focale, tempo, apertura, ISO):
  letti alla selezione da `shooting_info` in `server.py` (`fcimg info` sul Mac, tutti i
  formati; altrimenti `exif.read_exif(shooting=True)`, solo JPEG e RAW TIFF), tenuti in
  memoria per (percorso, mtime). Non sono nel database. «Ultima modifica» è l'mtime del file.
- **Larghezza del pannello di sinistra** (`sideResize` in `app.js`): variabile CSS
  `--side-w` su `#layout`, maniglia `#side-grip` sovrapposta al bordo, salvata in
  `localStorage["fc-side-w"]` per tutti i cataloghi. Il massimo lascia sempre almeno
  420 px alla griglia; se la finestra si stringe il pannello si adatta senza perdere
  la larghezza scelta.
- **Aspetto** (in fondo a `style.css`, blocco «aspetto»): stessi colori, ma piani
  distinti: pannelli `#28282a`, tavolo delle foto `--desk` più scuro, telaietti con
  sfumatura e filo di luce, selezione come anello blu. I cursori sono disegnati a mano;
  la parte colorata si calcola in JS (`rangeFill`, variabili `--pct`, `--lo`/`--hi`
  per quelli con lo zero al centro) perché WebKit non la disegna. La versione
  precedente all'aspetto nuovo è il commit `6f2ce8c`.
- **Revisione dell'interfaccia (dopo la 1.0)**, blocco in fondo a `style.css`:
  - sotto i 1420 px di larghezza i filtri (`#filters`) si raccolgono nel pannellino
    del pulsante «Filtri», con il numero dei filtri attivi; Esc o un clic fuori lo chiude;
  - `activeFilters()` elenca i filtri attivi: etichette cliccabili sotto il titolo
    (`#active-filters`) e messaggio «Nessuna foto corrisponde…» con «Togli i filtri»;
  - nel pannello informazioni il percorso è abbreviato (`shortPath`), completo nel
    suggerimento e copiato con un clic; `actionRows` mette Photoshop come pulsante principale;
  - i simboli di testo (▾ ⟲ ⟳ ⚑ ✕ ⊘) sono diventati icone di `ICON`;
  - i comandi che cancellano sono rossi anche nel menu Libreria (`danger-item`).
- **Seconda revisione**: `rangeFill` vale per tutti i cursori (anche nei dialoghi);
  i dialoghi usano gli stessi controlli della barra e «Chiudi» quando non c'è nulla da
  confermare; scorciatoie raggruppate in `helpDialog`; i lavori hanno `ended` e quelli
  riusciti spariscono dopo `JOB_KEEP_S` (60 s), gli errori restano fino a «Pulisci»;
  anche il pannello di destra si allarga (`panelResize`, `--info-w`); l'anteprima nel
  pannello segue le proporzioni della foto (altezza massima in px).
- **Cartelle raggiungibili**: `checkRoots()` chiede `/api/roots/status` ogni 8 s (e al
  ritorno sulla finestra). `_reachable` fa `isdir` con un limite di 2 s, così un NAS che
  non risponde non blocca il motore. Quando una cartella torna raggiungibile: spunta
  verde per 10 s, avviso, `thumbs._unavailable` svuotato e griglia ricaricata.
- **Esposizione nella vista ingrandita** (`setExpo` in `app.js`): solo CSS
  `brightness(2^(EV/2,2))` sull'`<img>`, che nei valori sRGB equivale a moltiplicare
  la luce lineare per 2^EV. Nessun file toccato. Dopo l'uso il cursore perde il
  focus, così le frecce tornano a scorrere le foto.
- **Striscia delle foto vicine** (`STRIP`, `stripRender` in `app.js`): `#loupe-strip` sopra
  `#loupe-bar`, si apre quando il mouse arriva sulla barra e si chiude `STRIP_HIDE_MS` (2,5 s)
  dopo che è tornato sulla foto. Contiene solo ±`STRIP_SPAN` (60) foto attorno a quella aperta,
  carica le pagine che servono e toglie le miniature uscite (`dropCell`, per non lasciarle in
  coda in WebKit). `mousedown` senza focus, così le frecce continuano a scorrere le foto.
- **Lavori in background**: `start_job`, con avanzamento in `/api/jobs`.
  `job.cancellable` mostra «Interrompi»; `job.quick` (sincronizzazione rapida) si
  ferma da solo al cambio di catalogo. L'interfaccia reagisce ai risultati in
  `onJobDone`, in base alle chiavi del risultato (`dng`, `export`, `moved`, …).
- **Comandi condivisi** (`COMMANDS` in `app.js`): li usano tastiera, menu col tasto
  destro e menu nativi. L'app per Mac chiama `fc.cmd("nome")`. Nell'app le
  scorciatoie con ⌘ arrivano **dal menu nativo** (`!NATIVE` in JS), per non
  eseguirle due volte; ⌘← ⌘→ ⌘⌫ non agiscono mentre si scrive in un campo di testo.

## Trappole di WebKit (la finestra dell'app usa il motore di Safari)

- Trascinando una miniatura, **WebKit aggiunge il tipo `Files`**: le foto interne
  (`application/x-fc-photos`) devono avere la precedenza (`isInternal`/`hasFiles`).
  Le immagini nelle celle hanno `draggable=false`.
- `effectAllowed = "copyMove"`, altrimenti il rilascio con effetto «sposta» sulle
  cartelle viene annullato.
- **Ogni tasto gestito va segnalato con `preventDefault()`** (Esc, numeri, P, X, U…):
  un `keydown` non consumato risale ad AppKit, che emette il suono d'avviso.
- **Trascinare le foto (app per Mac) = trascinare i file veri.** Nell'app il `dragstart`
  delle miniature viene annullato: la pagina manda `{cmd:"drag", ids}` a Swift
  (`FCWebView.requestDrag`), che chiede i percorsi a `POST /api/paths` e avvia un
  trascinamento **nativo** (`beginDraggingSession`, sorgente `FileDragSource`, oggetto
  separato per non toccare i trascinamenti propri di WebKit). Fuori dall'app solo
  `.copy`: il Finder copia, mai sposta. Dentro la finestra arriva come `Files`: la pagina
  sa di averlo iniziato (`S.nativeDrag`) e `isInternal`/`dragIds` lo trattano come foto
  (sposta su cartella, aggiungi a raccolta). A fine trascinamento Swift manda un
  `mouseUp` sintetico a WebKit e chiama `fc.dragEnded()`; il clic generato da quel
  `mouseUp` viene ignorato finché `S.nativeDrag` è attivo.
  Tentativo precedente fallito: aggiungere file al trascinamento di WebKit (override di
  `dragImage…`, «unavailable» in Swift; con il runtime ObjC non veniva comunque chiamato:
  il Finder riceveva solo il testo e creava un ritaglio `.textClipping`).
- **Finestra**: la pagina è vincolata a `window.contentLayoutGuide` (sotto la barra del
  titolo), `FCWebView.acceptsFirstMouse` è `true` e chiusa la schermata di avvio la
  finestra principale torna key (precauzioni aggiunte cercando un altro problema).
- **Mai `overflow: hidden` su `#topbar`**: il menu «Libreria» si apre sotto la barra e
  verrebbe tagliato via. Era la causa vera di «il pulsante Libreria non fa nulla».
  Nei test, `isVisible()` di Playwright non se ne accorge: controllare con
  `elementFromPoint` o con uno screenshot.
- **Errori dell'interfaccia**: uno script in `index.html` (prima di `app.js`) li mostra in
  una barra rossa e li manda a `POST /api/clientlog`, che li scrive nel registro
  (`~/Library/Logs/Provinia.log`, Aiuto › Mostra registro) con lo user agent.
- `<dialog>` richiede macOS 12.3 o successivo (`LSMinimumSystemVersion`).

## Strumenti di macOS usati (sul Linux di sviluppo non ci sono)

`qlmanage` e `sips` (miniature), `hdiutil` (cataloghi cifrati), `osascript` (scelta
di cartelle/file dal browser), `open` (Photoshop con `-b com.adobe.Photoshop`,
Finder), Adobe DNG Converter
(`/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter -c -p1 -d <dir> -o <nome> <file>`),
`swiftc` e `codesign` (costruzione dell'app).

## Versioni

- Il numero mostrato in «Informazioni su Provinia» viene dal file `VERSIONE`
  (letto da `mac/crea_app.sh`); la build (`CFBundleVersion`) è la data di costruzione.
- **1.0**: la versione considerata definitiva dall'utente (tag `v1.0`).
- **1.1**: filtri raccolti con finestra stretta e filtri attivi visibili, percorso breve
  e pulsanti rivisti nel pannello informazioni, icone uniformi, pallino del NAS aggiornato
  da solo, dialoghi e scorciatoie rivisti, barra delle attività più chiara, pannello di
  destra allargabile, anteprima intera, un solo menu aperto, rifiniture della vista ingrandita.
  Le modifiche successive si fanno su un ramo di sviluppo; quando l'utente ne approva
  una serie si aumenta `VERSIONE` (1.1, 1.2…) e si aggiunge un paragrafo a `NOVITA.md`
  (scritto per chi usa l'app, non per chi sviluppa).

## Come provare le modifiche

Lo sviluppo è avvenuto su **Linux**, senza Mac: lì si può provare tutto tranne gli
strumenti di macOS e il codice Swift.

- **Dati di prova**: foto generate con Pillow (presente nell'ambiente di
  sviluppo, **non** necessario sul Mac). `thumbs.py` ed `esporta.py` usano Pillow
  come piano B fuori da macOS.
- **Motore isolato**: `FOTOCAT_DATA=… FOTOCAT_BASE=… python3 server.py --no-browser`,
  poi `curl` sulle API. Mettere un `timeout` alle chiamate: se il catalogo si
  blocca, è un bug da scoprire.
- **Simulazioni**: un finto `hdiutil` (`create`/`attach`/`detach`/`chpass` con
  password in un file) con `FOTOCAT_HDIUTIL`; un finto DNG Converter (scrive
  `II*\0` più dei dati) con `FOTOCAT_DNG`; un finto catalogo Lightroom (tabelle
  `AgLibraryRootFolder`, `AgLibraryFolder`, `AgLibraryFile`, `Adobe_images`,
  `AgLibraryCollection`, `AgLibraryCollectionImage`, `AgLibraryKeyword`,
  `AgLibraryKeywordImage`; Lightroom usa la modalità WAL).
- **Interfaccia**: Playwright con Chromium. WebKit non è disponibile: i
  comportamenti di Safari si simulano costruendo `DragEvent` e `DataTransfer` a mano.
- Per le operazioni sui file, provare sempre i casi limite: nomi che si
  sovrappongono, file già esistenti, voci mancanti, interruzione a metà, NAS assente.
- **Sul Mac** l'utente costruisce l'app e prova. Funzioni Swift **non compilate
  qui**: tutto `mac/*.swift`. Finora l'utente le ha confermate funzionanti
  (cifratura, DNG, PNG → JPEG, esportazione, trascinamento dal Finder).

## Lavoro svolto e idee aperte

Il ramo di sviluppo è `claude/charming-gauss-qk1g5w`; la cronologia dei commit
descrive ogni passo.

Fatto dopo la prima nota (novembre 2026, in ordine): nome Provinia e icona, rinomina con
clic sul nome, copia trascinando nel Finder, Salva raccolta, niente suono sui tasti, elimina
tutte le scartate, ⌘C, priorità delle miniature, estetica (Inter, icone SVG, avvio e
Informazioni su), dati di scatto nel pannello, mappa dei luoghi con GPS e nomi dei luoghi.
Lezioni: verificare che un menu sia davvero **visibile** (non solo aperto) negli screenshot;
le prove in Chromium non mostrano i comportamenti di WebKit (immagini non annullate, cache).

Poi (fine novembre 2026): cursore dell'esposizione, pannello sinistro allargabile, nuovo
aspetto → **1.0** (tag `v1.0`). Revisioni con i plugin di design dell'utente (ui-review /
ui-polish) → **1.1** (tag `v1.1`): vedi `NOVITA.md` e la sezione «Versioni».

**Come si rilascia una versione** (la sessione non può creare tag né spingere su `main`):
1. su richiesta dell'utente: `VERSIONE`, paragrafo in `NOVITA.md`, nota in «Versioni»;
2. richiesta di unione dal ramo di sviluppo verso `main` con `gh api … /pulls`;
3. l'utente la unisce e **poi** crea la release `vX.Y` su `main` dalla pagina Releases
   (se la crea prima, il tag punta al `main` vecchio: con la 1.0 è successo);
4. controllare con `gh api …/git/refs/tags` che il tag coincida con `main`, poi
   riallineare il ramo di sviluppo (`git merge --ff-only origin/main`).
`NOVITA.md` (non ancora in `main`) entrerà con la prossima richiesta di unione.

Lezioni: nella revisione grafica guardare sempre le schermate vere, anche dei dialoghi
(il cursore della qualità era sbagliato solo lì); in WebKit le percentuali di altezza
dentro un box con `aspect-ratio` sono inaffidabili (usare px).

Idee discusse ma non fatte:
- dalla revisione grafica, non scelti: nessuno di rilievo rimasto;
- nomi dei luoghi: sigla della provincia in Italia (servono i codici ISTAT delle province);
- la mappa non «gira» oltre la linea del cambio di data (un'area tra Alaska e Siberia si
  sceglie solo con ⇧ + trascina dai due lati);
- scrivere la rotazione **dentro i file JPEG** (solo l'etichetta di orientamento,
  senza perdita di qualità), su richiesta;
- trascinare intere cartelle per spostarle nell'albero;
- un pulsante «Importa…» con selezione dei file (in WKWebView `<input type=file>`
  richiede `runOpenPanelWith` nel delegate Swift);
- raccolte smart (quelle di Lightroom non vengono importate);
- trascinamento verso il Finder anche nel browser (solo app per ora).
