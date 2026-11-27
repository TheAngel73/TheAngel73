# Novità di Provinia

Le versioni di Provinia, dalla più recente. Ogni versione si può scaricare dalla pagina
**Releases** del repository su GitHub; il numero della versione installata si legge in
**Provinia → Informazioni su Provinia**.

## 1.2 — novembre 2026

Una versione per organizzare meglio le foto ritoccate e i montaggi, con Annulla e Ripeti e
un'app che si installa su qualsiasi Mac con processore Apple. I cataloghi della 1.1 si aprono
senza fare nulla: le tabelle nuove si aggiungono da sole.

**JPG ritoccati e montaggi**
- Un **JPG e un RAW con lo stesso nome** nella stessa cartella (es. il DNG e il JPG ritoccato)
  si vedono come una foto sola, con le etichette **JPG DNG** sulla miniatura. Nel pannello
  «Anche in DNG» apre il RAW; nella vista ingrandita il pulsante JPG/DNG o il tasto **V**
  passa dall'uno all'altro. Si spegne da **Libreria**.
- **Pile nelle raccolte**: il JPG di un montaggio e le foto di partenza diventano una pila
  (tasto destro › **Crea pila** o **⌘G**). Nella raccolta resta la copertina con il numero di
  foto; nel pannello e nella vista ingrandita le miniature dei componenti, e **Mostra le foto
  della pila** per lavorarci. Nelle cartelle e nelle altre raccolte le foto restano normali.
- **Triangolino** in alto a sinistra: viola per JPG + RAW, blu per le pile.

**Annulla e Ripeti**
- **⌘Z** annulla le ultime operazioni (fino a 50) e **⇧⌘Z** le rifà: etichette, bandierine,
  rotazioni, parole chiave, raccolte, pile, e anche **rinomine e spostamenti dei file**.
  Si torna nella cartella o raccolta dove era stata fatta l'operazione, con le foto selezionate.

**Griglia e vista ingrandita**
- Portando il mouse sulla barra in basso della vista ingrandita compare la **striscia delle
  foto vicine**, per saltare avanti o indietro in una serie; sparisce da sola.
- Sulle miniature il nome è a sinistra (troncato se lungo) e il **formato** sempre visibile a destra.
- Cambiando cartella o raccolta **restano selezionate** le foto presenti anche lì.
- Le stelle sono state tolte dall'interfaccia (i valori importati da Lightroom restano nel catalogo).

**Lightroom**
- **Esporta nel catalogo Lightroom…** (menu Libreria): in un catalogo in cui Lightroom ha già
  importato le foto, Provinia riporta etichette, bandierine, rotazioni, parole chiave, raccolte
  e set, dopo una copia di sicurezza. La finestra elenca le cartelle da importare in Lightroom.
- L'importazione riconosce le etichette di Lightroom in italiano («Rossa»…), che prima andavano perse.

**Installazione**
- Nella pagina **Releases** c'è l'app già pronta (`Provinia-1.2-AppleSilicon.zip`), con Python
  incluso: funziona su qualsiasi Mac con processore Apple, senza strumenti per sviluppatori.
  Al primo avvio: Impostazioni di Sistema › Privacy e sicurezza › **Apri comunque**.

## 1.1 — novembre 2026

Una versione dedicata all'aspetto e alla comodità d'uso: nessun cambiamento ai cataloghi,
che restano compatibili con la 1.0.

**Barra in alto e filtri**
- Con la finestra stretta i filtri (Luogo, Etichette, Bandierine, Anno, Formato) si
  raccolgono nel pulsante **Filtri**, con un pallino che dice quanti sono attivi.
- I filtri attivi compaiono sotto il titolo della vista come etichette azzurre: la ✕
  toglie solo quel filtro.
- Se nessuna foto corrisponde, il messaggio dice quali filtri la escludono e offre
  **Togli i filtri**.
- Può restare aperto un solo menu alla volta.

**Pannello informazioni**
- Anche il pannello di destra si allarga trascinandone il bordo (doppio clic: misura normale).
- L'anteprima segue le proporzioni della foto e non viene mai tagliata.
- Sotto il nome compare solo la cartella; un clic copia il percorso completo.
- **Apri in Photoshop** è il pulsante principale, con accanto le rotazioni a icona.

**Cartelle sul NAS**
- Il pallino verde/rosso si aggiorna da solo quando il NAS si collega o si scollega.
  Al ritorno compare per qualche secondo una spunta verde e le anteprime mancanti
  vengono create subito.

**Finestre, menu e dettagli**
- Icone disegnate in uno stile unico al posto dei simboli di testo.
- Le finestre di dialogo usano gli stessi controlli della barra in alto; quelle
  informative si chiudono con **Chiudi**.
- **Scorciatoie da tastiera** complete e raggruppate per argomento.
- La barra delle attività in basso mostra l'orario di fine; le attività riuscite
  spariscono da sole dopo un minuto, gli errori restano fino a **Pulisci**.
- I comandi che cancellano file sono in rosso anche nel menu Libreria.
- Vista ingrandita con sfondo nero pieno, frecce ‹ › rotonde e testo di aiuto che si
  attenua dopo qualche secondo.

## 1.0 — novembre 2026

La prima versione definitiva, che sostituisce la Libreria di Lightroom.

- **Catalogo** di cartelle, raccolte e set, parole chiave, stelle, etichette colore e
  bandierine, con **importazione dal catalogo di Lightroom**.
- **Griglia veloce** anche con oltre 170 000 foto, con anteprime create in ordine di
  priorità (prima quelle sullo schermo).
- **Vista ingrandita** con zoom fino alla piena risoluzione, rotazione e un **cursore dell'esposizione**
  solo per la visione (il file non cambia).
- **Pannello informazioni** con dati di scatto (fotocamera, obiettivo, focale, tempo,
  apertura, ISO), data dell'ultima modifica e **nome del luogo** dalle coordinate GPS.
- **Mappa dei luoghi**: le foto con posizione GPS sul mappamondo, filtro per area.
- **Gestione dei file**: rinomina (anche con un clic sul nome), sposta, elimina con
  conferma, trova duplicati, elimina tutte le scartate.
- **Conversione** RAW → DNG e PNG → JPEG, **esportazione in JPEG**, **Salva raccolta…**
  come struttura di cartelle.
- **App per Mac**: trascinamento nel Finder (copia), ⌘C per copiare le foto, menu nativi,
  più cataloghi con protezione a password.
- Aspetto scuro con accento blu, telaietti per le foto, pannello di sinistra allargabile.
