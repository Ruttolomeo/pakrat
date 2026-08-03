#!/usr/bin/env python3
"""
mw5.py - backend MechWarrior 5: Mercenaries per pakrat.

Differenze rispetto a BG3, tutte verificate sul gioco e non dedotte:

  - le mod stanno nella cartella di INSTALLAZIONE, non nel prefix Wine:
    <install>/MW5Mercs/Mods/<NomeMod>/{mod.json,Paks/*.pak}
  - l'identita' di una mod e' il NOME DELLA CARTELLA (non un UUID)
  - lo stato attivo/disattivo sta in <install>/MW5Mercs/Mods/modlist.json:
        {"gameVersion": "...", "modStatus": {"<Cartella>": {"bEnabled": true}}}
  - il load order NON e' in modlist.json: e' il campo intero "defaultLoadOrder"
    dentro il mod.json di ogni singola mod, cioe' un file scritto dall'autore.
    Un aggiornamento della mod lo sovrascrive, quindi l'ordine autorevole lo
    teniamo noi nel config e lo ri-applichiamo dopo ogni update.
  - numero piu' alto = caricata dopo = vince sui file in conflitto
  - il gioco riscrive modlist.json all'uscita: se e' aperto non scriviamo nulla

Copyright (C) 2026 Ruttolomeo
SPDX-License-Identifier: GPL-3.0-or-later

Questo programma e' software libero: puoi ridistribuirlo e/o modificarlo secondo
i termini della GNU General Public License come pubblicata dalla Free Software
Foundation, versione 3 o (a tua scelta) una successiva. E' distribuito nella
speranza che sia utile, ma SENZA ALCUNA GARANZIA. Vedi il file LICENSE.
"""
import glob
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

NEXUS_GAME = "mechwarrior5mercenaries"
STEAM_APPID = "784080"
MODS_SUB = os.path.join("MW5Mercs", "Mods")
ORDER_STEP = 10          # passo fra due defaultLoadOrder consecutivi
_EXE_HINTS = ("mechwarrior.exe", "mw5mercs-win64-shipping.exe")


