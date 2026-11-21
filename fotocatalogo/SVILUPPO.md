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
| `exif.py` | lettore EXIF minimale (JPEG/TIFF/RAW) e data di ripresa dei video MP4/MOV |
| `static/app.js` | tutta l'interfaccia (circa 1900 righe, divise in sezioni `// ====`) |
| `mac/Provinia.swift` | finestra, menu di macOS, avvio e arresto del motore, finestre di scelta native, titolo con il nome del catalogo |
| `mac/fcimg.swift` | `fcimg info …` (dati di scatto in JSON, solo metadati), `fcimg tojpeg …` e `fcimg export …` (ImageIO: orientamento, rotazione, ridimensionamento, EXIF) |

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
- **Dati di scatto** nel pannello (fotocamera, obiettivo, focale, tempo, apertura, ISO):
  letti alla selezione da `shooting_info` in `server.py` (`fcimg info` sul Mac, tutti i
  formati; altrimenti `exif.read_exif(shooting=True)`, solo JPEG e RAW TIFF), tenuti in
  memoria per (percorso, mtime). Non sono nel database. «Ultima modifica» è l'mtime del file.
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
- `<dialog>` richiede macOS 12.3 o successivo (`LSMinimumSystemVersion`).

## Strumenti di macOS usati (sul Linux di sviluppo non ci sono)

`qlmanage` e `sips` (miniature), `hdiutil` (cataloghi cifrati), `osascript` (scelta
di cartelle/file dal browser), `open` (Photoshop con `-b com.adobe.Photoshop`,
Finder), Adobe DNG Converter
(`/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter -c -p1 -d <dir> -o <nome> <file>`),
`swiftc` e `codesign` (costruzione dell'app).

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

Idee discusse ma non fatte:
- **estetica** dell'interfaccia: l'utente ne vuole parlare più avanti;
- scrivere la rotazione **dentro i file JPEG** (solo l'etichetta di orientamento,
  senza perdita di qualità), su richiesta;
- trascinare intere cartelle per spostarle nell'albero;
- un pulsante «Importa…» con selezione dei file (in WKWebView `<input type=file>`
  richiede `runOpenPanelWith` nel delegate Swift);
- raccolte smart (quelle di Lightroom non vengono importate);
- trascinamento verso il Finder anche nel browser (solo app per ora).
