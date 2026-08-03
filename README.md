# pakrat

Gestore mod nativo Linux. Niente Wine, niente mod manager di Windows dentro un
prefix Proton.

Il nome viene da `.pak`, l'unita' di mod che i giochi supportati hanno in comune
(BG3 usa LSPK, MechWarrior 5 i Paks di Unreal) — e da *pack rat*, chi accumula.

**Giochi supportati**

| Gioco | Mod | Stato attivo | Load order |
|---|---|---|---|
| Baldur's Gate 3 | un `.pak` per mod | presenza in `modsettings.lsx` | posizione nella lista |
| MechWarrior 5: Mercenaries | una cartella con `mod.json` | `modlist.json` | `defaultLoadOrder` per mod |

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
| `pakrat` | TUI curses |
| `pakrat <comando>` | CLI, vedi sotto |

Il core (`pakrat`) non dipende da Qt: la GUI e' un frontend sostituibile.

## Comandi

```
pakrat setup                wizard: sceglie la cartella di gioco
pakrat list                 elenco mod e load order
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
pakrat mw5 add ARCHIVIO     installa da zip/7z/rar (--no-enable per non attivare)
pakrat mw5 enable MOD       attiva (indice, nome cartella o nome visualizzato)
pakrat mw5 disable MOD      disattiva
pakrat mw5 order MOD N      imposta il load order (piu' alto = caricata dopo)
pakrat mw5 order --seq A B  riassegna l'ordine nella sequenza data
pakrat mw5 order --apply    ri-applica l'ordine salvato nel config
pakrat mw5 link MOD ID      associa una mod alla sua pagina Nexus
pakrat mw5 check            cerca aggiornamenti
pakrat mw5 update [MOD]     scarica e installa gli aggiornamenti
pakrat mw5 prune            togli da modlist.json le voci senza cartella
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

Non ancora verificati: il flusso `nxm://` per BG3 (quello di MW5 sì), `mw5 update`
su una mod che ha effettivamente una versione nuova, e l'estrazione di archivi
rar/7z per MW5 (per BG3 sì).