# ------------------------------------------------------------------- core ---
def core():
    """Il modulo pakrat, che ci presta config, API Nexus e download."""
    m = sys.modules.get("pakrat_core")
    if m is not None:
        return m
    import importlib.machinery
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "pakrat")
    loader = importlib.machinery.SourceFileLoader("pakrat_core", path)
    spec = importlib.util.spec_from_loader("pakrat_core", loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules["pakrat_core"] = m
    loader.exec_module(m)
    return m


def cfg_load():
    """Config condiviso col resto del tool. MW5 vive in una chiave sua: la parte
    BG3 (mods, larian_dir) non viene mai toccata, cosi' non serve migrazione."""
    cfg = core().load_config()
    ns = cfg.setdefault("mw5", {})
    ns.setdefault("install_dir", "")
    ns.setdefault("mods", {})
    return cfg, ns


def cfg_save(cfg):
    core().save_config(cfg)


# --------------------------------------------------------------- percorsi ---
def _heroic_config_dirs():
    for d in ("~/.config/heroic",
              "~/.var/app/com.heroicgameslauncher.hgl/config/heroic"):
        p = os.path.expanduser(d)
        if os.path.isdir(p):
            yield p


def _heroic_roots():
    """Cartelle di installazione note a Heroic.

    Legge i suoi JSON invece di tirare a indovinare coi glob: install_path e'
    esplicito nelle librerie Epic (legendary), GOG e Amazon (nile).
    """
    roots = []
    for h in _heroic_config_dirs():
        try:
            with open(os.path.join(h, "config.json")) as f:
                base = json.load(f).get("defaultSettings", {}).get("defaultInstallPath")
            if base:
                roots.append(os.path.expanduser(base))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        libs = ["store_cache/legendary_library.json", "store/legendary_library.json",
                "gog_store/installed.json", "store/gog_library.json",
                "nile_store/library.json", "store/nile_library.json"]
        for rel in libs:
            try:
                with open(os.path.join(h, rel)) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            entries = data if isinstance(data, list) else \
                (data.get("installed") or data.get("library") or data.get("games") or [])
            if not isinstance(entries, list):
                continue
            for e in entries:
                if not isinstance(e, dict):
                    continue
                p = e.get("install_path") or (e.get("install") or {}).get("install_path")
                if p:
                    roots.append(os.path.expanduser(p))
    return roots


def _steam_roots():
    roots = []
    try:
        libs = core()._steam_libraries()
    except Exception:
        libs = []
    for lib in libs:
        roots.append(os.path.join(lib, "steamapps/common/MW5Mercs"))
        roots.append(os.path.join(lib, "steamapps/common/MechWarrior 5 Mercenaries"))
    return roots


def _looks_like_install(p):
    """Una root valida contiene MW5Mercs/Mods (o almeno l'eseguibile)."""
    if not p or not os.path.isdir(p):
        return False
    return (os.path.isdir(os.path.join(p, MODS_SUB))
            or os.path.isfile(os.path.join(p, "MechWarrior.exe")))


def detect_install_dirs():
    """Cerca l'installazione di MW5: env, Heroic, Steam, prefix generici."""
    cands = []
    env = core().env_var("MW5_DIR")
    if env:
        cands.append(os.path.expanduser(env))
    cands += _heroic_roots()
    cands += _steam_roots()
    # le root di Heroic sono cartelle contenitore: guardiamoci dentro
    for r in list(cands):
        if os.path.isdir(r):
            cands += sorted(glob.glob(os.path.join(r, "*MW5*")))
            cands += sorted(glob.glob(os.path.join(r, "*MechWarrior*")))
    cands += sorted(glob.glob(os.path.expanduser(
        "~/.steam/steam/steamapps/compatdata/%s/pfx/drive_c/**/MW5Mercs" % STEAM_APPID)))

    seen, out = set(), []
    for c in cands:
        c = os.path.normpath(c)
        if c not in seen and _looks_like_install(c):
            seen.add(c)
            out.append(c)
    return out


def resolve_install_dir(ns=None):
    """1) config  2) rilevamento automatico  3) stringa vuota."""
    if ns is None:
        _cfg, ns = cfg_load()
    p = os.path.expanduser(ns.get("install_dir") or "")
    if _looks_like_install(p):
        return p
    found = detect_install_dirs()
    return found[0] if found else ""


def mods_dir(install=None):
    install = install or resolve_install_dir()
    return os.path.join(install, MODS_SUB) if install else ""


def modlist_path(install=None):
    md = mods_dir(install)
    return os.path.join(md, "modlist.json") if md else ""


# --------------------------------------------------- guardia gioco aperto ---
def game_running():
    """True se MW5 e' in esecuzione. Scansiona /proc: pgrep -f darebbe falsi
    positivi perche' il pattern compare nella riga di comando di pgrep stessa."""
    me = os.getpid()
    for entry in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            pid = int(entry.split("/")[2])
            if pid == me:
                continue
            with open(entry, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").lower()
        except (OSError, ValueError):
            continue
        if any(h in cmd for h in _EXE_HINTS):
            return True
    return False


def require_game_closed():
    if game_running():
        print("MechWarrior 5 e' in esecuzione: all'uscita riscriverebbe modlist.json\n"
              "cancellando le modifiche. Chiudi il gioco e riprova.", file=sys.stderr)
        return False
    return True


# ----------------------------------------------------------- modlist.json ---
def read_modlist(install=None):
    p = modlist_path(install)
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def backup_modlist(install=None):
    """Copia modlist.json nei backup prima di riscriverlo."""
    p = modlist_path(install)
    if not p or not os.path.isfile(p):
        return ""
    c = core()
    os.makedirs(c.BACKUP_DIR, exist_ok=True)
    dest = os.path.join(c.BACKUP_DIR,
                        "modlist-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(p, dest)
    return dest


def write_modlist(data, install=None):
    """Scrittura atomica di modlist.json, con backup.

    Preserva 'gameVersion' e ogni altra chiave che il gioco ci mette: scriviamo
    in un file dell'applicazione, non in uno nostro.
    """
    p = modlist_path(install)
    if not p:
        raise RuntimeError("installazione MW5 non trovata")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    backup_modlist(install)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    os.replace(tmp, p)
    return p


def status_map(install=None):
    d = read_modlist(install).get("modStatus")
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------- mod.json ---
class Mod:
    """Una mod presente in Mods/."""

    def __init__(self, folder, path, meta, enabled):
        self.folder = folder
        self.path = path
        self.meta = meta or {}
        self.enabled = enabled
        self.name = str(self.meta.get("displayName") or folder)
        self.version = str(self.meta.get("version") or "")
        self.build = self.meta.get("buildNumber")
        self.game_version = str(self.meta.get("gameVersion") or "")
        try:
            self.order = int(self.meta.get("defaultLoadOrder"))
        except (TypeError, ValueError):
            self.order = 0

    def __repr__(self):
        return f"<Mod {self.folder} order={self.order} enabled={self.enabled}>"


def mod_json_path(folder_path):
    return os.path.join(folder_path, "mod.json")


def read_mod_json(folder_path):
    try:
        with open(mod_json_path(folder_path), encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def scan_mods(install=None):
    """Elenca le mod installate, ordinate per load order crescente."""
    md = mods_dir(install)
    if not md or not os.path.isdir(md):
        return []
    st = status_map(install)
    out = []
    for name in sorted(os.listdir(md)):
        p = os.path.join(md, name)
        if not os.path.isdir(p):
            continue
        meta = read_mod_json(p)
        if meta is None:
            continue
        enabled = bool((st.get(name) or {}).get("bEnabled"))
        out.append(Mod(name, p, meta, enabled))
    out.sort(key=lambda m: (m.order, m.folder.lower()))
    return out


def find_mod(ref, mods=None, install=None):
    """Risolve una mod da indice (1-based), nome cartella o displayName."""
    mods = mods if mods is not None else scan_mods(install)
    s = str(ref).strip()
    if s.isdigit():
        i = int(s)
        if 1 <= i <= len(mods):
            return mods[i - 1]
    low = s.lower()
    for m in mods:
        if m.folder.lower() == low:
            return m
    for m in mods:
        if m.name.lower() == low:
            return m
    hits = [m for m in mods if low in m.folder.lower() or low in m.name.lower()]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------- attiva/disattiva ---
def set_enabled(folders, enabled, install=None):
    """Scrive bEnabled per le cartelle indicate. Ritorna i nomi applicati."""
    if not require_game_closed():
        return []
    data = read_modlist(install)
    if not data:
        # il gioco non ha ancora generato il file: creiamo lo stesso scheletro
        data = {"gameVersion": "", "modStatus": {}}
    st = data.setdefault("modStatus", {})
    if not isinstance(st, dict):
        st = data["modStatus"] = {}
    done = []
    for f in folders:
        entry = st.get(f)
        if not isinstance(entry, dict):
            entry = st[f] = {}
        entry["bEnabled"] = bool(enabled)
        done.append(f)
    write_modlist(data, install)
    return done


def prune_modlist(install=None):
    """Toglie da modStatus le voci di cartelle che non esistono piu'."""
    if not require_game_closed():
        return []
    data = read_modlist(install)
    st = data.get("modStatus")
    if not isinstance(st, dict):
        return []
    md = mods_dir(install)
    stale = [k for k in st if not os.path.isdir(os.path.join(md, k))]
    if stale:
        for k in stale:
            del st[k]
        write_modlist(data, install)
    return stale


# ------------------------------------------------------------ load order ---
def write_order(folder_path, order):
    """Riscrive defaultLoadOrder nel mod.json preservando tutto il resto.

    Il mod.json e' un file dell'autore: leggiamo, cambiamo una chiave, riscriviamo
    in modo atomico. Non lo rigeneriamo da zero.
    """
    meta = read_mod_json(folder_path)
    if meta is None:
        return False
    meta["defaultLoadOrder"] = int(order)
    p = mod_json_path(folder_path)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=3, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, p)
    return True


def remember_order(ns, folder, order):
    ns.setdefault("mods", {}).setdefault(folder, {})["order"] = int(order)


def apply_orders(install=None):
    """Ri-applica ai mod.json l'ordine autorevole salvato nel nostro config.

    Serve dopo ogni update: l'archivio della mod porta il defaultLoadOrder
    dell'autore e cancellerebbe la tua scelta senza dirti niente.
    """
    cfg, ns = cfg_load()
    changed = []
    for m in scan_mods(install):
        want = (ns.get("mods", {}).get(m.folder) or {}).get("order")
        if want is None or int(want) == m.order:
            continue
        if write_order(m.path, want):
            changed.append((m.folder, m.order, int(want)))
    return changed


def reorder(folders, install=None):
    """Assegna l'ordine seguendo la sequenza data, a passi di ORDER_STEP."""
    cfg, ns = cfg_load()
    applied = []
    for i, f in enumerate(folders, start=1):
        m = find_mod(f, install=install)
        if m is None:
            continue
        n = i * ORDER_STEP
        if write_order(m.path, n):
            remember_order(ns, m.folder, n)
            applied.append((m.folder, n))
    cfg_save(cfg)
    return applied


# ------------------------------------------------------- rimozione mod ---
ARCHIVE_DIRNAME = "pakrat-mods-rimosse"


def archive_dir(install=None, create=False):
    """Cartella d'archivio delle mod rimosse, accanto all'installazione.

    Sta sullo stesso filesystem del gioco, cosi' rimuovere e' un rename e non una
    copia da gigabyte, ed e' fuori da Mods/ quindi il gioco non la guarda.
    """
    install = install or resolve_install_dir()
    if not install:
        return ""
    outside = os.path.join(os.path.dirname(install), ARCHIVE_DIRNAME)
    inside = os.path.join(install, ARCHIVE_DIRNAME)
    for cand in (outside, inside):
        if os.path.isdir(cand):
            return cand
    if not create:
        return outside
    for cand in (outside, inside):
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except OSError:
            continue
    raise RuntimeError("nessuna cartella d'archivio scrivibile")


def forget_modlist(folders, install=None):
    """Toglie da modStatus solo le voci indicate, lasciando intatto il resto."""
    data = read_modlist(install)
    st = data.get("modStatus")
    if not isinstance(st, dict):
        return []
    gone = [f for f in folders if f in st]
    if gone:
        for f in gone:
            del st[f]
        write_modlist(data, install)
    return gone


def remove_mod(folder, install=None, purge=False, log=print):
    """Rimuove una mod: la sposta in archivio (o la cancella con purge=True).

    La voce nel db resta, con removed_at: conserva il load order scelto, cosi' un
    eventuale ripristino non riparte dal defaultLoadOrder dell'autore.
    """
    md = mods_dir(install)
    src = os.path.join(md, folder)
    if not os.path.isdir(src):
        raise RuntimeError(f"non installata: {folder}")
    if not require_game_closed():
        return ""
    if purge:
        shutil.rmtree(src)
        dest = ""
        log(f"  {folder}: cancellata")
    else:
        adir = archive_dir(install, create=True)
        dest = os.path.join(adir, f"{folder}-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            os.rename(src, dest)
        except OSError:                    # filesystem diversi: copia e cancella
            shutil.copytree(src, dest)
            shutil.rmtree(src)
        log(f"  {folder}: spostata in {dest}")
    forget_modlist([folder], install)
    cfg, ns = cfg_load()
    e = ns.setdefault("mods", {}).get(folder)
    if e is not None:
        if purge:
            del ns["mods"][folder]
        else:
            e["removed_at"] = int(time.time())
            e["archived_to"] = dest
        cfg_save(cfg)
    return dest


def list_archived(install=None):
    """Mod in archivio: [(cartella_originale, percorso, timestamp)]."""
    adir = archive_dir(install)
    if not adir or not os.path.isdir(adir):
        return []
    out = []
    for name in sorted(os.listdir(adir)):
        p = os.path.join(adir, name)
        if not os.path.isdir(p):
            continue
        m = re.match(r'^(.*)-(\d{8}-\d{6})$', name)
        out.append((m.group(1) if m else name, p, m.group(2) if m else ""))
    return out


def restore_mod(path, install=None, enable=True, log=print):
    """Rimette in Mods/ una mod archiviata."""
    if not os.path.isdir(path):
        raise RuntimeError(f"non trovata in archivio: {path}")
    if not require_game_closed():
        return ""
    m = re.match(r'^(.*)-\d{8}-\d{6}$', os.path.basename(path))
    folder = m.group(1) if m else os.path.basename(path)
    md = mods_dir(install)
    dest = os.path.join(md, folder)
    if os.path.exists(dest):
        raise RuntimeError(f"{folder} e' gia' presente in Mods/: "
                           "rimuovila prima di ripristinare l'archivio")
    os.makedirs(md, exist_ok=True)
    try:
        os.rename(path, dest)
    except OSError:
        shutil.copytree(path, dest)
        shutil.rmtree(path)
    log(f"  {folder}: ripristinata")
    cfg, ns = cfg_load()
    e = ns.setdefault("mods", {}).get(folder)
    if e is not None:
        e.pop("removed_at", None)
        e.pop("archived_to", None)
        cfg_save(cfg)
    for f, old, new in apply_orders(install):
        log(f"  ordine ri-applicato a {f}: {old} -> {new}")
    if enable:
        if set_enabled([folder], True, install):
            log(f"  attivata: {folder}")
    return folder


# ------------------------------------------------------------ estrazione ---
def _extract_archive(archive, outdir):
    """Estrae un archivio qualsiasi in outdir."""
    import subprocess
    import zipfile
    os.makedirs(outdir, exist_ok=True)
    low = archive.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(outdir)
        return
    tool = shutil.which("7z") or shutil.which("7za")
    if low.endswith(".rar") and shutil.which("unrar"):
        cmd = ["unrar", "x", "-y", "-idq", archive, outdir + os.sep]
    elif tool:
        cmd = [tool, "x", "-y", f"-o{outdir}", archive]
    else:
        raise RuntimeError(
            f"nessun estrattore per {os.path.basename(archive)} (installa p7zip o unrar)")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"estrazione fallita: {(r.stderr or r.stdout)[:200]}")


def find_mod_roots(tree):
    """Trova le cartelle-mod dentro un albero estratto.

    Gli archivi MW5 sono incoerenti sulla profondita': a volte mod.json e' in
    cima, a volte sotto uno o due livelli, a volte l'archivio contiene piu' mod.
    Cerchiamo tutte le cartelle che contengono un mod.json valido e scartiamo le
    annidate dentro un'altra cartella-mod.
    """
    roots = []
    for dirpath, _dirs, files in os.walk(tree):
        if any(f.lower() == "mod.json" for f in files) and read_mod_json(dirpath):
            roots.append(dirpath)
    roots.sort(key=lambda p: p.count(os.sep))
    out = []
    for r in roots:
        if not any(r.startswith(o + os.sep) for o in out):
            out.append(r)
    return out


def install_tree(src, install=None, force=True):
    """Copia una cartella-mod in Mods/. Ritorna (nome_cartella, era_update)."""
    md = mods_dir(install)
    if not md:
        raise RuntimeError("installazione MW5 non trovata")
    folder = os.path.basename(src.rstrip(os.sep))
    dest = os.path.join(md, folder)
    existed = os.path.isdir(dest)
    if existed and not force:
        raise RuntimeError(f"{folder} e' gia' installata (usa --force)")
    os.makedirs(md, exist_ok=True)
    if existed:
        # via il vecchio contenuto, ma solo dopo che il nuovo e' pronto accanto
        staging = dest + ".new"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(src, staging)
        old = dest + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.replace(dest, old)
        os.replace(staging, dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        shutil.copytree(src, dest)
    return folder, existed


_REQ_RE = re.compile(r'(requires?|do ?not use|don\'t use|incompatible|prerequisit)', re.I)


def meta_warnings(meta):
    """Righe di 'REQUIRES' / 'DO NOT USE' dalla descrizione di un mod.json.

    Sono l'informazione che decide se una mod va attivata o no, e stanno sepolte
    nella descrizione: vale la pena tirarle su prima di chiedere.
    """
    desc = str(meta.get("description") or "")
    desc = re.sub(r'\[/?[a-z0-9=#*\s"\'.,:;/-]+\]', '', desc, flags=re.I)
    out = []
    for line in desc.splitlines():
        line = line.strip()
        if line and _REQ_RE.search(line) and line not in out:
            out.append(line)
    return out[:3]


def choose_to_enable(candidates, log=print):
    """Chiede quali mod attivare quando un archivio ne contiene piu' di una.

    Un archivio con piu' cartelle-mod di solito e' un mod principale piu' patch
    opzionali, che non vanno attivati alla cieca: dipendono da altre mod che
    magari non hai. Senza un terminale non si attiva niente e si spiega come fare.
    """
    log(f"  l'archivio contiene {len(candidates)} mod:")
    for i, (folder, meta) in enumerate(candidates, 1):
        name = str(meta.get("displayName") or folder)
        nfiles = len(meta.get("manifest") or [])
        log(f"    {i}) {folder}  -  {name} {meta.get('version') or '?'}"
            f"  ({nfiles} file nel manifest)")
        for w in meta_warnings(meta):
            log(f"       ! {w[:140]}")
    if not sys.stdin.isatty():
        log("  nessun terminale per chiedere: non ne attivo nessuna.")
        log("  attivale con: pakrat mw5 enable NOME [NOME...]")
        return []
    folders = [f for f, _ in candidates]
    while True:
        try:
            ans = input("  quali attivo? [numeri, 'a' tutte, invio nessuna]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return []
        if not ans:
            return []
        if ans.lower() in ("a", "all", "t", "tutte"):
            return list(folders)
        picks, bad = [], False
        for tok in ans.replace(",", " ").split():
            if not tok.isdigit() or not 1 <= int(tok) <= len(folders):
                bad = True
                break
            f = folders[int(tok) - 1]
            if f not in picks:
                picks.append(f)
        if bad or not picks:
            print(f"  rispondi con numeri fra 1 e {len(folders)}, 'a', o invio")
            continue
        return picks


def install_archive(archive, install=None, enable=True, force=True, log=print):
    """Estrae un archivio e installa tutte le mod che contiene.

    'enable' decide cosa attivare dopo l'installazione:
      True    una sola mod -> attiva; piu' di una -> chiede quali
      "all"   attiva tutto senza chiedere
      "keep"  conserva lo stato che ogni cartella aveva (per gli update)
      False   non attiva niente

    Ritorna la lista dei nomi cartella installati.
    """
    archive = os.path.expanduser(archive)
    if not os.path.isfile(archive):
        raise RuntimeError(f"file non trovato: {archive}")
    if not require_game_closed():
        return []
    c = core()
    tmp = os.path.join(c.CONFIG_DIR, "cache", "mw5-extract")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _extract_archive(archive, tmp)
        roots = find_mod_roots(tmp)
        if not roots:
            raise RuntimeError(
                "nessun mod.json nell'archivio: non e' una mod MW5, oppure va "
                "installata a mano (leggi la descrizione su Nexus)")
        installed, candidates = [], []
        prev = status_map(install)          # stato prima di toccare niente
        cfg, ns = cfg_load()
        for r in roots:
            meta = read_mod_json(r) or {}
            candidates.append((os.path.basename(r.rstrip(os.sep)), meta))
            folder, was_update = install_tree(r, install, force=force)
            entry = ns.setdefault("mods", {}).setdefault(folder, {})
            entry["display_name"] = str(meta.get("displayName") or folder)
            entry["mod_version"] = str(meta.get("version") or "")
            entry["game_version"] = str(meta.get("gameVersion") or "")
            entry["installed_at"] = int(time.time())
            if "order" not in entry:
                try:
                    entry["order"] = int(meta.get("defaultLoadOrder"))
                except (TypeError, ValueError):
                    entry["order"] = (len(ns["mods"]) + 1) * ORDER_STEP
            log(f"  {'aggiornata' if was_update else 'installata'} {folder} "
                f"({entry['display_name']} {entry['mod_version'] or '?'})")
            installed.append(folder)
        cfg_save(cfg)
        # l'archivio porta il defaultLoadOrder dell'autore: rimettiamo il nostro
        for folder, old, new in apply_orders(install):
            log(f"  ordine ri-applicato a {folder}: {old} -> {new}")
        if installed:
            if enable == "keep":
                # update: si conserva lo stato che ogni cartella aveva
                want = [f for f in installed if (prev.get(f) or {}).get("bEnabled")]
            elif enable == "all":
                want = list(installed)
            elif enable:
                want = (choose_to_enable([c for c in candidates if c[0] in installed],
                                         log=log)
                        if len(installed) > 1 else list(installed))
            else:
                want = []
            if want:
                set_enabled(want, True, install)
                log(f"  attivate: {', '.join(want)}")
            elif enable:
                log("  nessuna attivata")
        return installed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- nexus ---
def nexus_get(path, api_key):
    return core().nexus_get(path, api_key, game=NEXUS_GAME)


def mod_page_url(mod_id, file_id=None):
    u = f"https://www.nexusmods.com/{NEXUS_GAME}/mods/{mod_id}?tab=files&nmm=1"
    if file_id:
        u += f"&file_id={file_id}"
    return u


def remote_version(info):
    return str(info.get("version") or "").strip()


def _vkey(v):
    """Chiave d'ordinamento tollerante per versioni tipo '2.1a', '0.98.5', '4.0'."""
    out = []
    for tok in re.findall(r'\d+|[A-Za-z]+', str(v or "")):
        out.append((0, int(tok), "") if tok.isdigit() else (1, 0, tok.lower()))
    return out


def usable_files(files):
    return [f for f in files
            if str(f.get("category_name") or "").upper() not in ("OLD_VERSION", "ARCHIVED")]


def remote_status(files, entry):
    """Confronta cosa c'e' su Nexus con cosa e' installato.

    Ritorna (stato, file_da_scaricare, versione_remota, nota). Stati:
      "update"   esiste una versione piu' nuova della SUA variante
      "ok"       sei all'ultima
      "variant"  stessa versione, file diverso: la mod pubblica piu' varianti
                 (es. ritratti con e senza sfondi) e non va scambiata
      "gone"     il file installato non e' piu' in elenco
      "unknown"  non sappiamo quale file fosse installato

    Il confronto e' fatto dentro la stessa variante, identificata dal nome del
    file: altrimenti un update ti sostituirebbe la variante scelta con un'altra.
    """
    us = usable_files(files)
    if not us:
        return "gone", None, "", "nessun file scaricabile"
    fid = entry.get("installed_file_id")
    if not fid:
        return "unknown", core().pick_main_file(us), "", ""
    inst = next((f for f in files if f.get("file_id") == fid), None)
    if inst is None:
        f = core().pick_main_file(us)
        return "gone", f, (f.get("version") or "").strip(), \
            "il file installato non e' piu' su Nexus"
    name = str(inst.get("name") or "").strip().lower()
    same = [f for f in us if str(f.get("name") or "").strip().lower() == name] or [inst]
    target = max(same, key=lambda f: _vkey(f.get("version")))
    iv = (inst.get("version") or "").strip()
    tv = (target.get("version") or "").strip()
    if _vkey(tv) > _vkey(iv):
        return "update", target, tv, ""
    # varianti = altri file della STESSA categoria alla stessa versione. I
    # "Source files" stanno in MISCELLANEOUS e non sono alternative giocabili.
    cat = str(inst.get("category_name") or "").upper()
    others = [f for f in us if f.get("file_id") != fid
              and str(f.get("category_name") or "").upper() == cat
              and _vkey(f.get("version")) == _vkey(iv)]
    if others:
        return "variant", None, iv, \
            "altre varianti alla stessa versione: " + ", ".join(
                str(f.get("name")) for f in others[:3])
    return "ok", None, iv, ""


def link_folder(folder, mod_id, ns, file_id=None, version=""):
    entry = ns.setdefault("mods", {}).setdefault(folder, {})
    entry["nexus_id"] = int(mod_id)
    if file_id:
        entry["installed_file_id"] = int(file_id)
    if version:
        entry["installed_version"] = version
    return entry


def parse_ref(s):
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    m = re.search(NEXUS_GAME + r"/mods/(\d+)", s)
    if m:
        return int(m.group(1))
    raise core().NexusError(f"non riesco a ricavare un ID Nexus da: {s}")


# ------------------------------------------------------------- comandi ---
def cmd_list(_args=None):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    cfg, ns = cfg_load()
    mods = scan_mods(install)
    ml = read_modlist(install)
    gv = ml.get("gameVersion") or "?"
    print(f"installazione: {install}")
    print(f"versione gioco: {gv}   mod: {len(mods)}\n")
    if not mods:
        print("nessuna mod in " + mods_dir(install))
        return 0
    print(f"{'#':>3} {'on':^3} {'ord':>5}  {'mod':<34} {'ver':<10} nexus")
    for i, m in enumerate(mods, 1):
        e = ns.get("mods", {}).get(m.folder, {})
        nx = e.get("nexus_id") or "-"
        warn = ""
        if m.game_version and gv != "?" and m.game_version != gv:
            warn = f"  ! per gioco {m.game_version}"
        print(f"{i:>3} {'X' if m.enabled else ' ':^3} {m.order:>5}  "
              f"{m.name[:34]:<34} {(m.version or '?')[:10]:<10} {nx}{warn}")
    orphans = [k for k in status_map(install) if not any(m.folder == k for m in mods)]
    if orphans:
        print("\nvoci in modlist.json senza cartella: " + ", ".join(orphans)
              + "\n  (ripulisci con: pakrat mw5 prune)")
    return 0


def _no_install():
    print("Installazione di MechWarrior 5 non trovata.\n\n"
          "Ho cercato nelle librerie Heroic (Epic/GOG/Amazon) e Steam.\n"
          "Se e' altrove, indicala con:\n\n"
          "  - variabile d'ambiente PAKRAT_MW5_DIR\n"
          "  - pakrat mw5 setup PERCORSO\n\n"
          "Deve puntare alla cartella che contiene MW5Mercs/ e MechWarrior.exe",
          file=sys.stderr)
    return 1


def cmd_setup(args):
    cfg, ns = cfg_load()
    if not args:
        found = detect_install_dirs()
        if not found:
            return _no_install()
        print("installazioni trovate:")
        for p in found:
            print("  " + p)
        print("\nusa: pakrat mw5 setup PERCORSO")
        return 0
    p = os.path.abspath(os.path.expanduser(args[0]))
    if not _looks_like_install(p):
        print(f"non sembra un'installazione di MW5: {p}\n"
              "manca MW5Mercs/Mods e MechWarrior.exe", file=sys.stderr)
        return 1
    ns["install_dir"] = p
    cfg_save(cfg)
    print(f"installazione MW5 impostata: {p}")
    print(f"cartella mod: {mods_dir(p)}")
    return 0


def cmd_add(args):
    if not args:
        print("uso: pakrat mw5 add ARCHIVIO.zip [--no-enable|--enable-all]",
              file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    enable = False if "--no-enable" in args else (
        "all" if "--enable-all" in args else True)
    files = [a for a in args if not a.startswith("--")]
    rc = 0
    for f in files:
        print(f"{os.path.basename(f)}:")
        try:
            got = install_archive(f, install, enable=enable)
        except Exception as ex:
            print(f"  errore: {ex}", file=sys.stderr)
            rc = 1
            continue
        if not got:
            rc = 1
    if rc == 0:
        print("\nfatto. Controlla l'ordine con: pakrat mw5 list")
    return rc


def cmd_enable(args, enabled=True):
    if not args:
        verb = "enable" if enabled else "disable"
        print(f"uso: pakrat mw5 {verb} MOD [MOD...]   (indice, cartella o nome)",
              file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    mods = scan_mods(install)
    targets, rc = [], 0
    for a in args:
        m = find_mod(a, mods, install)
        if m is None:
            print(f"mod non trovata (o ambigua): {a}", file=sys.stderr)
            rc = 1
            continue
        targets.append(m.folder)
    if not targets:
        return 1
    done = set_enabled(targets, enabled, install)
    if not done:
        return 1
    print(("attivate: " if enabled else "disattivate: ") + ", ".join(done))
    return rc


def cmd_order(args):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    if not args:
        print("uso:\n"
              "  pakrat mw5 order MOD N        imposta il load order di una mod\n"
              "  pakrat mw5 order --seq A B C  riassegna l'ordine nella sequenza data\n"
              "  pakrat mw5 order --apply      ri-applica l'ordine salvato nel config\n"
              "\nnumero piu' alto = caricata dopo = vince sui conflitti",
              file=sys.stderr)
        return 1
    if args[0] == "--apply":
        changed = apply_orders(install)
        if not changed:
            print("ordine gia' allineato al config")
        for folder, old, new in changed:
            print(f"{folder}: {old} -> {new}")
        return 0
    if args[0] == "--seq":
        applied = reorder(args[1:], install)
        for folder, n in applied:
            print(f"{folder}: {n}")
        return 0 if applied else 1
    if len(args) < 2 or not args[1].lstrip("-").isdigit():
        print("uso: pakrat mw5 order MOD N", file=sys.stderr)
        return 1
    m = find_mod(args[0], install=install)
    if m is None:
        print(f"mod non trovata (o ambigua): {args[0]}", file=sys.stderr)
        return 1
    n = int(args[1])
    if not write_order(m.path, n):
        print(f"mod.json non scrivibile per {m.folder}", file=sys.stderr)
        return 1
    cfg, ns = cfg_load()
    remember_order(ns, m.folder, n)
    cfg_save(cfg)
    print(f"{m.folder}: {m.order} -> {n}")
    return 0


def cmd_remove(args):
    if not args or all(a.startswith("--") for a in args):
        print("uso: pakrat mw5 remove MOD [MOD...] [--purge]\n"
              "  senza --purge la mod va in archivio e si puo' ripristinare\n"
              "  con --purge viene cancellata dal disco", file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    purge = "--purge" in args
    mods = scan_mods(install)
    targets, rc = [], 0
    for a in (x for x in args if not x.startswith("--")):
        m = find_mod(a, mods, install)
        if m is None:
            print(f"mod non trovata (o ambigua): {a}", file=sys.stderr)
            rc = 1
            continue
        targets.append(m.folder)
    if not targets:
        return 1
    if purge:
        tot = 0
        for f in targets:
            for root, _d, fs in os.walk(os.path.join(mods_dir(install), f)):
                tot += sum(os.path.getsize(os.path.join(root, x)) for x in fs
                           if os.path.exists(os.path.join(root, x)))
        print(f"cancellazione definitiva di {len(targets)} mod "
              f"({tot / (1 << 30):.1f} GB): {', '.join(targets)}")
        if sys.stdin.isatty():
            try:
                if input("confermi? [scrivi 'si']: ").strip().lower() not in ("si", "sì", "s"):
                    print("annullato")
                    return 1
            except (EOFError, KeyboardInterrupt):
                print("\nannullato")
                return 1
        else:
            print("nessun terminale per confermare: annullato", file=sys.stderr)
            return 1
    for f in targets:
        try:
            remove_mod(f, install, purge=purge)
        except Exception as ex:
            print(f"  {f}: {ex}", file=sys.stderr)
            rc = 1
    if not purge:
        print("ripristinabile con: pakrat mw5 restore")
    return rc


def cmd_restore(args):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    arch = list_archived(install)
    if not arch:
        print(f"archivio vuoto ({archive_dir(install)})")
        return 0
    enable = "--no-enable" not in args
    picks = [a for a in args if not a.startswith("--")]
    if not picks:
        print(f"mod in archivio ({archive_dir(install)}):\n")
        for i, (folder, p, ts) in enumerate(arch, 1):
            when = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}" if ts else "?"
            size = sum(os.path.getsize(os.path.join(r, x))
                       for r, _d, fs in os.walk(p) for x in fs
                       if os.path.exists(os.path.join(r, x)))
            print(f"  {i:>2}) {folder:<28} rimossa il {when}  {size / (1 << 30):.1f} GB")
        print("\nripristina con: pakrat mw5 restore N|NOME")
        return 0
    rc = 0
    for a in picks:
        hit = None
        if a.isdigit() and 1 <= int(a) <= len(arch):
            hit = arch[int(a) - 1]
        else:
            cands = [x for x in arch if x[0].lower() == a.lower()] or \
                    [x for x in arch if a.lower() in x[0].lower()]
            if len(cands) == 1:
                hit = cands[0]
            elif len(cands) > 1:
                print(f"ambiguo: {a} -> " + ", ".join(x[0] for x in cands), file=sys.stderr)
                rc = 1
                continue
        if hit is None:
            print(f"non in archivio: {a}", file=sys.stderr)
            rc = 1
            continue
        try:
            restore_mod(hit[1], install, enable=enable)
        except Exception as ex:
            print(f"  {hit[0]}: {ex}", file=sys.stderr)
            rc = 1
    return rc


def cmd_prune(_args=None):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    stale = prune_modlist(install)
    print("rimosse da modlist.json: " + ", ".join(stale) if stale
          else "nessuna voce orfana")
    return 0


def cmd_link(args):
    if len(args) < 2:
        print("uso: pakrat mw5 link MOD URL_O_ID_NEXUS", file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    m = find_mod(args[0], install=install)
    if m is None:
        print(f"mod non trovata (o ambigua): {args[0]}", file=sys.stderr)
        return 1
    c = core()
    try:
        mod_id = parse_ref(args[1])
    except c.NexusError as ex:
        print(str(ex), file=sys.stderr)
        return 1
    cfg, ns = cfg_load()
    api_key = cfg["nexus_api_key"]
    version = ""
    try:
        version = remote_version(nexus_get(f"/mods/{mod_id}.json", api_key))
    except c.NexusError as ex:
        print(f"attenzione: {ex}", file=sys.stderr)
    link_folder(m.folder, mod_id, ns, version=version or m.version)
    cfg_save(cfg)
    print(f"{m.folder} -> nexusmods.com/{NEXUS_GAME}/mods/{mod_id}")
    return 0


def cmd_check(_args=None):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    cfg, ns = cfg_load()
    c = core()
    api_key = cfg["nexus_api_key"]
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    mods = scan_mods(install)
    mapped = [m for m in mods if ns.get("mods", {}).get(m.folder, {}).get("nexus_id")]
    if not mapped:
        print("nessuna mod associata a Nexus (usa: pakrat mw5 link MOD ID)")
        return 0
    updates = unknown = 0
    for m in mapped:
        e = ns["mods"][m.folder]
        try:
            files = nexus_get(f"/mods/{e['nexus_id']}/files.json", api_key).get("files", [])
        except c.NexusError as ex:
            print(f"{m.name}: {ex}")
            continue
        state, _f, rv, note = remote_status(files, e)
        lv = e.get("installed_version") or m.version
        if state == "update":
            print(f"{m.name}: {lv or '?'} -> {rv or '?'}  AGGIORNAMENTO")
            updates += 1
        elif state == "gone":
            print(f"{m.name}: {lv or '?'}  ! {note}")
            unknown += 1
        elif state == "variant":
            print(f"{m.name}: {lv or '?'}  ok  ({note})")
        elif state == "unknown":
            print(f"{m.name}: {lv or '?'}  (file installato non registrato, "
                  "non verificabile)")
            unknown += 1
        else:
            print(f"{m.name}: {lv or '?'}  ok")
    print(f"\n{updates} aggiornamenti disponibili"
          + ("  (pakrat mw5 update)" if updates else ""))
    if unknown:
        print(f"{unknown} non verificabili: reinstallale con 'pakrat mw5 update MOD' "
              "per registrare il file di provenienza")
    return 0


def update_one(m, entry, api_key, install, log=print):
    """Scarica e installa la versione nuova di una mod. Ritorna (ok, messaggio)."""
    c = core()
    mod_id = entry.get("nexus_id")
    if not mod_id:
        return False, "non associata a Nexus"
    try:
        files = nexus_get(f"/mods/{mod_id}/files.json", api_key).get("files", [])
    except c.NexusError as ex:
        return False, str(ex)
    state, f, rv, note = remote_status(files, entry)
    if state == "ok":
        return False, f"gia' aggiornata ({rv or '?'})"
    if state == "variant":
        # Piu' file MAIN alla stessa versione sono varianti, non aggiornamenti:
        # scaricare l'altra sostituirebbe una scelta deliberata dell'utente.
        return False, (f"gia' aggiornata ({rv or '?'}); {note} — "
                       "per cambiare variante scaricala dal sito")
    if f is None:
        return False, "nessun file scaricabile"
    if state == "unknown":
        log("  attenzione: file di provenienza non registrato, "
            f"installo il principale ({f.get('name')})")
    # Senza premium l'API non rilascia link diretti: l'unica via e' il pulsante
    # "Mod Manager Download" sul sito, che genera un nxm:// per il nostro handler.
    if not c.is_premium(api_key):
        return False, "richiede download dal browser: " + mod_page_url(mod_id, f["file_id"])
    try:
        links = nexus_get(
            f"/mods/{mod_id}/files/{f['file_id']}/download_link.json", api_key)
    except c.NexusError as ex:
        return False, f"link non ottenibile: {ex}"
    if not links:
        return False, "nessun link: " + mod_page_url(mod_id, f["file_id"])

    cache = os.path.join(c.CONFIG_DIR, "cache")
    os.makedirs(cache, exist_ok=True)
    archive = os.path.join(cache, f["file_name"])
    log(f"  scarico {f['file_name']} ({f.get('size_kb', 0)/1024:.1f} MB)")
    try:
        c.download_url(links[0]["URI"], archive)
    except Exception as ex:
        return False, f"download fallito: {ex}"
    try:
        got = install_archive(archive, install, enable="keep", log=log)
    except Exception as ex:
        return False, str(ex)
    if not got:
        return False, "installazione non riuscita"
    cfg, ns = cfg_load()
    for folder in got:
        link_folder(folder, mod_id, ns, file_id=f["file_id"], version=rv)
    cfg_save(cfg)
    return True, f"aggiornata a {rv or '?'}"


def cmd_update(args):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    cfg, ns = cfg_load()
    api_key = cfg["nexus_api_key"]
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    mods = scan_mods(install)
    if args:
        m = find_mod(args[0], mods, install)
        if m is None:
            print(f"mod non trovata (o ambigua): {args[0]}", file=sys.stderr)
            return 1
        targets = [m]
    else:
        targets = [m for m in mods
                   if ns.get("mods", {}).get(m.folder, {}).get("nexus_id")]
    if not targets:
        print("nessuna mod associata a Nexus (usa: pakrat mw5 link MOD ID)")
        return 0
    rc = 0
    for m in targets:
        print(f"{m.name}:")
        ok, msg = update_one(m, ns.get("mods", {}).get(m.folder, {}), api_key, install)
        print(("  " + msg) if ok else f"  {msg}")
        if not ok and "gia'" not in msg:
            rc = 1
    return rc


def cmd_nxm(url):
    """Handler dei link nxm://mechwarrior5mercenaries/... .

    Scarica il file, lo installa, registra id/file_id/versione nel db locale e
    attiva la mod: il flusso completo da un clic sul sito.
    """
    import urllib.parse
    import urllib.request
    c = core()
    p = urllib.parse.urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    try:
        mod_id = int(parts[parts.index("mods") + 1])
        file_id = int(parts[parts.index("files") + 1])
    except (ValueError, IndexError):
        print(f"link nxm malformato: {url}", file=sys.stderr)
        return 1
    q = urllib.parse.parse_qs(p.query)
    key = q.get("key", [None])[0]
    expires = q.get("expires", [None])[0]

    install = resolve_install_dir()
    if not install:
        return _no_install()
    cfg, ns = cfg_load()
    api_key = cfg["nexus_api_key"]
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    if expires and expires.isdigit() and int(expires) < time.time():
        print("link scaduto: rigenera il download da Nexus", file=sys.stderr)
        return 1

    path = f"/mods/{mod_id}/files/{file_id}/download_link.json"
    if key:
        path += f"?key={urllib.parse.quote(key)}&expires={expires}"
    try:
        links = nexus_get(path, api_key)
    except c.NexusError as ex:
        print(f"impossibile ottenere il link di download: {ex}", file=sys.stderr)
        return 1
    if not links:
        print("Nexus non ha restituito alcun link", file=sys.stderr)
        return 1

    dl = links[0].get("URI")
    fname = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(dl).path))
    cache = os.path.join(c.CONFIG_DIR, "cache")
    os.makedirs(cache, exist_ok=True)
    dest = os.path.join(cache, fname)
    print(f"scarico {fname} ...")
    try:
        c.download_url(dl, dest)
    except Exception as ex:
        print(f"download fallito: {ex}", file=sys.stderr)
        return 1

    # versione dichiarata da Nexus per questo file, per i check successivi
    version = ""
    try:
        for f in nexus_get(f"/mods/{mod_id}/files.json", api_key).get("files", []):
            if f.get("file_id") == file_id:
                version = (f.get("version") or "").strip()
                break
    except c.NexusError:
        pass

    try:
        got = install_archive(dest, install, enable=True)
    except Exception as ex:
        print(f"installazione fallita: {ex}", file=sys.stderr)
        print(f"l'archivio resta in {dest}")
        return 1
    if not got:
        return 1
    cfg, ns = cfg_load()
    for folder in got:
        link_folder(folder, mod_id, ns, file_id=file_id, version=version)
    cfg_save(cfg)
    print(f"associata a nexusmods.com/{NEXUS_GAME}/mods/{mod_id}"
          + (f" (v{version})" if version else ""))
    print("pronta: le mod attive si caricano al prossimo avvio del gioco")
    return 0


HELP = """pakrat mw5 - MechWarrior 5: Mercenaries

  list                  elenco mod, stato e load order
  add ARCHIVIO [...]    installa da zip/7z/rar
                        se l'archivio contiene piu' mod chiede quali attivare
                        --no-enable non attiva nulla, --enable-all attiva tutto
  enable MOD [...]      attiva
  disable MOD [...]     disattiva
  order MOD N           imposta il load order (piu' alto = caricata dopo)
  order --seq A B C     riassegna l'ordine nella sequenza data
  order --apply         ri-applica l'ordine salvato nel config
  remove MOD [...]      sposta in archivio (--purge per cancellare davvero)
  restore [N|NOME]      elenca l'archivio, o ripristina una mod rimossa
  link MOD ID           associa una mod installata alla sua pagina Nexus
  check                 cerca aggiornamenti su Nexus
  update [MOD]          scarica e installa gli aggiornamenti
  prune                 togli da modlist.json le voci senza cartella
  setup [PERCORSO]      mostra o imposta l'installazione di MW5

MOD si indica per indice (da 'list'), nome cartella o nome visualizzato.
"""


def main(args):
    # senza questo, con l'output rediretto stdout resta nel buffer e i messaggi
    # d'errore su stderr compaiono fuori ordine
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if not args or args[0] in ("help", "-h", "--help"):
        print(HELP)
        return 0
    cmd, rest = args[0], args[1:]
    table = {
        "list": lambda: cmd_list(rest),
        "ls": lambda: cmd_list(rest),
        "add": lambda: cmd_add(rest),
        "enable": lambda: cmd_enable(rest, True),
        "disable": lambda: cmd_enable(rest, False),
        "order": lambda: cmd_order(rest),
        "remove": lambda: cmd_remove(rest),
        "rm": lambda: cmd_remove(rest),
        "restore": lambda: cmd_restore(rest),
        "link": lambda: cmd_link(rest),
        "check": lambda: cmd_check(rest),
        "update": lambda: cmd_update(rest),
        "prune": lambda: cmd_prune(rest),
        "setup": lambda: cmd_setup(rest),
        "nxm": lambda: cmd_nxm(rest[0]) if rest else 1,
    }
    fn = table.get(cmd)
    if fn is None:
        print(HELP)
        return 1
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
