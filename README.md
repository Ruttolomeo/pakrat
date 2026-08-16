# pakrat

Gestore mod nativo Linux. Niente Wine, niente mod manager di Windows dentro un
prefix Proton.

Il nome viene da `.pak`, l'unita' di mod che i primi giochi supportati avevano in
comune (BG3 usa LSPK, MechWarrior 5 i Paks di Unreal) — e da *pack rat*, chi
accumula. Cyberpunk 2077 e' arrivato dopo e usa `.archive`, ma il soprannome e'
rimasto.

**Giochi supportati**

| Gioco | Mod | Stato attivo | Load order |
|---|---|---|---|
| Baldur's Gate 3 | un `.pak` per mod | presenza in `modsettings.lsx` | posizione nella lista |
| MechWarrior 5: Mercenaries | una cartella con `mod.json` | `modlist.json` | `defaultLoadOrder` per mod |
| Cyberpunk 2077 | un insieme di file sulla radice del gioco | presenza dei file | nome del file `.archive` |

Nato per sostituire BG3ModManager e Vortex, che su Linux girano solo dentro un
prefix Proton.

## Installazione

```
sudo pacman -S --needed python-lz4 pyside6 p7zip unrar   # Arch/CachyOS
git clone https://github.com/Ruttolomeo/pakrat.git ~/projects/pakrat
~/projects/pakrat/install.sh
```

`install.sh` crea i symlink in `~/.local/bin`, la voce di menu e l'icona (estratta
dal gioco se la trova). E' idempotente: si puo' rilanciare dopo un `git pull`.

I dati personali stanno in `~/.config/pakrat/` e **non** sono nel repo: API key,
associazioni a Nexus, backup di `modsettings.lsx`. Dopo un ripristino da zero
serve rimettere la chiave con `pakrat apikey`.

## Interfacce

Un unico core, tre modi per usarlo:

| Comando | Cosa fa |
|---|---|
| `pakrat-gui` | GUI Qt6 (PySide6), anche dal menu di Plasma come "BG3 Mods" |
| `pakrat` | menu: sceglie il gioco, poi ne apre l'interfaccia |
| `pakrat <comando>` | CLI, vedi sotto |

`pakrat` senza argomenti chiede quale gioco gestire, mostrando per ciascuno se e'
stato trovato e quante mod ha. Scegliendo BG3 si apre la TUI curses; MechWarrior 5
e Cyberpunk una TUI non ce l'hanno, quindi stampano l'elenco delle mod e i comandi
disponibili. I comandi diretti restano quelli di sempre, `pakrat list` compreso.

Il core (`pakrat`) non dipende da Qt: la GUI e' un frontend sostituibile.

## Comandi

```
pakrat                      menu di scelta del gioco
pakrat setup                wizard: sceglie la cartella di gioco
pakrat list                 elenco mod e load order
pakrat doctor               perche' una mod non si vede in gioco
pakrat add FILE.pak         installa un .pak
pakrat apikey CHIAVE        salva la Personal API Key di Nexus
pakrat match [N]            cerca su Nexus e associa (scelta interattiva)
pakrat link N URL|ID        associa a mano una mod al suo ID Nexus
pakrat discover             tenta l'associazione automatica via hash MD5
pakrat check                confronta le versioni locali con Nexus
pakrat update [N]           scarica e installa gli aggiornamenti
pakrat nxm nxm://...        handler dei link "Mod Manager Download"
pakrat handler              registra l'handler nxm:// nel sistema
pakrat backups              elenca i backup di modsettings.lsx
pakrat restore N            ripristina un backup
```

### MechWarrior 5: Mercenaries

I comandi del gioco stanno sotto `pakrat mw5`. La API key e la cache sono in
comune col resto del tool, il resto e' separato.

```
pakrat mw5 list             elenco mod, stato e load order
pakrat mw5 add ARCHIVIO     installa da zip/7z/rar; se l'archivio contiene piu'
                            mod chiede quali attivare (--no-enable / --enable-all)
pakrat mw5 enable MOD       attiva (indice, nome cartella o nome visualizzato)
pakrat mw5 disable MOD      disattiva
pakrat mw5 order MOD N      imposta il load order (piu' alto = caricata dopo)
pakrat mw5 order --seq A B  riassegna l'ordine nella sequenza data
pakrat mw5 order --apply    ri-applica l'ordine salvato nel config
pakrat mw5 remove MOD       sposta in archivio (--purge per cancellare davvero)
pakrat mw5 restore [N]      elenca l'archivio, o ripristina una mod rimossa
pakrat mw5 link MOD ID      associa una mod alla sua pagina Nexus
pakrat mw5 check            cerca aggiornamenti
pakrat mw5 update [MOD]     scarica e installa gli aggiornamenti
pakrat mw5 prune            togli da modlist.json le voci senza cartella
pakrat mw5 doctor           perche' una mod non si vede: versioni, crash, log
pakrat mw5 setup [PERCORSO] mostra o imposta l'installazione
```

Tre particolarita' di MW5, tutte verificate sul gioco:

- Le mod stanno nella cartella di **installazione** (`<install>/MW5Mercs/Mods/`),
  non nel prefix Wine come in BG3. L'identita' di una mod e' il nome della sua
  cartella, non un UUID.
- Il load order non e' centralizzato: e' il campo `defaultLoadOrder` dentro il
  `mod.json` di ogni mod, cioe' un file scritto dall'autore. Un aggiornamento lo
  sovrascrive, quindi `pakrat` tiene l'ordine autorevole nel proprio config e lo
  ri-applica dopo ogni `update`.
- Il gioco riscrive `modlist.json` quando esce: se e' aperto, `pakrat` si rifiuta
  di scrivere invece di perdere le modifiche in silenzio.

Un archivio MW5 puo' contenere piu' cartelle-mod: tipicamente il mod principale
piu' patch opzionali, che dipendono da altre mod e non vanno attivati alla cieca.
In quel caso `add` le installa tutte ma chiede quali attivare, mostrando le righe
`REQUIRES` / `DO NOT USE` pescate dalla descrizione di ogni `mod.json`.

`check` confronta il `file_id` di Nexus, non la stringa di versione: autori e file
sono numerati in modo indipendente, e un confronto testuale segnalava come
aggiornamento anche i downgrade. Il confronto avviene dentro la stessa variante:
certe mod pubblicano piu' file principali che sono alternative fra loro (es. i
pacchetti di ritratti, con e senza sfondi), non versioni successive, e un update
non deve sostituirne una con l'altra.

`remove` non cancella: sposta la mod in `pakrat-mods-rimosse/` accanto
all'installazione, sullo stesso filesystem — quindi e' un rename istantaneo anche
per una mod da un gigabyte, e si ripristina con `restore`. Il load order resta nel
config, cosi' un ripristino non riparte dal `defaultLoadOrder` dell'autore. Serve
perche' togliere contenuto a cui una carriera fa riferimento puo' romperne il
salvataggio, e conviene poter tornare indietro. `--purge` cancella davvero, e
chiede conferma esplicita mostrando quanti GB stai per perdere.

### Cyberpunk 2077

I comandi stanno sotto `pakrat cp2077`. API key e cache sono in comune col resto
del tool.

```
pakrat cp2077 list          elenco mod, stato, ordine e numero di file
pakrat cp2077 add ARCHIVIO  installa da zip/7z/rar
                            --no-enable installa senza attivare
                            --name NOME forza il nome della mod
pakrat cp2077 enable MOD    attiva (rimette i file in gioco)
pakrat cp2077 disable MOD   disattiva (sposta i file nel deposito)
pakrat cp2077 order MOD N   prefisso NNN_ sugli .archive ('-' per toglierlo)
pakrat cp2077 remove MOD    sposta in archivio (--purge per cancellare davvero)
pakrat cp2077 restore [N]   elenca l'archivio, o ripristina una mod rimossa
pakrat cp2077 verify        confronta il manifest col disco, trova gli orfani
pakrat cp2077 deps          stato dei core mod e prerequisiti dedotti
pakrat cp2077 bootstrap     scarica e installa i core mod mancanti, in ordine
                            NOME==1.36.0 fissa una versione, NOME==latest libera
                            --dry-run mostra cosa farebbe, --force reinstalla
pakrat cp2077 deploy        cosa serve per far caricare i REDmod
pakrat cp2077 doctor        dopo una partita: cosa il gioco ha caricato davvero
pakrat cp2077 search TERM   cerca mod su Nexus per nome (--limit N)
pakrat cp2077 get ID        scarica e installa da Nexus per ID (premium)
                            --with-reqs installa prima i prerequisiti dedotti
pakrat cp2077 reqs ID       cosa pretende una mod, dedotto dalla sua pagina
pakrat cp2077 body [N]      elenca i corpi opzionali, o ne installa uno con
                            la sua catena (--dry-run mostra e basta)
pakrat cp2077 link MOD ID   associa una mod alla sua pagina Nexus
pakrat cp2077 check         cerca aggiornamenti
pakrat cp2077 update [MOD]  scarica e installa gli aggiornamenti
pakrat cp2077 setup [PATH]  mostra o imposta l'installazione
```

Cyberpunk rompe l'assunto degli altri due backend, cioe' che una mod sia **una**
unita' nel filesystem. Qui una mod tipica si spalma sulla radice del gioco:

| dove | cosa |
|---|---|
| `archive/pc/mod/*.archive` (+ `.xl`) | contenuto; il grosso delle mod |
| `mods/<nome>/info.json` | REDmod |
| `r6/scripts`, `r6/tweaks` | redscript, TweakXL |
| `red4ext/plugins/`, `bin/x64/plugins/cyber_engine_tweaks/mods/` | RED4ext, CET |

Quindi l'identita' di una mod non puo' essere un percorso: pakrat tiene il
**manifest** dei file che ogni archivio ha installato, e su quello poggiano
disattiva, rimuovi e ripristina. E' un piccolo gestore di pacchetti con un solo
repository: l'archivio che ti sei scaricato.

Quattro conseguenze, tutte volute:

- **Disattivare non cancella**: sposta i file in `pakrat-cp2077/disattivate/`
  accanto all'installazione, sullo stesso filesystem — quindi e' un rename anche
  per una mod da un gigabyte. Non si rinomina in `.disabled` perche' funzionerebbe
  per gli `.archive` ma non per `r6/scripts` o i plugin, caricati per estensione
  o per cartella: spostare e' l'unica regola che vale per tutti i tipi.
- **Quello che viene coperto viene messo da parte** prima di essere sovrascritto,
  file di gioco o file di un'altra mod che sia, e torna al suo posto con `remove`.
  Senza, disinstallare una mod lascerebbe un buco al posto di un file del gioco.
- **Un file conteso appartiene a chi l'ha scritto per ultimo**, ed e' l'unica mod
  che puo' spostarlo. Chi ha perso il conflitto continua a dichiararlo — se il
  vincitore se ne va il file torna suo — ma `disable` e `remove` glielo lasciano
  stare, altrimenti disinstallare la mod perdente smonterebbe pezzi di quella
  vincente.
- `archive/pc/content`, `archive/pc/ep1` e `tools/redmod/` sono **protetti**,
  insieme all'eseguibile del gioco, a `oo2ext_7_win64.dll` e a
  `REDprelauncher.exe`: un archivio che prova a scriverci viene installato lo
  stesso, ma quei file vengono saltati e segnalati. `engine/` **non** e' protetta
  in blocco, perche' redscript si installa proprio li' dentro
  (`engine/tools/scc.exe`, `engine/config/base/scripts.ini`) e vietarlo lo
  installerebbe con zero file; quei file di gioco li salva comunque il
  meccanismo qui sopra.

Il load order degli `.archive` e' l'ordine **ASCII**-alfabetico del nome file
(maiuscole prima delle minuscole), e in Cyberpunk **vince il primo caricato**: il
conflitto si risolve per singolo file e se lo tiene il primo mod che lo modifica.
E' il contrario di MechWarrior 5, dove l'ultimo sovrascrive — l'errore piu' facile
da fare passando da un gioco all'altro. Quindi **numero piu' basso = vince**.

`order MOD N` applica un prefisso `NNN_`, rinominando insieme il compagno
`.archive.xl` cosi' la coppia non si separa. I `.archive` dentro una cartella
REDmod non si toccano: i REDmod si caricano tutti *dopo* gli `.archive` e non
partecipano a quell'ordine.

Esiste anche `archive/pc/mod/modlist.txt`, che impone un ordine esplicito, e
pakrat **non lo usa di proposito**: gli archivi non elencati la' dentro rischiano
di non caricarsi affatto, e la documentazione ufficiale consiglia di cancellarlo.
Il prefisso sul nome e' reversibile e non puo' far sparire una mod.

`verify` confronta il manifest col disco: dice quali file mancano all'appello e
quali file in `archive/pc/mod` non sono gestiti da pakrat (installati a mano).

### Cercare mod

`pakrat cp2077 search TERMINE` cerca su Nexus per nome, alla maniera di `apt
search`, e segna con `*` quelle che hai gia' installato e collegato.

```
  #      ID  MOD                                            VERSIONE  AUTORE
  1*   6945  Equipment-EX                                      1.2.9  psiberx
  2    7049  Toggleable Feet for V - ArchiveXL and Equipm        1.0  xBaebsae
```

Una cosa e' bene saperla, perche' altrimenti sembra che la ricerca funzioni male:
**il filtro di Nexus fa match su sottostringa, non su parole simili**. Un refuso
dentro una parola (`equpment`) non restituisce niente, e da qui non si puo'
rimediare — servirebbe un indice locale di tutte le mod del gioco. Quello che
pakrat fa e' cercare anche le singole parole quando la frase intera non da'
risultati, e riordinare per somiglianza cio' che torna, cosi' il risultato piu'
pertinente sta in cima invece che in mezzo.

Trovato l'ID, `pakrat cp2077 get 6945` scarica e installa in un colpo solo, e
registra da se' l'associazione a Nexus — quindi `check` e `update` funzionano
subito, senza passare da `link`. Serve un account **premium**: l'API i link di
download diretti li da' solo a quelli, e senza si ricade sulla pagina del sito
(o sul pulsante "Mod Manager Download", che pakrat gestisce via `nxm://`).
`--file FILE_ID` sceglie un file preciso invece del principale, per le mod che
ne offrono diverse varianti.

**L'ID Nexus vale anche per indicare una mod gia' installata**: `remove 6945`,
`disable 6945`, `order 6945 10`. Un numero e' prima di tutto l'indice della
lista, che e' quello che si ha sotto gli occhi; se non esiste un indice cosi', si
prova come ID Nexus, che e' sempre molto piu' grande. Nel dubbio, `id:6945` e'
esplicito.

### Prerequisiti (core mod)

Quasi ogni mod moderna di Cyberpunk poggia su un framework che non aggiunge
contenuto ma lo rende possibile. Due sono nativi e senza prerequisiti a loro
volta — **RED4ext** (`red4ext/plugins/`, carica i plugin `.dll`) e **redscript**
(`r6/scripts/`, compila gli script) — e sopra RED4ext stanno **Cyber Engine
Tweaks** (strato Lua), **ArchiveXL** (carica risorse nuove senza sostituire le
originali), **TweakXL** (TweakDB da `.yaml`/`.tweak`) e **Codeware** (libreria
per redscript e CET).

L'API pubblica di Nexus **non espone** la lista "Requirements" di una mod: sta
solo nella pagina web. Quindi pakrat non puo' leggere le dipendenze dichiarate.
Fa una cosa diversa e per i core mod piu' affidabile: le **deduce dai file che la
mod ha davvero installato**, che sono gia' nel manifest.

| se la mod contiene | allora pretende |
|---|---|
| un `.xl` accanto all'`.archive` | ArchiveXL |
| roba in `r6/tweaks/` | TweakXL |
| `.reds` in `r6/scripts/` | redscript |
| qualcosa in `red4ext/plugins/` | RED4ext |
| una cartella sotto `cyber_engine_tweaks/mods/` | Cyber Engine Tweaks |

I framework stessi si rilevano sul disco dalla loro cartella. `add` avvisa subito
se ne manca uno, `verify` lo segnala e `deps` mostra il quadro completo. Serve
perche' un prerequisito mancante di solito **non da' errore**: la mod semplicemente
non fa niente.

Resta fuori quello che non si deduce dai file: le dipendenze fra mod normali
(«questa richiede quell'altra mod di abiti») vanno lette sulla pagina Nexus.

### Installarli automaticamente

`pakrat cp2077 bootstrap` scarica e installa i core mod mancanti da solo, e
**quando ne manca qualcuno lo propone**: dopo un `add` che ne pretende uno, e in
fondo a `deps`. La domanda arriva solo se c'e' davvero un terminale a
risponderti — da script, da cron o dalla GUI stampa il comando e tira dritto,
invece di piantarsi su un prompt che nessuno vedra'.

Scarica **da GitHub, non da Nexus**. Sono tutti progetti open source con release
pubbliche, quindi il link e' diretto, versionato e non serve un account: l'API di
Nexus i link di download diretti li da' solo agli utenti premium. Non ci sono ID
Nexus cablati nel codice, che cambiano nel tempo — ci sono i repository, che no.

| core mod | repository |
|---|---|
| RED4ext | `wopss/RED4ext` |
| redscript | `jac3km4/redscript` |
| Cyber Engine Tweaks | `maximegmd/CyberEngineTweaks` |
| ArchiveXL | `psiberx/cp2077-archive-xl` |
| TweakXL | `psiberx/cp2077-tweak-xl` |
| Codeware | `psiberx/cp2077-codeware` |

L'ordine di quella tabella **e' l'ordine di installazione**: prima RED4ext e
redscript, che non dipendono da nessuno, poi i quattro che poggiano su RED4ext.

Installa solo quello che serve davvero: se accetti la proposta partita da una mod
che vuole ArchiveXL e TweakXL, mette quelli e RED4ext sotto, non tutti e sei.
`bootstrap NOME...` ne prende di specifici, `--dry-run` mostra versioni e URL
senza toccare niente, `--force` reinstalla anche quello che c'e' gia'. Rilanciarlo
non fa danni: quello che e' aggiornato lo salta, e se su GitHub c'e' una versione
piu' nuova di quella registrata te lo dice e la aggiorna.

Vale la pena ricordare che questi sei sono codice nativo che gira dentro il gioco
via Proton: se dopo il bootstrap il gioco non parte piu', `pakrat cp2077 disable`
su quello appena messo e' il primo passo per capire chi e' — e il tool lo dice
anche a fine installazione.

### Fissare una versione

Il caso che rende necessario il pin e' preciso: **Cyberpunk si aggiorna e
l'ultima CET o redscript non e' ancora allineata**, quindi il gioco non parte.
Serve tornare a quella che funzionava, e soprattutto che non torni avanti da
sola al prossimo `bootstrap`.

```
pakrat cp2077 bootstrap cet==1.36.0    installa quella versione e la fissa
pakrat cp2077 bootstrap                le altre si aggiornano, CET no
pakrat cp2077 bootstrap cet==latest    toglie il pin e riprende l'ultima
```

Il pin viene **ricordato** (`pinned_version` nella config) ed e' l'unica cosa che
ferma un aggiornamento: senza, `bootstrap` prende sempre l'ultima release. Se
chiedi una versione che hai gia' installata, non reinstalla niente e mette solo
il pin. `deps` mostra quale versione c'e' e se e' fissata.

Il numero si scrive nudo (`1.36.0`) o col tag (`v1.36.0`), indifferentemente. I
core mod si indicano per nome o con gli abbreviativi d'uso — `cet`, `axl`, `txl`,
`reds`, `r4e` — e un nome che non corrisponde a niente e' un errore, non un
silenzioso "non ho fatto nulla".

### Capire se in gioco le mod ci sono davvero

`verify` guarda i file sul disco, che e' un'altra domanda: dice che la mod e'
installata, non che il gioco la carichi. `pakrat cp2077 doctor` risponde alla
seconda, leggendo cio' che i framework hanno scritto **girando dentro il gioco**.

Serve a distinguere i due casi che da fuori si somigliano — "la mod non c'e'" e
"la mod c'e' ma non viene caricata" — e su Linux il secondo e' il piu' comune.
RED4ext e Cyber Engine Tweaks non sono plugin: si installano come
`bin/x64/winmm.dll` e `bin/x64/version.dll`, cioe' si **sostituiscono a una DLL
di sistema** che l'eseguibile carica comunque. Se Proton non le carica, non
succede assolutamente niente e nessuno protesta.

Per ogni loader `doctor` dice due cose diverse: se il **file** c'e', e se ha
**scritto** un log, cioe' se ha girato davvero. Un loader installato che non ha
mai scritto niente e' il sintomo, e la causa quasi sempre e' una sola.

**L'override delle DLL.** Sotto Proton, Wine preferisce le proprie `winmm` e
`version` a quelle della cartella del gioco: la DLL del mod resta un file inerte,
senza un errore e senza una riga di log. Il gioco parte, gli `.archive` si
vedono, e tutto cio' che passa da RED4ext o CET semplicemente non esiste —
ArchiveXL, TweakXL e Codeware compresi, che risultano installati e non li carica
nessuno. La cura e' una variabile d'ambiente:

    WINEDLLOVERRIDES=winmm,version=n,b

Non sta nell'installazione ma nella configurazione del launcher, quindi e'
l'unico pezzo dello stack che non si vede guardando i file del gioco. Per questo
pakrat **la legge davvero** invece di limitarsi a consigliarla: trova il gioco
nell'indice di Heroic, apre il suo `GamesConfig/<appName>.json` e controlla se
`winmm` e `version` sono forzate native (anche ereditate dalle impostazioni
globali). `doctor` lo riporta sempre, `verify` e `deps` avvisano in una riga, e
`bootstrap` lo dice subito dopo aver installato i loader, che e' il momento in cui
serve saperlo. Se manca, `doctor` si offre di scriverla lui — con backup, e solo
a Heroic chiuso, perche' Heroic tiene quella configurazione in memoria e una
modifica fatta mentre e' aperto sparirebbe in silenzio. Su Steam non si legge
niente e si dice come metterla nelle opzioni di avvio, invece di fingere una
diagnosi.

Sui log, `doctor` separa l'**ultima corsa** dalle precedenti. Serve piu' di
quanto sembri: redscript ruota il proprio log all'**inizio** della corsa nuova,
quindi `redscript_r<data>.log` contiene sempre la partita di prima per quanto
recente sia il suo mtime, ed e' la trappola per cui si legge un errore gia'
risolto e lo si insegue una seconda volta. Se gli errori stanno solo li', il
comando lo dice a chiare lettere. Resta l'avviso di quando i log sono **piu'
vecchi dell'ultima modifica alle mod**: stai leggendo la configurazione
precedente, rilancia il gioco.

Le cartelle si scandiscono (`red4ext/logs`, `r6/logs`, quella di CET) invece di
cercare nomi di file precisi: le convenzioni cambiano fra versioni, l'esistenza
di un log fresco no.

**Cosa e' condiviso e cosa no.** La lettura dei log e la loro resa a video stanno
nel core (`scan_logs`, `split_runs`, `report_logs`): la tabella, il "quanto fa" e
la divisione fra ultima corsa e precedenti sono le stesse per qualunque gioco, e
un `doctor` per un altro titolo parte da li'. Il core pero' non interpreta:
riceve dal backend le cartelle dove guardare (relative all'installazione o
assolute, per chi tiene i log nel prefix), la regex di cosa e' un errore e il
predicato che riconosce un log ruotato. Il significato — quale DLL e' quale mod,
cosa vuol dire un import irrisolto — resta nel backend, perche' e' l'unica parte
che non si generalizza senza diventare vaga.

### Lo stesso per gli altri due giochi

`doctor` esiste anche per BG3 (`pakrat doctor`) e MW5 (`pakrat mw5 doctor`), ma
guarda cose diverse, perche' diversi sono i giochi. In comune c'e' solo la resa a
video, e per BG3 e MW5 quasi non serve: **nessuno dei due scrive un log di gioco**.
Larian non ne scrive affatto; MW5 e' UE4 shipping e in `Saved/Logs` lascia solo il
`cef3.log` del Chromium dei menu, a meno di lanciarlo con `-log` — cosa che
`doctor` dice, con l'argomento da aggiungere in Heroic.

Quindi le domande cambiano.

**BG3** — la stessa meccanica di Cyberpunk sta in Script Extender, che si installa
come `bin/DWrite.dll` e si sostituisce a una DLL di sistema esattamente come
RED4ext: se Proton non lo carica, le mod che dipendono da lui non fanno niente e
non protesta nessuno. Che sia stato caricato lo si vede dal suo aggiornatore, che
scrive nel prefix (`AppData/Local/BG3ScriptExtender`); se non l'ha mai fatto,
`doctor` passa al controllo degli override — lo stesso di Cyberpunk, con `dwrite`
al posto di `winmm`. Poi confronta `modsettings.lsx` con i `.pak` presenti: una
voce attiva rimasta senza il suo modulo e' il modo classico in cui una mod
"installata" non c'e'.

**MW5** — niente loader nativi, quindi niente override. Le domande sono altre: se
una mod dichiara una versione del gioco diversa da quella corrente (confrontata
per **serie**, `1.14` contro `1.13`, non per patch: in MW5 quasi nessun `mod.json`
e' allineato alla patch e segnalarle tutte vorrebbe dire segnalare 12 mod su 13),
se `modlist.json` conosce le cartelle che ci sono davvero, e soprattutto i **crash
dump**, che UE4 lascia sempre in `Saved/Crashes`. Di ognuno si legge tipo e
messaggio, e si guarda se il messaggio nomina una mod — UE4 nel testo dell'errore
mette il percorso dell'oggetto che ha fatto saltare tutto, e per una mod quel
percorso contiene il nome della sua cartella. E' un'euristica, ma quando becca
qualcosa e' esattamente quello che stavi cercando.

### Dipendenze fra mod, e i corpi

I Requirements di una mod l'API **li espone, ma solo via GraphQL**: l'API v1,
quella dei `/mods/ID.json` che si usa per tutto il resto, non li ha, e il tipo
`Mod` di GraphQL si' — `modRequirements.nexusRequirements` e' esattamente la
tabella che l'autore compila a mano e che sul sito si legge sotto *Nexus
requirements*, note comprese. `pakrat cp2077 reqs ID` legge quella.

Le note fanno il grosso del lavoro di classificazione, perche' gli autori ci
scrivono dentro quanto una dipendenza sia vincolante: *SOFT REQ* e *not mandatory*
diventano **consigliato**, *only if you have the DLC* diventa **condizionato**.
E quest'ultima condizione pakrat la sa valutare da solo — Phantom Liberty o c'e'
in `archive/pc/ep1` o non c'e' — quindi il refit della DLC entra nella catena solo
se la DLC ce l'hai.

Quando la tabella e' **vuota** (succede: non tutti la compilano) si ripiega sulla
descrizione, dove i prerequisiti gli autori li linkano comunque, e un link
contiene l'ID. Li' *richiesto* e *citato* si deducono dalla frase in cui il link
compare, o dalla sezione (una riga corta tipo "1. Install requirements" apre un
blocco). E' un'euristica, `reqs` dice sempre da quale delle due fonti sta
leggendo, e in nessuno dei due casi si installa qualcosa senza aver mostrato prima
la catena. `get --with-reqs` scende di **un solo livello**.

Un prerequisito gia' soddisfatto viene saltato, e non solo guardando gli ID Nexus
gia' associati: i core mod pakrat li prende dalle **release GitHub**, quindi nel
config un ID Nexus non ce l'hanno. Il confronto avviene allora sul nome, contro la
tabella `FRAMEWORKS`; senza, una catena reinstallerebbe ArchiveXL da Nexus sopra
quello che c'e' gia'.

**`pakrat cp2077 body`** e' l'applicazione pratica. Cyberpunk non ha *il* body
replacer, ma nemmeno una scelta larga: ha **due basi** — VTK, che rifa' mesh e
texture tenendo le proporzioni vanilla, e spawn0, che le proporzioni le cambia da
menu senza toccare la mesh — e sopra VTK una serie di **varianti** di silhouette,
ognuna col proprio pacchetto di refit. Basi e varianti si escludono a vicenda
quando toccano la stessa mesh. Il comando elenca i piu' diffusi con il numero di utenti reale —
preso dal feed statistico pubblico di Nexus (una richiesta, nessuna chiave, nessuna
quota consumata) e non da una tabella che invecchia — e installando ne risolve la
catena, avvisando se un altro corpo per lo stesso personaggio e' gia' li'.

L'elenco dei corpi e' l'unico pezzo scritto a mano, e non poteva essere altrimenti:
l'API non sa dire ne' "questa mod e' un corpo" ne' "questa esclude quest'altra".
Con esso il comando dichiara la cosa che conta davvero e che nessuna automazione
risolve — un corpo *sculpt* rifa' la mesh, quindi i **vestiti** vanno rifatti su
quella forma, e i refit sono mod a parte, una per outfit. Un corpo *rig* cambia le
proporzioni dello scheletro e non ne ha bisogno.

**REDmod**: pakrat prepara `mods/` ma **non lancia `redMod.exe deploy`**, che e' un
eseguibile Windows — questo tool non dipende da Wine e non e' il caso di iniziare
qui. Il deploy lo fa REDprelauncher all'avvio, se il gioco parte con `-modded`.
`pakrat cp2077 deploy` non deploya: dice cosa manca e dove mettere il flag.

## Sicurezza dei dati

`modsettings.lsx` decide se il gioco parte, quindi ogni scrittura ha tre reti:

- backup automatico prima di ogni modifica (ultimi 30, in `~/.config/pakrat/backups`)
- validazione XML del contenuto generato prima di toccare l'originale
- rifiuto di scrivere se mancano i moduli base (`GustavX`, `HonourX`) o se la lista e' vuota

La scrittura e' atomica. Gli aggiornamenti conservano il `.pak` precedente in
`~/.config/pakrat/old-paks/` invece di cancellarlo.

## Rilevamento dell'installazione

Cerca da solo la cartella dati di Larian in Steam (`compatdata/1086940`, leggendo
`libraryfolders.vdf`), Heroic, Lutris, Bottles e `~/.wine`.

Se non trova nulla parte un wizard che mostra le installazioni rilevate e permette
di indicare un percorso a mano, con validazione (dice cosa manca invece di
rifiutare e basta). Si rilancia con `pakrat setup`, o dalla GUI con
*File -> Cartella di gioco...*.

In alternativa: variabile `PAKRAT_LARIAN` oppure chiave `larian_dir` in
`~/.config/pakrat/config.json`.

Per MechWarrior 5 cerca la cartella di installazione leggendo i JSON delle
librerie di Heroic (Epic/GOG/Amazon) e le librerie Steam. In alternativa:
`PAKRAT_MW5_DIR` oppure `pakrat mw5 setup PERCORSO`.

Per Cyberpunk 2077 stessa ricerca (Heroic e Steam), riconoscendo la radice da
`bin/x64/Cyberpunk2077.exe`. In alternativa: `PAKRAT_CP2077_DIR` oppure
`pakrat cp2077 setup PERCORSO`.

Il tool si chiamava `bg3mods` finche' gestiva solo BG3: al primo avvio sposta
`~/.config/bg3mods` su `~/.config/pakrat` e le vecchie variabili `BG3MODS_*`
continuano a funzionare.

## Nexus Mods

Serve una Personal API Key (nexusmods.com -> Site preferences -> API Keys).

Il `.pak` non contiene l'ID Nexus della mod, quindi va associato una volta:
automaticamente se scaricata via `nxm://`, altrimenti con `match` (ricerca fuzzy
per nome e autore, con scelta manuale fra i candidati) o `link`.

**Account premium**: `update` scarica e installa da solo.
**Account free**: Nexus non rilascia link diretti all'API; il tool apre la pagina
della mod, si clicca "Mod Manager Download" e l'handler `nxm://` raccoglie il file.

## Dipendenze

- Python 3, `python-lz4` (decompressione dei blocchi LSPK)
- `pyside6` solo per la GUI
- `7z` / `unrar` solo per mod distribuite in archivi non-zip

## Licenza

GPL-3.0-or-later. Vedi [LICENSE](LICENSE).

## Stato

Uso personale. BG3 testato su un'installazione Heroic/GOG con 11 mod.

MechWarrior 5, su un'installazione Heroic/Epic: formato di `modlist.json` e
comportamento del load order verificati sul gioco. Il flusso `nxm://` completo e'
collaudato end-to-end — clic su "Mod Manager Download", download, estrazione,
installazione, attivazione, registrazione dell'ID Nexus — cosi' come `mw5 check`
contro l'API reale. Riordino e persistenza dell'ordine attraverso un update
collaudati su un'installazione di prova.

Cyberpunk 2077: backend nuovo, collaudato su un banco di prova con
installazione finta e config isolato — installazione da archivio nelle quattro
forme (struttura di gioco completa, `.archive` nudo, REDmod, archivio che tenta
di scrivere nel contenuto base), attiva/disattiva, `order`, conflitti fra mod
sullo stesso file lungo tutto il ciclo (chi vince tiene il file, rimuovere chi ha
perso non glielo porta via, rimuovere il vincitore lo restituisce al perdente),
`remove`/`restore`, ripristino di un file di gioco sovrascritto, `verify` e
rilevamento degli orfani.

Su un secondo banco sono stati installati i **sei core mod veri** (gli zip
scaricati di RED4ext, redscript, CET, ArchiveXL, TweakXL, Codeware): `deps` li
riconosce tutti, `verify` trova il manifest coerente, e `remove redscript` rimette
al suo posto lo `scripts.ini` originale del gioco.

`bootstrap` e' stato collaudato contro le **release GitHub reali**: tutti e sei i
repository rispondono e i pattern degli asset combaciano, l'installazione dei sei
va a buon fine su un'installazione finta, e rilanciarlo li salta. Provata anche la
proposta automatica in tutte e tre le strade — accettata (installa solo i quattro
che servivano, RED4ext per primo), rifiutata, e senza terminale (stampa il comando
senza chiedere).

Il pin e' collaudato sullo scenario per cui esiste, sempre contro release vere:
installata l'ultima CET, tornati a 1.36.0 (che si fissa da sola), verificato che
un `bootstrap` generico successivo la lasci dov'e' mentre aggiorna le altre, e
che `cet==latest` tolga il pin e riprenda l'ultima.

Non ancora verificati: il flusso `nxm://` per BG3 (quello di MW5 sì), `mw5 update`
su una mod che ha effettivamente una versione nuova, e l'estrazione di archivi
rar/7z per MW5 (per BG3 sì). Per Cyberpunk non e' ancora stata installata **una
mod vera su una partita vera**: il banco di prova copre la meccanica, non
l'ecosistema.
