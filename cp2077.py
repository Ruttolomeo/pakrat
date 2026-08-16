#!/usr/bin/env python3
"""
cp2077.py - backend Cyberpunk 2077 per pakrat.

Cyberpunk rompe l'assunto su cui poggiano gli altri due backend, cioe' che una
mod sia UNA unita' nel filesystem (un .pak per BG3, una cartella per MW5). Qui
una mod tipica e' un archivio che si spalma sulla RADICE del gioco toccando piu'
posti insieme:

    archive/pc/mod/*.archive (+ .xl)  contenuto; ordine ASCII-alfabetico sul nome,
                                      e VINCE IL PRIMO caricato (non l'ultimo)
    mods/<nome>/info.json             REDmod; serve il deploy e l'avvio -modded
    r6/scripts/*.reds                 redscript
    r6/tweaks/*.tweak                 TweakXL
    red4ext/plugins/<nome>/           RED4ext
    bin/x64/plugins/cyber_engine_tweaks/mods/<nome>/   CET

Quindi qui l'identita' di una mod non puo' essere un percorso: teniamo un
MANIFEST, cioe' la lista dei file che ogni archivio ha installato, e su quella
lista poggiano disattiva, rimuovi e ripristina. Di fatto un piccolo gestore di
pacchetti con un solo repository: l'archivio che ti sei scaricato.

Tre conseguenze, tutte volute:

  - prima di sovrascrivere un file che non appartiene a nessuna mod nota, lo
    mettiamo da parte (shadow). Cosi' 'remove' sa rimettere l'originale invece
    di lasciare un buco.
  - disattivare NON cancella: sposta i file in una cartella accanto
    all'installazione, sullo stesso filesystem, quindi e' un rename anche per
    una mod da un gigabyte.
  - REDmod non lo deployamo noi: 'redMod.exe deploy' e' un eseguibile Windows e
    pakrat non chiama Wine. Prepariamo mods/ e ricordiamo il flag -modded, che
    fa fare il deploy a REDprelauncher all'avvio.

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

NEXUS_GAME = "cyberpunk2077"
STEAM_APPID = "1091500"
_EXE_HINTS = ("cyberpunk2077.exe", "redprelauncher.exe")

# Cartelle di primo livello che, dentro un archivio, dicono "questa e' la radice
# del gioco": il resto dei percorsi va copiato cosi' com'e'.
TOP_DIRS = ("archive", "mods", "r6", "red4ext", "bin", "engine", "plugins")

# Percorsi dove una mod non deve mettere le mani: contenuto base e toolchain
# REDmod del gioco. NON si protegge 'engine/' in blocco: redscript si installa
# proprio li' dentro (engine/tools/scc.exe, engine/config/base/scripts.ini), e
# vietarlo lo installerebbe con zero file. I file di gioco che finiscono coperti
# sono comunque salvati dal meccanismo di shadow.
PROTECTED = ("archive/pc/content", "archive/pc/ep1", "tools/redmod/")
PROTECTED_FILES = ("bin/x64/cyberpunk2077.exe", "bin/x64/oo2ext_7_win64.dll",
                   "redprelauncher.exe")

ARCHIVE_MOD_DIR = os.path.join("archive", "pc", "mod")

# I "core mod": non aggiungono contenuto, lo rendono possibile. Quasi ogni mod
# moderna ne pretende almeno uno.
#
# 'detect' sono percorsi relativi alla radice: se ne esiste uno, il framework
# c'e'. Non ci sono ID Nexus cablati di proposito: cambiano, e una pagina
# sbagliata e' peggio di una ricerca.
FRAMEWORKS = {
    "RED4ext": {
        "detect": ["red4ext/plugins", "bin/x64/winmm.dll"],
        "why": "carica i plugin .dll nel motore; base di tutto cio' che e' nativo",
    },
    "redscript": {
        "detect": ["engine/tools/scc.exe", "r6/cache/modded"],
        "why": "compila gli script .reds",
    },
    "Cyber Engine Tweaks": {
        "detect": ["bin/x64/plugins/cyber_engine_tweaks"],
        "why": "strato Lua e console a runtime",
    },
    "ArchiveXL": {
        "detect": ["red4ext/plugins/ArchiveXL"],
        "why": "carica risorse nuove senza sostituire quelle originali",
    },
    "TweakXL": {
        "detect": ["red4ext/plugins/TweakXL"],
        "why": "modifica il TweakDB da .yaml/.tweak",
    },
    "Codeware": {
        "detect": ["red4ext/plugins/Codeware"],
        "why": "libreria per mod redscript e CET",
    },
}

# Che cosa pretende una mod, dedotto dai file che ha portato. E' piu' affidabile
# della lista dichiarata dall'autore: guarda cosa la mod fa davvero.
def requirements_of(files):
    """Framework richiesti, dedotti dai percorsi nel manifest."""
    need = set()
    for rel in files:
        low = rel.replace(os.sep, "/").lower()
        if low.endswith(".xl"):
            need.add("ArchiveXL")
        elif low.startswith("r6/tweaks/"):
            need.add("TweakXL")
        elif low.startswith("r6/scripts/") and low.endswith(".reds"):
            need.add("redscript")
        elif low.startswith("bin/x64/plugins/cyber_engine_tweaks/mods/"):
            need.add("Cyber Engine Tweaks")
        elif low.startswith("red4ext/plugins/"):
            need.add("RED4ext")
    # i plugin RED4ext sono inutili senza il loader
    if need & {"ArchiveXL", "TweakXL", "Codeware"}:
        need.add("RED4ext")
    return sorted(need)


def framework_present(name, install):
    """True se il framework risulta installato sul disco."""
    for rel in FRAMEWORKS.get(name, {}).get("detect", []):
        if os.path.exists(os.path.join(install, rel.replace("/", os.sep))):
            return True
    return False


def missing_frameworks(files, install):
    """Framework che la mod pretende e che non si vedono installati."""
    return [f for f in requirements_of(files) if not framework_present(f, install)]


# Da dove si prendono i core mod. NON da Nexus: sono tutti progetti open source
# con release su GitHub, e da li' il download e' diretto, versionato e senza
# bisogno di API key ne' di un account premium. L'ordine e' quello di
# installazione: prima i due che non dipendono da nessuno.
CORE_SOURCES = [
    ("RED4ext",             "WopsS/RED4ext",            r"^red4ext-[\d.]+\.zip$"),
    ("redscript",           "jac3km4/redscript",        r"^redscript-v[\d.]+-windows\.zip$"),
    ("Cyber Engine Tweaks", "maximegmd/CyberEngineTweaks", r"^cet_[\d.]+\.zip$"),
    ("ArchiveXL",           "psiberx/cp2077-archive-xl", r"^ArchiveXL-[\d.]+\.zip$"),
    ("TweakXL",             "psiberx/cp2077-tweak-xl",   r"^TweakXL-[\d.]+\.zip$"),
    ("Codeware",            "psiberx/cp2077-codeware",   r"^Codeware-[\d.]+\.zip$"),
]

# Come li chiama la gente. Senza questi 'bootstrap cet' non troverebbe niente:
# "cet" non e' una sottostringa di "Cyber Engine Tweaks".
CORE_ALIASES = {
    "cet": "Cyber Engine Tweaks",
    "cyberenginetweaks": "Cyber Engine Tweaks",
    "cyber": "Cyber Engine Tweaks",
    "red4ext": "RED4ext",
    "r4e": "RED4ext",
    "redscript": "redscript",
    "reds": "redscript",
    "archivexl": "ArchiveXL",
    "axl": "ArchiveXL",
    "tweakxl": "TweakXL",
    "txl": "TweakXL",
    "codeware": "Codeware",
}


def core_match(token):
    """Il core mod indicato da un token, o None. Alias, nome esatto o pezzo di nome."""
    t = token.strip().lower()
    if t in CORE_ALIASES:
        return CORE_ALIASES[t]
    names = [n for n, _r, _a in CORE_SOURCES]
    for n in names:
        if n.lower() == t:
            return n
    hits = [n for n in names if t and t in n.lower()]
    return hits[0] if len(hits) == 1 else None


def parse_core_args(args):
    """Separa i token in {nome: versione_richiesta}.

    'NOME' vuol dire "l'ultima", 'NOME==1.36.0' una precisa, 'NOME==latest'
    toglie il pin. Ritorna anche i token che non corrispondono a niente, perche'
    ignorarli in silenzio farebbe credere di aver fatto qualcosa.
    """
    want, bad = {}, []
    for a in args:
        if a.startswith("-"):
            continue
        tok, _, ver = a.partition("==")
        name = core_match(tok)
        if name is None:
            bad.append(a)
            continue
        want[name] = ver.strip() or None
    return want, bad


def github_release(repo, asset_re, tag=None):
    """(tag, nome_asset, url) di una release, o None se non si trova.

    Senza tag prende l'ultima. Niente token: l'API pubblica basta e il limite di
    60 richieste/ora e' abbondante per sei repo.
    """
    import urllib.error
    import urllib.request
    if tag:
        # su questi repo i tag sono tutti 'vX.Y.Z', ma accettiamo anche il numero
        # nudo: chi lo scrive a mano lo copia dalla versione, non dal tag
        cands = [tag] if tag.startswith("v") else [f"v{tag}", tag]
        data = None
        for t in cands:
            url = f"https://api.github.com/repos/{repo}/releases/tags/{t}"
            req = urllib.request.Request(
                url, headers={"User-Agent": "pakrat",
                              "Accept": "application/vnd.github+json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as ex:
                if ex.code != 404:
                    raise
        if data is None:
            raise RuntimeError(f"versione {tag} non trovata fra le release di {repo}")
    else:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"User-Agent": "pakrat",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    rx = re.compile(asset_re)
    for a in data.get("assets") or []:
        if rx.match(a.get("name") or ""):
            return data.get("tag_name") or "", a["name"], a["browser_download_url"]
    return None


def github_latest(repo, asset_re):
    return github_release(repo, asset_re)


STORE_DIRNAME = "pakrat-cp2077"          # accanto all'installazione
ORDER_RE = re.compile(r'^(\d{3})_')


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
    """Config condiviso col resto del tool, in una chiave tutta nostra."""
    cfg = core().load_config()
    ns = cfg.setdefault("cp2077", {})
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
    """Cartelle di installazione note a Heroic (Epic/GOG/Amazon).

    Stessa lettura dei JSON che fa il backend MW5: install_path e' esplicito,
    meglio che tirare a indovinare coi glob.
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
        roots.append(os.path.join(lib, "steamapps/common/Cyberpunk 2077"))
    return roots


def _looks_like_install(p):
    """Radice valida = c'e' l'eseguibile, o almeno archive/pc con il contenuto base."""
    if not p or not os.path.isdir(p):
        return False
    return (os.path.isfile(os.path.join(p, "bin", "x64", "Cyberpunk2077.exe"))
            or os.path.isdir(os.path.join(p, "archive", "pc", "content")))


def detect_install_dirs():
    cands = []
    env = core().env_var("CP2077_DIR")
    if env:
        cands.append(os.path.expanduser(env))
    cands += _heroic_roots()
    cands += _steam_roots()
    for r in list(cands):
        if os.path.isdir(r):
            cands += sorted(glob.glob(os.path.join(r, "*Cyberpunk*")))
    cands += sorted(glob.glob(os.path.expanduser(
        "~/.steam/steam/steamapps/compatdata/%s/pfx/drive_c/**/Cyberpunk 2077"
        % STEAM_APPID)))

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


def store_dir(install=None, sub="", create=False):
    """Deposito accanto all'installazione: mod disattivate e mod rimosse.

    Sta sullo stesso filesystem del gioco (rename istantaneo anche per una mod
    da un giga) e fuori dalla radice, cosi' il gioco non ci guarda dentro.
    """
    install = install or resolve_install_dir()
    if not install:
        return ""
    base = os.path.join(os.path.dirname(install), STORE_DIRNAME)
    if not os.path.isdir(base) and os.path.isdir(os.path.join(install, STORE_DIRNAME)):
        base = os.path.join(install, STORE_DIRNAME)   # fallback gia' esistente
    p = os.path.join(base, sub) if sub else base
    if create:
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            base = os.path.join(install, STORE_DIRNAME)
            p = os.path.join(base, sub) if sub else base
            os.makedirs(p, exist_ok=True)
    return p


# --------------------------------------------------- guardia gioco aperto ---
def _proc_names(piddir):
    """(comm, basename di argv[0]) di un processo, in minuscolo."""
    argv0 = ""
    try:
        with open(os.path.join(piddir, "cmdline"), "rb") as f:
            argv0 = f.read().split(b"\0", 1)[0].decode("utf-8", "replace")
    except OSError:
        pass
    argv0 = re.split(r'[\\/]', argv0)[-1].lower()
    comm = ""
    try:
        with open(os.path.join(piddir, "comm")) as f:
            comm = f.read().strip().lower()
    except OSError:
        pass
    return comm, argv0


def game_running():
    """True se Cyberpunk (o il suo prelauncher) e' in esecuzione.

    Confronto sul NOME del processo, non sul cmdline: cercare la stringa nella
    riga di comando matcha qualunque shell che nomini l'eseguibile, se stessi
    compresi. 'comm' e' troncato a 15 caratteri dal kernel, da cui h[:15].
    """
    me = os.getpid()
    for d in glob.glob("/proc/[0-9]*"):
        try:
            if int(os.path.basename(d)) == me:
                continue
        except ValueError:
            continue
        comm, argv0 = _proc_names(d)
        for h in _EXE_HINTS:
            if argv0 == h or (comm and comm == h[:15]):
                return True
    return False


def require_game_closed():
    if game_running():
        print("Cyberpunk 2077 e' in esecuzione: toccare i file delle mod mentre "
              "gira\nporta a crash o a un caricamento a meta'. Chiudi il gioco e "
              "riprova.", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------- manifest ---
class Mod:
    """Una mod come la vede pakrat: un nome e la lista dei file che ha portato."""

    def __init__(self, slug, entry):
        self.slug = slug
        self.entry = entry
        self.name = str(entry.get("display_name") or slug)
        self.version = str(entry.get("installed_version") or "")
        self.enabled = bool(entry.get("enabled", True))
        self.files = list(entry.get("files") or [])
        self.kinds = list(entry.get("kinds") or [])
        self.nexus_id = entry.get("nexus_id")

    @property
    def order(self):
        """Prefisso numerico degli .archive, o None se non ne ha."""
        for rel in self.archives():
            m = ORDER_RE.match(os.path.basename(rel))
            if m:
                return int(m.group(1))
        return None

    def archives(self):
        """Solo gli .archive in archive/pc/mod: sono i soli caricati per nome.

        Un .archive dentro mods/<nome>/ appartiene a un REDmod, lo carica il
        deploy e non partecipa all'ordine alfabetico: rinominarlo lo romperebbe.
        """
        pre = ARCHIVE_MOD_DIR.replace(os.sep, "/") + "/"
        return [f for f in self.files
                if f.lower().endswith(".archive") and f.replace(os.sep, "/").startswith(pre)]

    def missing(self, install):
        """File del manifest che non sono dove dovrebbero essere."""
        base = install if self.enabled else store_dir(install, "disattivate/" + self.slug)
        if not base:
            return list(self.files)
        return [f for f in self.files if not os.path.exists(os.path.join(base, f))]


def scan_mods(ns=None):
    """Le mod note, in ordine di caricamento effettivo poi per nome.

    L'ordine di caricamento degli .archive e' alfabetico sul nome file: e' il
    gioco a stabilirlo, noi possiamo solo influenzarlo col prefisso numerico.
    """
    if ns is None:
        _cfg, ns = cfg_load()
    out = []
    for slug, entry in (ns.get("mods") or {}).items():
        if entry.get("removed_at"):
            continue
        out.append(Mod(slug, entry))

    def key(m):
        a = sorted(os.path.basename(x).lower() for x in m.archives())
        return (0, a[0]) if a else (1, m.slug.lower())
    out.sort(key=key)
    return out


def find_mod(ref, mods=None, ns=None):
    """Trova una mod per indice (da 'list'), ID Nexus, slug o nome visualizzato.

    Un numero e' prima di tutto l'indice della lista, che e' quello che si ha
    sotto gli occhi; se non esiste un indice cosi' si prova come ID Nexus, che e'
    sempre molto piu' grande. Per togliere ogni dubbio c'e' la forma 'id:6945'.
    """
    if mods is None:
        mods = scan_mods(ns)
    ref = str(ref).strip()
    if ref.lower().startswith("id:") and ref[3:].strip().isdigit():
        want = int(ref[3:].strip())
        by_id = [m for m in mods if m.nexus_id and int(m.nexus_id) == want]
        return by_id[0] if by_id else None
    if ref.isdigit():
        i = int(ref)
        if 1 <= i <= len(mods):
            return mods[i - 1]
        by_id = [m for m in mods if m.nexus_id and int(m.nexus_id) == i]
        return by_id[0] if by_id else None
    low = ref.lower()
    exact = [m for m in mods if m.slug.lower() == low or m.name.lower() == low]
    if len(exact) == 1:
        return exact[0]
    part = [m for m in mods if low in m.slug.lower() or low in m.name.lower()]
    return part[0] if len(part) == 1 else None


def _norm(rel):
    return str(rel).replace(os.sep, "/").lower()


def _claimants(rel, ns):
    """Le mod (non archiviate) che dichiarano di aver installato un file."""
    rel = _norm(rel)
    return [slug for slug, e in (ns.get("mods") or {}).items()
            if not e.get("removed_at")
            and any(_norm(f) == rel for f in (e.get("files") or []))]


def _losers(rel, ns):
    """Le mod che hanno installato quel file ma se lo sono viste sovrascrivere.

    Il vincitore registra il sorpasso in 'overrides' al momento dell'installazione:
    e' l'unico modo per sapere chi ha scritto per ultimo, visto che due mod possono
    elencare lo stesso percorso in eterno.
    """
    rel = _norm(rel)
    out = set()
    for e in (ns.get("mods") or {}).values():
        if e.get("removed_at"):
            continue
        for r, loser in (e.get("overrides") or {}).items():
            if _norm(r) == rel:
                out.add(loser)
    return out


def owner_of(rel, ns, skip=None):
    """Quale mod possiede un file ORA sul disco.

    Quando due mod installano lo stesso percorso, il file sul disco e' uno solo:
    e' di chi l'ha scritto per ultimo, e solo quella puo' spostarlo o cancellarlo.
    Chi ha perso resta a elencarlo (se il vincitore se ne va, il file torna suo),
    ma non deve piu' toccarlo.
    """
    claim = [s for s in _claimants(rel, ns) if s != skip]
    if not claim:
        return None
    lost = _losers(rel, ns)
    for slug in claim:
        if slug not in lost:
            return slug
    return claim[0]


def overridden_files(slug, ns):
    """I file che 'slug' ha installato ma che ora sono di un'altra mod."""
    out = set()
    for other, e in (ns.get("mods") or {}).items():
        if other == slug or e.get("removed_at"):
            continue
        for rel, loser in (e.get("overrides") or {}).items():
            if loser == slug:
                out.add(rel)
    return out


# ------------------------------------------------------------- estrazione ---
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


def _rel_files(root):
    """Tutti i file sotto root, come percorsi relativi con '/'."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            out.append(os.path.relpath(p, root).replace(os.sep, "/"))
    return sorted(out)


def _find_game_root(tree):
    """La cartella dell'albero estratto che corrisponde alla radice del gioco.

    Molti archivi hanno un livello di incarto ('MiaMod/archive/pc/mod/...'), a
    volte due. Prendiamo la piu' esterna che contenga una delle cartelle note e
    che regga a un controllo di forma, per non scambiare una cartella 'mods' di
    documentazione per la radice.
    """
    best = None
    for dirpath, dirs, _files in os.walk(tree):
        low = {d.lower(): d for d in dirs}
        hit = [low[t] for t in TOP_DIRS if t in low]
        if not hit:
            continue
        ok = False
        for h in hit:
            p = os.path.join(dirpath, h)
            hl = h.lower()
            if hl == "archive" and os.path.isdir(os.path.join(p, "pc")):
                ok = True
            elif hl == "r6" and any(os.path.isdir(os.path.join(p, s))
                                    for s in ("scripts", "tweaks", "config")):
                ok = True
            elif hl == "red4ext" and os.path.isdir(os.path.join(p, "plugins")):
                ok = True
            elif hl == "bin" and os.path.isdir(os.path.join(p, "x64")):
                ok = True
            elif hl == "mods" and any(
                    os.path.isfile(os.path.join(p, d, "info.json"))
                    for d in os.listdir(p)
                    if os.path.isdir(os.path.join(p, d))):
                ok = True
        if ok and (best is None or dirpath.count(os.sep) < best.count(os.sep)):
            best = dirpath
    return best


def _loose_layout(tree, slug):
    """Archivi che NON portano la struttura del gioco: deduciamo dove va cosa.

    Molte mod piccole sono un .archive nudo, o una manciata di .reds, e si
    aspettano che sia tu a sapere dove metterli. Qui lo sappiamo noi.
    """
    plan = []
    for rel in _rel_files(tree):
        base = os.path.basename(rel)
        low = base.lower()
        parts = rel.split("/")
        src = os.path.join(tree, rel.replace("/", os.sep))
        if low.endswith(".archive") or low.endswith(".archive.xl"):
            plan.append((src, f"{ARCHIVE_MOD_DIR}/{base}".replace(os.sep, "/")))
        elif low == "info.json" and len(parts) >= 1:
            # REDmod: la cartella che contiene info.json e' il nome del modulo
            folder = parts[-2] if len(parts) >= 2 else slug
            sub = "/".join(parts[parts.index(folder) + 1:]) if folder in parts else base
            plan.append((src, f"mods/{folder}/{sub}"))
        elif low.endswith(".reds"):
            plan.append((src, f"r6/scripts/{slug}/{base}"))
        elif low.endswith(".tweak") or low.endswith(".tweakdb"):
            plan.append((src, f"r6/tweaks/{slug}/{base}"))
        elif low == "init.lua":
            plan.append((src, f"bin/x64/plugins/cyber_engine_tweaks/mods/{slug}/{base}"))
        else:
            plan.append((None, rel))          # segnalato, non installato
    return plan


def plan_install(tree, slug):
    """Cosa va copiato dove. Ritorna (coppie, ignorati, tipi).

    'coppie' sono (sorgente_assoluta, destinazione_relativa_alla_radice).
    """
    root = _find_game_root(tree)
    pairs, ignored = [], []
    if root:
        for rel in _rel_files(root):
            pairs.append((os.path.join(root, rel.replace("/", os.sep)), rel))
    else:
        # niente struttura riconoscibile: proviamo a dedurla dai file
        loose = _loose_layout(tree, slug)
        redmod_dirs = {d.split("/")[1] for s, d in loose
                       if s and d.startswith("mods/") and len(d.split("/")) > 1}
        for src, dest in loose:
            if src is None:
                # i file sciolti di un REDmod stanno sotto la sua cartella: se
                # abbiamo riconosciuto un info.json, seguono quello
                taken = False
                for rd in redmod_dirs:
                    if f"/{rd}/" in "/" + dest or dest.startswith(rd + "/"):
                        sub = dest.split(rd + "/", 1)[-1]
                        pairs.append((os.path.join(tree, dest.replace("/", os.sep)),
                                      f"mods/{rd}/{sub}"))
                        taken = True
                        break
                if not taken:
                    ignored.append(dest)
            else:
                pairs.append((src, dest))

    # niente percorsi che escono dalla radice, niente cartelle protette
    safe, kinds = [], set()
    for src, dest in pairs:
        norm = os.path.normpath(dest).replace(os.sep, "/")
        if norm.startswith("..") or norm.startswith("/"):
            ignored.append(dest)
            continue
        low = norm.lower()
        if any(low.startswith(p) for p in PROTECTED) or low in PROTECTED_FILES:
            ignored.append(dest)
            continue
        safe.append((src, norm))
        if low.startswith("archive/"):
            kinds.add("archive")
        elif low.startswith("mods/"):
            kinds.add("redmod")
        elif low.startswith("r6/") or low.startswith("red4ext/") or low.startswith("bin/"):
            kinds.add("script")
    return safe, ignored, sorted(kinds)


# ------------------------------------------------------------ installazione ---
def _slug_from_archive(path):
    """Nome mod ricavato dal file scaricato, ripulito dal codice Nexus.

    Nexus consegna 'Nome Mod-4523-1-2-1699999999.zip': la coda e' id, versione e
    timestamp, e non fa parte del nome.
    """
    base = os.path.basename(path)
    base = re.sub(r'\.(zip|7z|rar|tar\.gz|tgz)$', '', base, flags=re.I)
    base = re.sub(r'-\d+(-\d+)*-\d{9,}$', '', base)     # -id-ver-timestamp
    base = re.sub(r'-\d+-\d+(-\d+)*$', '', base)        # -id-ver
    base = re.sub(r'[\\/]+', '_', base).strip(" .-_")
    return base or "mod"


def _shadow_dir(slug, create=False):
    p = os.path.join(core().CONFIG_DIR, "cp2077-shadow", slug)
    if create:
        os.makedirs(p, exist_ok=True)
    return p


def install_plan(pairs, slug, install, ns, log=print):
    """Copia i file pianificati nella radice del gioco.

    Prima di sovrascrivere un file che non appartiene a nessuna mod nota lo
    mettiamo da parte: e' l'unico modo perche' 'remove' sappia rimettere le cose
    com'erano invece di lasciare un buco al posto di un file di gioco.
    """
    written, shadowed, overrides = [], {}, {}
    mine = ns.get("mods", {}).get(slug, {}).get("files") or []
    for src, rel in pairs:
        dest = os.path.join(install, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest) and rel not in mine:
            # mettiamo da parte quello che stiamo per coprire, che sia di un'altra
            # mod o del gioco: e' cio' che 'remove' rimettera' al suo posto.
            sd = os.path.join(_shadow_dir(slug, create=True),
                              rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(sd), exist_ok=True)
            if not os.path.exists(sd):
                shutil.copy2(dest, sd)
            other = owner_of(rel, ns, skip=slug)
            if other:
                overrides[rel] = other
            else:
                shadowed[rel] = True
        shutil.copy2(src, dest)
        written.append(rel)
    for rel, other in sorted(overrides.items()):
        log(f"  ! {rel}\n      sovrascrive un file di '{other}'")
    return written, shadowed, overrides


def _prune_empty(install, rels):
    """Toglie le cartelle rimaste vuote, senza mai risalire oltre la radice."""
    dirs = set()
    for rel in rels:
        d = os.path.dirname(rel.replace("/", os.sep))
        while d:
            dirs.add(d)
            d = os.path.dirname(d)
    for d in sorted(dirs, key=lambda x: -x.count(os.sep)):
        p = os.path.join(install, d)
        if os.path.isdir(p) and not os.listdir(p):
            try:
                os.rmdir(p)
            except OSError:
                pass


def _move_files(rels, src_base, dst_base):
    """Sposta un insieme di file fra due radici, preservando i percorsi."""
    moved = []
    for rel in rels:
        s = os.path.join(src_base, rel.replace("/", os.sep))
        if not os.path.exists(s):
            continue
        d = os.path.join(dst_base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        try:
            os.replace(s, d)
        except OSError:                      # filesystem diversi
            shutil.copy2(s, d)
            os.remove(s)
        moved.append(rel)
    return moved


def install_archive(archive, install=None, enable=True, log=print, slug=None):
    """Estrae un archivio e ne installa il contenuto. Ritorna lo slug, o None."""
    archive = os.path.expanduser(archive)
    if not os.path.isfile(archive):
        raise RuntimeError(f"file non trovato: {archive}")
    install = install or resolve_install_dir()
    if not install:
        raise RuntimeError("installazione di Cyberpunk 2077 non trovata")
    if not require_game_closed():
        return None
    c = core()
    slug = slug or _slug_from_archive(archive)
    tmp = os.path.join(c.CONFIG_DIR, "cache", "cp2077-extract")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _extract_archive(archive, tmp)
        pairs, ignored, kinds = plan_install(tmp, slug)
        if not pairs:
            raise RuntimeError(
                "non riconosco la struttura di questo archivio: nessun .archive, "
                "info.json,\n  .reds o cartella nota. Va installata a mano "
                "(leggi la descrizione su Nexus)")
        cfg, ns = cfg_load()
        entry = ns.setdefault("mods", {}).setdefault(slug, {})
        was = bool(entry.get("files"))
        if was and entry.get("enabled") is False:
            # aggiornare una mod disattivata la riporterebbe in gioco a sorpresa
            raise RuntimeError(f"'{slug}' e' disattivata: riattivala prima di "
                               "aggiornarla, o rimuovila")
        old_files = list(entry.get("files") or [])
        written, shadowed, overrides = install_plan(pairs, slug, install, ns, log=log)

        # i file che c'erano prima e ora non ci sono piu' vanno tolti, altrimenti
        # resta in giro roba della versione vecchia che il gioco carica lo stesso
        # ma non quelli che nel frattempo sono passati a un'altra mod
        lost = overridden_files(slug, ns)
        stale = [f for f in old_files if f not in written and f not in lost]
        for rel in stale:
            p = os.path.join(install, rel.replace("/", os.sep))
            if os.path.isfile(p):
                os.remove(p)
        if stale:
            _prune_empty(install, stale)
            log(f"  tolti {len(stale)} file della versione precedente")

        entry["display_name"] = entry.get("display_name") or slug
        entry["files"] = written
        entry["kinds"] = kinds
        entry["enabled"] = True
        entry["installed_at"] = int(time.time())
        entry["source_archive"] = os.path.basename(archive)
        if shadowed:
            entry["shadowed"] = sorted(shadowed)
        if overrides:
            entry["overrides"] = overrides
        else:
            entry.pop("overrides", None)
        entry.pop("removed_at", None)
        entry.pop("archived_to", None)
        cfg_save(cfg)

        log(f"  {'aggiornata' if was else 'installata'} {slug}: "
            f"{len(written)} file ({', '.join(kinds) or 'sconosciuto'})")
        if ignored:
            log(f"  {len(ignored)} file non installati (fuori dalla struttura nota "
                "o in cartelle protette):")
            for rel in ignored[:5]:
                log(f"      {rel}")
            if len(ignored) > 5:
                log(f"      ... e altri {len(ignored) - 5}")
        if not enable:
            set_enabled([slug], False, install, log=log)
        if "redmod" in kinds:
            log("  ! REDmod: serve il flag -modded negli argomenti di avvio, "
                "il deploy\n    lo fa REDprelauncher (vedi: pakrat cp2077 deploy)")
        # meglio dirlo ora che lasciartelo scoprire da un gioco che non carica
        for f in missing_frameworks(written, install):
            log(f"  ! manca un prerequisito: {f} — {FRAMEWORKS[f]['why']}")
        return slug
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- attiva/disattiva ---
def set_enabled(slugs, enabled, install=None, log=print):
    """Attiva o disattiva spostando i file fra il gioco e il deposito.

    Non si rinomina in .disabled: funzionerebbe per gli .archive ma non per
    r6/scripts o i plugin, che vengono caricati per estensione o per cartella.
    Spostare e' l'unica regola che vale per tutti e cinque i tipi.
    """
    install = install or resolve_install_dir()
    if not install or not require_game_closed():
        return []
    cfg, ns = cfg_load()
    done = []
    for slug in slugs:
        e = (ns.get("mods") or {}).get(slug)
        if e is None or e.get("removed_at"):
            continue
        if bool(e.get("enabled", True)) == enabled:
            continue
        files = list(e.get("files") or [])
        lost = overridden_files(slug, ns)
        if lost:
            # se li spostassimo li toglieremmo dal gioco a nome di un'altra mod
            files = [f for f in files if f not in lost]
            log(f"  {slug}: {len(lost)} file lasciati stare (ora di un'altra mod)")
        shadow = store_dir(install, "disattivate/" + slug, create=True)
        if enabled:
            moved = _move_files(files, shadow, install)
            shutil.rmtree(shadow, ignore_errors=True)
        else:
            moved = _move_files(files, install, shadow)
            _prune_empty(install, files)
        if not moved and files:
            log(f"  {slug}: nessun file trovato da spostare")
            continue
        e["enabled"] = enabled
        done.append(slug)
    if done:
        cfg_save(cfg)
    return done


# ------------------------------------------------------------- load order ---
def set_order(slug, n, install=None, log=print):
    """Rinomina gli .archive con un prefisso NNN_ per forzare l'ordine.

    Il gioco carica gli .archive di archive/pc/mod in ordine ASCII-alfabetico sul
    nome file, e in Cyberpunk **vince chi carica per primo**: il conflitto si
    risolve per singolo file, e il primo mod che lo modifica se lo tiene. Quindi
    numero PIU' BASSO = caricata prima = vince. E' il contrario di Skyrim e anche
    di MechWarrior 5, dove l'ultimo sovrascrive: da qui l'errore facile.

    Esiste anche archive/pc/mod/modlist.txt, che imporrebbe un ordine esplicito,
    ma non lo usiamo: gli archivi non elencati la' dentro rischiano di non essere
    caricati affatto, e la documentazione ufficiale consiglia di cancellarlo. Il
    prefisso sul nome e' reversibile e non puo' far sparire una mod.

    Il compagno .archive.xl viene rinominato insieme, cosi' la coppia non si
    separa.
    """
    install = install or resolve_install_dir()
    if not install or not require_game_closed():
        return None
    cfg, ns = cfg_load()
    e = (ns.get("mods") or {}).get(slug)
    if e is None:
        raise RuntimeError(f"mod sconosciuta: {slug}")
    if not e.get("enabled", True):
        raise RuntimeError(f"'{slug}' e' disattivata: riattivala prima di riordinarla")
    prefix = "" if n is None else f"{max(0, min(999, int(n))):03d}_"
    files, changed = list(e.get("files") or []), []
    pre = ARCHIVE_MOD_DIR.replace(os.sep, "/") + "/"
    for i, rel in enumerate(files):
        low = rel.lower()
        if not rel.replace(os.sep, "/").startswith(pre):
            continue                      # i REDmod non si ordinano per nome
        if not (low.endswith(".archive") or low.endswith(".archive.xl")):
            continue
        d, base = os.path.split(rel)
        new_base = prefix + ORDER_RE.sub("", base)
        if new_base == base:
            continue
        src = os.path.join(install, rel.replace("/", os.sep))
        dst = os.path.join(install, d.replace("/", os.sep), new_base)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            raise RuntimeError(f"esiste gia': {os.path.join(d, new_base)}")
        os.replace(src, dst)
        files[i] = f"{d}/{new_base}" if d else new_base
        changed.append((base, new_base))
    e["files"] = files
    cfg_save(cfg)
    for old, new in changed:
        log(f"  {old} -> {new}")
    return changed


# --------------------------------------------------------- rimozione mod ---
def remove_mod(slug, install=None, purge=False, log=print):
    """Sposta i file della mod in archivio (o li cancella con purge=True).

    I file di gioco che la mod aveva sovrascritto tornano al loro posto: e' il
    motivo per cui li avevamo messi da parte all'installazione.
    """
    install = install or resolve_install_dir()
    if not install or not require_game_closed():
        return None
    cfg, ns = cfg_load()
    e = (ns.get("mods") or {}).get(slug)
    if e is None:
        raise RuntimeError(f"mod sconosciuta: {slug}")
    files = list(e.get("files") or [])
    lost = overridden_files(slug, ns)
    if lost:
        # sul disco quei file sono di chi ha vinto il conflitto: toccarli qui
        # vorrebbe dire disinstallare pezzi di un'altra mod
        files = [f for f in files if f not in lost]
        log(f"  {slug}: {len(lost)} file lasciati stare (ora di un'altra mod)")
    base = install if e.get("enabled", True) else store_dir(install, "disattivate/" + slug)
    if purge:
        for rel in files:
            p = os.path.join(base, rel.replace("/", os.sep))
            if os.path.isfile(p):
                os.remove(p)
        dest = ""
        log(f"  {slug}: {len(files)} file cancellati")
    else:
        dest = store_dir(install, f"rimosse/{slug}-{datetime.now():%Y%m%d-%H%M%S}",
                         create=True)
        moved = _move_files(files, base, dest)
        log(f"  {slug}: {len(moved)} file spostati in {dest}")
    _prune_empty(base, files)
    shutil.rmtree(store_dir(install, "disattivate/" + slug), ignore_errors=True)

    # rimettiamo a posto quello che la mod aveva coperto
    shadow = _shadow_dir(slug)
    restored = 0
    if os.path.isdir(shadow):
        for rel in _rel_files(shadow):
            d = os.path.join(install, rel.replace("/", os.sep))
            if not os.path.exists(d):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(os.path.join(shadow, rel.replace("/", os.sep)), d)
                restored += 1
        if purge:
            shutil.rmtree(shadow, ignore_errors=True)
    if restored:
        log(f"  {restored} file di gioco ripristinati")

    if purge:
        ns["mods"].pop(slug, None)
    else:
        e["removed_at"] = int(time.time())
        e["archived_to"] = dest
    cfg_save(cfg)
    return dest


def list_archived(install=None):
    """Mod in archivio: [(slug, percorso, timestamp)]."""
    adir = store_dir(install, "rimosse")
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


def restore_mod(path, install=None, log=print):
    """Rimette in gioco una mod archiviata."""
    install = install or resolve_install_dir()
    if not install or not require_game_closed():
        return None
    if not os.path.isdir(path):
        raise RuntimeError(f"non trovata in archivio: {path}")
    m = re.match(r'^(.*)-\d{8}-\d{6}$', os.path.basename(path))
    slug = m.group(1) if m else os.path.basename(path)
    cfg, ns = cfg_load()
    e = ns.setdefault("mods", {}).setdefault(slug, {})
    rels = _rel_files(path)
    conflict = [r for r in rels if owner_of(r, ns, skip=slug)]
    if conflict:
        raise RuntimeError(
            f"{len(conflict)} file appartengono ora ad altre mod "
            f"(es. {conflict[0]}): rimuovile prima di ripristinare")
    moved = _move_files(rels, path, install)
    shutil.rmtree(path, ignore_errors=True)
    # i file persi in un conflitto non erano nell'archivio (li aveva gia' un'altra
    # mod), ma restano dichiarati: se quella se ne va, tornano suoi.
    kept = [r for r in (e.get("files") or [])
            if r not in moved and owner_of(r, ns, skip=slug)]
    e["files"] = moved + kept
    e["enabled"] = True
    e.pop("removed_at", None)
    e.pop("archived_to", None)
    cfg_save(cfg)
    log(f"  {slug}: {len(moved)} file ripristinati")
    return slug


# ----------------------------------------------------------------- nexus ---
def nexus_get(path, api_key):
    return core().nexus_get(path, api_key, game=NEXUS_GAME)


def mod_page_url(mod_id, file_id=None):
    u = f"https://www.nexusmods.com/{NEXUS_GAME}/mods/{mod_id}?tab=files&nmm=1"
    if file_id:
        u += f"&file_id={file_id}"
    return u


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

    Stessa logica del backend MW5, e per lo stesso motivo: il confronto e' sul
    file_id dentro la stessa variante, non sulla stringa di versione. Su
    Cyberpunk la cosa e' anche piu' sentita, perche' una mod pubblica spesso
    varianti parallele (per corpo, per versione del gioco, con o senza
    dipendenze) che non sono l'una l'aggiornamento dell'altra.
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
    cat = str(inst.get("category_name") or "").upper()
    others = [f for f in us if f.get("file_id") != fid
              and str(f.get("category_name") or "").upper() == cat
              and _vkey(f.get("version")) == _vkey(iv)]
    if others:
        return "variant", None, iv, \
            "altre varianti alla stessa versione: " + ", ".join(
                str(f.get("name")) for f in others[:3])
    return "ok", None, iv, ""


def link_slug(slug, mod_id, ns, file_id=None, version=""):
    entry = ns.setdefault("mods", {}).setdefault(slug, {})
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


# --------------------------------------------------------------- comandi ---
def _no_install():
    print("Installazione di Cyberpunk 2077 non trovata.\n\n"
          "Ho cercato nelle librerie Heroic (Epic/GOG/Amazon) e Steam.\n"
          "Se e' altrove, indicala con:\n\n"
          "  - variabile d'ambiente PAKRAT_CP2077_DIR\n"
          "  - pakrat cp2077 setup PERCORSO\n\n"
          "Deve puntare alla cartella che contiene bin/x64/Cyberpunk2077.exe",
          file=sys.stderr)
    return 1


def cmd_list(_args=None):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    _cfg, ns = cfg_load()
    mods = scan_mods(ns)
    print(f"installazione: {install}")
    print(f"mod: {len(mods)}   attive: {sum(1 for m in mods if m.enabled)}\n")
    if not mods:
        print("nessuna mod registrata.\n"
              "installane una con: pakrat cp2077 add ARCHIVIO.zip")
        return 0
    print(f"{'#':>3} {'on':^3} {'ord':>4}  {'mod':<32} {'ver':<9} {'file':>5}  tipo")
    for i, m in enumerate(mods, 1):
        o = m.order
        miss = m.missing(install)
        warn = f"  ! {len(miss)} file mancanti" if miss else ""
        print(f"{i:>3} {'X' if m.enabled else ' ':^3} {(str(o) if o is not None else '-'):>4}  "
              f"{m.name[:32]:<32} {(m.version or '?')[:9]:<9} {len(m.files):>5}  "
              f"{','.join(m.kinds) or '?'}{warn}")
    print("\ngli .archive si caricano in ordine ASCII-alfabetico sul nome file, e")
    print("VINCE IL PRIMO caricato: 'ord' piu' basso = vince sui file in conflitto.")
    print("(i REDmod si caricano tutti dopo gli .archive)")
    if any("redmod" in m.kinds and m.enabled for m in mods):
        print("\n! ci sono REDmod attive: servono il flag -modded e il deploy "
              "(pakrat cp2077 deploy)")
    return 0


def cmd_setup(args):
    cfg, ns = cfg_load()
    if not args:
        found = detect_install_dirs()
        if not found:
            return _no_install()
        print("installazioni trovate:")
        for p in found:
            print("  " + p)
        print("\nusa: pakrat cp2077 setup PERCORSO")
        return 0
    p = os.path.abspath(os.path.expanduser(args[0]))
    if not _looks_like_install(p):
        print(f"non sembra un'installazione di Cyberpunk 2077: {p}\n"
              "manca bin/x64/Cyberpunk2077.exe e archive/pc/content", file=sys.stderr)
        return 1
    ns["install_dir"] = p
    cfg_save(cfg)
    print(f"installazione impostata: {p}")
    return 0


def cmd_add(args):
    if not args:
        print("uso: pakrat cp2077 add ARCHIVIO.zip [...] [--no-enable] [--name NOME]",
              file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    enable = "--no-enable" not in args
    name = None
    if "--name" in args:
        i = args.index("--name")
        if i + 1 < len(args):
            name = args[i + 1]
    skip = {"--name", name} if name else set()
    files = [a for a in args if not a.startswith("--") and a not in skip]
    rc, done = 0, []
    for f in files:
        print(f"{os.path.basename(f)}:")
        try:
            got = install_archive(f, install, enable=enable,
                                  slug=name if len(files) == 1 else None)
        except Exception as ex:
            print(f"  errore: {ex}", file=sys.stderr)
            rc = 1
            continue
        if not got:
            rc = 1
        else:
            done.append(got)
    if rc == 0:
        print("\nfatto. Controlla con: pakrat cp2077 list")
    # e' il momento giusto per chiederlo: la mod e' appena entrata e senza i suoi
    # prerequisiti non fara' niente al primo avvio
    if done:
        _cfg, ns = cfg_load()
        need = sorted({fw for s in done
                       for fw in missing_frameworks(
                           (ns.get("mods") or {}).get(s, {}).get("files") or [],
                           install)})
        offer_bootstrap(need, install)
    return rc


def cmd_enable(args, enabled=True):
    if not args:
        verb = "enable" if enabled else "disable"
        print(f"uso: pakrat cp2077 {verb} MOD [MOD...]   (indice, slug o nome)",
              file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    mods = scan_mods()
    targets, rc = [], 0
    for a in args:
        m = find_mod(a, mods)
        if m is None:
            print(f"mod non trovata (o ambigua): {a}", file=sys.stderr)
            rc = 1
            continue
        targets.append(m.slug)
    if not targets:
        return 1
    done = set_enabled(targets, enabled, install)
    if not done:
        print("nessun cambiamento (gia' nello stato richiesto?)")
        return rc
    print(("attivate: " if enabled else "disattivate: ") + ", ".join(done))
    return rc


def cmd_order(args):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    if len(args) < 2 or not (args[1].isdigit() or args[1] == "-"):
        print("uso:\n"
              "  pakrat cp2077 order MOD N   prefisso NNN_ sugli .archive\n"
              "  pakrat cp2077 order MOD -   toglie il prefisso\n"
              "\nnumero piu' BASSO = caricata prima = VINCE sui file in conflitto.\n"
              "In Cyberpunk il conflitto si risolve per singolo file e se lo tiene\n"
              "il primo mod che lo modifica: e' il contrario di MechWarrior 5.",
              file=sys.stderr)
        return 1
    m = find_mod(args[0])
    if m is None:
        print(f"mod non trovata (o ambigua): {args[0]}", file=sys.stderr)
        return 1
    if not m.archives():
        print(f"'{m.slug}' non ha .archive: non ha un ordine da impostare",
              file=sys.stderr)
        return 1
    try:
        changed = set_order(m.slug, None if args[1] == "-" else int(args[1]), install)
    except Exception as ex:
        print(f"errore: {ex}", file=sys.stderr)
        return 1
    if changed is None:
        return 1
    if not changed:
        print("gia' cosi'")
    return 0


def cmd_remove(args):
    if not args:
        print("uso: pakrat cp2077 remove MOD [...] [--purge]", file=sys.stderr)
        return 1
    install = resolve_install_dir()
    if not install:
        return _no_install()
    purge = "--purge" in args
    mods = scan_mods()
    targets = []
    for a in [x for x in args if not x.startswith("--")]:
        m = find_mod(a, mods)
        if m is None:
            print(f"mod non trovata (o ambigua): {a}", file=sys.stderr)
            return 1
        targets.append(m)
    if purge:
        n = sum(len(m.files) for m in targets)
        print(f"cancellazione definitiva di {len(targets)} mod ({n} file).")
        if sys.stdin.isatty():
            try:
                if input("confermi? [scrivi 'si']: ").strip().lower() not in ("si", "sì"):
                    print("annullato")
                    return 1
            except (EOFError, KeyboardInterrupt):
                print()
                return 1
    rc = 0
    for m in targets:
        print(f"{m.name}:")
        try:
            if remove_mod(m.slug, install, purge=purge) is None:
                rc = 1
        except Exception as ex:
            print(f"  errore: {ex}", file=sys.stderr)
            rc = 1
    return rc


def cmd_restore(args):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    arch = list_archived(install)
    if not args:
        if not arch:
            print("archivio vuoto")
            return 0
        print(f"{'#':>3}  {'mod':<34} rimossa il")
        for i, (slug, _p, ts) in enumerate(arch, 1):
            when = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}" if ts else "?"
            print(f"{i:>3}  {slug[:34]:<34} {when}")
        print("\nripristina con: pakrat cp2077 restore N")
        return 0
    ref = args[0]
    if ref.isdigit() and 1 <= int(ref) <= len(arch):
        path = arch[int(ref) - 1][1]
    else:
        hit = [p for slug, p, _ in arch if slug.lower() == ref.lower()]
        if not hit:
            print(f"non in archivio: {ref}", file=sys.stderr)
            return 1
        path = hit[-1]
    try:
        return 0 if restore_mod(path, install) else 1
    except Exception as ex:
        print(f"errore: {ex}", file=sys.stderr)
        return 1


def cmd_verify(_args=None):
    """Confronta il manifest con quello che c'e' davvero sul disco."""
    install = resolve_install_dir()
    if not install:
        return _no_install()
    _cfg, ns = cfg_load()
    mods = scan_mods(ns)
    problems = 0
    for m in mods:
        miss = m.missing(install)
        if miss:
            problems += 1
            print(f"{m.name}: {len(miss)} file mancanti")
            for rel in miss[:5]:
                print(f"    {rel}")
            if len(miss) > 5:
                print(f"    ... e altri {len(miss) - 5}")
    # file in archive/pc/mod che non appartengono a nessuna mod nota
    amd = os.path.join(install, ARCHIVE_MOD_DIR)
    if os.path.isdir(amd):
        known = {f.replace(os.sep, "/").lower()
                 for m in mods for f in m.files}
        orphans = []
        for name in sorted(os.listdir(amd)):
            rel = f"{ARCHIVE_MOD_DIR}/{name}".replace(os.sep, "/")
            if rel.lower() not in known:
                orphans.append(name)
        if orphans:
            problems += 1
            print(f"\n{len(orphans)} file in {ARCHIVE_MOD_DIR} non gestiti da pakrat:")
            for n in orphans[:10]:
                print(f"    {n}")
            print("  (installati a mano? pakrat non li tocchera')")
    miss_fw = sorted({f for m in mods for f in missing_frameworks(m.files, install)})
    if miss_fw:
        problems += 1
        print("\nprerequisiti mancanti: " + ", ".join(miss_fw)
              + "\n  installali con: pakrat cp2077 bootstrap"
              + "\n  (dettagli: pakrat cp2077 deps)")
    # il manifest puo' essere perfetto e il gioco caricare zero: qui si guarda il
    # disco, e il disco non sa niente di come Proton carica le DLL
    if override_check(install):
        problems += 1
    if not problems:
        print(f"tutto a posto: {len(mods)} mod, manifest coerente col disco")
    return 0


def offer_bootstrap(missing, install=None):
    """Propone di installare i core mod mancanti, e lo fa se l'utente accetta.

    Chiede solo se c'e' davvero qualcuno a rispondere: da script, da cron o dalla
    GUI stdin non e' un terminale, e li' una domanda bloccherebbe tutto. In quel
    caso stampa il comando e basta.
    """
    if not missing:
        return 0
    names = ", ".join(missing)
    print(f"\nmancano {len(missing)} prerequisiti: {names}")
    print("  senza, le mod che li usano non fanno niente — di solito in silenzio,")
    print("  senza dare errore.")
    cmd = "pakrat cp2077 bootstrap"
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f"  installali con: {cmd}")
        return 0
    print("\nposso scaricarli e installarli io dalle loro release GitHub"
          " (non da Nexus:\nsono progetti open source, non serve un account premium).")
    try:
        ans = input("  procedo? [s/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if ans not in ("s", "si", "sì", "y", "yes"):
        print(f"  ok, non faccio niente. All'occorrenza: {cmd}")
        return 0
    print()
    return cmd_bootstrap([n for n in missing])


def _pin_write(name, tag):
    """Fissa (o libera) la versione di un core mod gia' installato."""
    cfg, ns = cfg_load()
    e = ns.setdefault("mods", {}).setdefault(name, {})
    if tag:
        e["pinned_version"] = tag
    else:
        e.pop("pinned_version", None)
    cfg_save(cfg)


def cmd_bootstrap(args):
    """Scarica e installa i core mod dalle loro release GitHub.

    Non passa da Nexus di proposito: sono progetti open source con release
    pubbliche, quindi il download e' diretto e non serve un account premium
    (l'API di Nexus i link diretti li da' solo a quelli).

    'NOME==1.36.0' fissa una versione e la ricorda: serve quando il gioco si
    aggiorna e l'ultima release di CET o redscript non parte ancora: si torna a
    quella che funziona e i bootstrap successivi non la rimettono avanti finche'
    non lo dici tu con 'NOME==latest'.
    """
    install = resolve_install_dir()
    if not install:
        return _no_install()
    dry = "--dry-run" in args or "-n" in args
    force = "--force" in args
    want, bad = parse_core_args(args)
    if bad:
        print(f"non riconosco: {', '.join(bad)}", file=sys.stderr)
        print("  core mod: " + ", ".join(n for n, _r, _a in CORE_SOURCES),
              file=sys.stderr)
        print("  per fissare una versione: NOME==1.36.0", file=sys.stderr)
        return 1
    c = core()
    if not require_game_closed():
        return 1

    cache = os.path.join(c.CONFIG_DIR, "cache")
    os.makedirs(cache, exist_ok=True)
    rc, did = 0, 0
    for name, repo, asset_re in CORE_SOURCES:
        if want and name not in want:
            continue
        here = framework_present(name, install)
        _cfg, ns = cfg_load()
        entry = (ns.get("mods") or {}).get(name) or {}
        have = entry.get("installed_version") or ""
        pinned = entry.get("pinned_version") or ""
        asked = want.get(name)                  # None = "l'ultima"
        unpin = str(asked or "").lower() == "latest"
        if unpin:
            asked = None
        # un pin messo apposta non deve saltare via al prossimo bootstrap: e' il
        # motivo per cui esiste (versione che funziona col gioco che hai adesso)
        target = asked or (None if unpin else pinned) or None
        try:
            rel = github_release(repo, asset_re, target)
        except Exception as ex:
            print(f"{name}: {ex}" if isinstance(ex, RuntimeError)
                  else f"{name}: impossibile interrogare GitHub ({ex})")
            rc = 1
            continue
        if rel is None:
            print(f"{name}: nessun file corrispondente nella release di {repo}")
            rc = 1
            continue
        tag, fname, url = rel
        same = have and _vkey(have) == _vkey(tag)
        if here and not force and (same or (not target and have
                                            and _vkey(have) >= _vkey(tag))):
            how = f" ({have}{', fissata' if pinned and not unpin else ''})" if have else ""
            print(f"{name}: gia' presente{how}, salto")
            if asked and not pinned:            # chiesta la versione che c'e' gia'
                _pin_write(name, tag)
                print(f"    versione fissata a {tag}")
            elif unpin and pinned:
                _pin_write(name, None)
                print("    pin tolto: ai prossimi bootstrap prendera' l'ultima")
            continue
        if here and have and _vkey(tag) < _vkey(have):
            print(f"{name}: {have} -> {tag} (torno indietro)")
        elif here and have:
            print(f"{name}: {have} -> {tag}")
        else:
            print(f"{name}: {tag}")
        if dry:
            print(f"    scaricherei {fname}\n    da {url}")
            continue
        dest = os.path.join(cache, fname)
        try:
            if not os.path.isfile(dest):
                print(f"    scarico {fname}")
                c.download_url(url, dest)
            got = install_archive(dest, install, enable=True, slug=name,
                                  log=lambda s: print("  " + s.lstrip()))
        except Exception as ex:
            print(f"    errore: {ex}", file=sys.stderr)
            rc = 1
            continue
        if not got:
            rc = 1
            continue
        cfg, ns = cfg_load()
        e = ns.setdefault("mods", {}).setdefault(name, {})
        e["display_name"] = name
        e["installed_version"] = tag
        e["github"] = repo
        e["github_asset"] = asset_re
        if asked:
            e["pinned_version"] = tag
            print(f"    versione fissata a {tag}: i prossimi bootstrap non la "
                  "toccheranno")
        elif unpin:
            e.pop("pinned_version", None)
        cfg_save(cfg)
        did += 1

    if dry:
        print("\n(--dry-run: non ho scaricato ne' installato niente)")
        return rc
    if did:
        print(f"\n{did} core mod installati o aggiornati.")
        print("Controlla con: pakrat cp2077 deps")
        print("\nRicorda: CET e RED4ext girano dentro il gioco via Proton — se il\n"
              "gioco non parte piu', 'pakrat cp2077 disable' su quello che hai\n"
              "appena messo e' il primo passo per capire chi e'.")
        # e' il momento in cui serve saperlo: i loader appena installati non
        # faranno niente finche' Proton non li carica al posto delle sue builtin
        st = override_state(install)
        if st["known"] and st["missing"] and any(
                os.path.isfile(os.path.join(install, rel.replace("/", os.sep)))
                for _n, rel, _d, _l in LOADERS):
            print()
            override_report(st, install)
            offer_override_fix(st, install)
    else:
        print("\nniente da fare: i core mod risultano gia' installati "
              "(--force per reinstallarli)")
    return rc


def cmd_deps(_args=None):
    """Stato dei core mod e prerequisiti dedotti mod per mod."""
    install = resolve_install_dir()
    if not install:
        return _no_install()
    _cfg, ns = cfg_load()
    mods = scan_mods(ns)
    print("core mod (prerequisiti degli altri):\n")
    absent = []
    for name, meta in FRAMEWORKS.items():
        here = framework_present(name, install)
        e = (ns.get("mods") or {}).get(name) or {}
        ver = e.get("installed_version") or ""
        if ver and e.get("pinned_version"):
            ver += " (fissata)"
        print(f"  {'X' if here else ' '}  {name:<22} {ver:<18} {meta['why']}")
        if not here:
            absent.append(name)
    override_check(install)
    print("\nprerequisiti dedotti dai file di ogni mod:\n")
    if not mods:
        print("  nessuna mod registrata")
        if absent:
            print(f"\n{len(absent)} core mod non installati: {', '.join(absent)}")
            print("  pakrat li scarica dalle release GitHub: pakrat cp2077 bootstrap")
        return 0
    problems, need_missing = 0, set()
    for m in mods:
        need = requirements_of(m.files)
        if not need:
            continue
        miss = [f for f in need if not framework_present(f, install)]
        line = ", ".join(f"{f}{' (MANCA)' if f in miss else ''}" for f in need)
        print(f"  {m.name[:30]:<30} {line}")
        problems += len(miss)
        need_missing.update(miss)
    if not problems:
        print("  (tutto quello che serve risulta installato)")
    print("\nnota: si deduce dai file installati, non dalla pagina Nexus — l'API\n"
          "non espone i 'Requirements'. Dipendenze fra mod normali (non core)\n"
          "restano da leggere sulla pagina della mod.")
    if need_missing:
        offer_bootstrap(sorted(need_missing), install)
    elif absent:
        print(f"\ncore mod non installati: {', '.join(absent)} — nessuna mod "
              "installata\nli richiede, ma quasi ogni mod nuova lo fara': "
              "pakrat cp2077 bootstrap")
    return 0


# I due loader nativi non si installano come plugin: si sostituiscono a una DLL
# di sistema che l'eseguibile carica comunque (DLL hijacking). E' il punto in cui
# tutto lo stack si rompe su Linux, perche' Proton puo' non caricarli.
#
# L'ultimo campo e' dove il loader scrive girando: e' l'unica prova che e' stato
# caricato davvero. Il file .dll sul disco non dimostra niente.
LOADERS = [
    ("RED4ext", "bin/x64/winmm.dll", "winmm", "red4ext/logs"),
    ("Cyber Engine Tweaks", "bin/x64/version.dll", "version",
     "bin/x64/plugins/cyber_engine_tweaks"),
]

# --------------------------------------------- override delle DLL (Proton) ---
# Su Windows l'eseguibile carica winmm.dll e version.dll dalla propria cartella,
# ed e' li' che i due loader si infilano. Sotto Proton no: Wine preferisce le
# proprie builtin e la DLL del mod resta un file inerte — senza un errore, senza
# una riga di log. Il gioco parte, gli .archive si vedono, e tutto cio' che passa
# da RED4ext o CET semplicemente non esiste: ArchiveXL, TweakXL e Codeware sono
# sul disco e sono anche caricati... da nessuno.
#
# E' il fallimento peggiore dello stack perche' non somiglia a un fallimento, e
# perche' guardare i file non lo rivela: la cura e' una variabile d'ambiente, che
# non sta nell'installazione ma nella configurazione del launcher. Unico pezzo
# che pakrat non puo' ne' installare ne' verificare guardando il gioco.
OVERRIDE_KEY = "WINEDLLOVERRIDES"

# I due loader nativi di questo gioco, nella forma che vuole il core:
# (nome del mod, DLL di sistema che si prende).
OVERRIDE_LOADERS = [(n, d) for n, _rel, d, _log in LOADERS]


def override_state(install):
    return core().override_state(install, OVERRIDE_LOADERS)


def override_advice(st, _install=None):
    return core().override_advice(st)


def override_report(st, _install=None, indent=""):
    return core().override_report(st, indent)


def offer_override_fix(st, _install=None):
    rc = core().offer_override_fix(st)
    return rc


def override_check(install, indent=""):
    """Controllo breve per i comandi che non sono 'doctor'. True se manca."""
    if not any(os.path.isfile(os.path.join(install, rel.replace("/", os.sep)))
               for _n, rel, _d, _l in LOADERS):
        return False            # senza loader la variabile non serve a niente
    st = override_state(install)
    if not (st["known"] and st["missing"]):
        return False
    print(f"\n{indent}i loader nativi non risultano abilitati sotto Proton:")
    for name, dll in st["missing"]:
        print(f"{indent}  {dll}.dll  ->  {name}")
    print(f"{indent}  senza, i plugin RED4ext (ArchiveXL, TweakXL, Codeware) sono\n"
          f"{indent}  installati ma non li carica nessuno, e in gioco non succede\n"
          f"{indent}  niente. Dettagli e rimedio: pakrat cp2077 doctor")
    return True


# Dove i framework di QUESTO gioco scrivono. La lettura e la resa a video le fa
# il core (scan_logs / report_logs), che pero' non puo' sapere ne' dove guardare
# ne' cosa distingue una corsa dall'altra: quelle due cose gliele diciamo qui.
LOG_DIRS = [
    "red4ext/logs",
    "r6/logs",
    "bin/x64/plugins/cyber_engine_tweaks",
]

# redscript ruota il suo log ALL'INIZIO della corsa nuova: redscript_r<data>.log
# contiene sempre la corsa precedente, per quanto recente sia il suo mtime. E'
# la trappola per cui si legge un errore risolto e lo si insegue di nuovo.
_ROTATED_RE = re.compile(r"redscript_r(?!CURRENT)", re.I)


def _rotated(name):
    return bool(_ROTATED_RE.search(name))


def _age(ts, now=None):
    return core().log_age(ts, now)


def _scan_logs(install):
    return core().scan_logs(install, LOG_DIRS)


def _last_write(install, reldir):
    """mtime del log piu' fresco sotto una cartella, o None."""
    p = os.path.join(install, reldir.replace("/", os.sep))
    best = None
    for root, _d, files in os.walk(p):
        for fn in files:
            if not fn.lower().endswith(".log"):
                continue
            try:
                ts = os.stat(os.path.join(root, fn)).st_mtime
            except OSError:
                continue
            best = ts if best is None else max(best, ts)
    return best


def cmd_doctor(_args=None):
    """Dopo una partita, dice cosa il gioco ha caricato davvero.

    'verify' guarda i file sul disco, che e' un'altra domanda: qui si legge cio'
    che i framework hanno scritto girando dentro il gioco. E' l'unico modo per
    distinguere "la mod non c'e'" da "la mod c'e' ma non viene caricata", che
    sotto Proton non e' un caso di scuola: e' il modo normale in cui si rompe.
    """
    install = resolve_install_dir()
    if not install:
        return _no_install()
    _cfg, ns = cfg_load()
    mods = scan_mods(ns)
    print(f"installazione: {install}\n")

    logs = _scan_logs(install)
    newest = max([t for _r, t, _e in logs] or [0])
    window = core().LOG_RUN_WINDOW

    print("loader nativi (si sostituiscono a una DLL di sistema):")
    missing_loader, silent = [], []
    for name, rel, dll, logdir in LOADERS:
        here = os.path.isfile(os.path.join(install, rel.replace("/", os.sep)))
        wrote = _last_write(install, logdir) if here else None
        fresh = wrote is not None and (newest - wrote) <= window
        if not here:
            state = "manca il file"
            missing_loader.append((name, dll))
        elif fresh:
            state = f"caricato {_age(wrote)}"
        elif wrote:
            state = f"ultima volta {_age(wrote)}"
        else:
            state = "MAI CARICATO"
            silent.append((name, dll))
        print(f"  {'X' if here else ' '}  {name:<22} {rel:<24} {state}")

    print()
    st = override_state(install)
    todo = override_report(st, install)

    # un loader che c'e' ma non ha mai scritto e' il sintomo dell'override
    # mancante: le due diagnosi si confermano a vicenda, dirle insieme evita di
    # trattarle come due problemi diversi
    if silent:
        chi = ", ".join(n for n, _d in silent)
        verbo = "non ha mai girato" if len(silent) == 1 else "non hanno mai girato"
        if st["known"] and st["missing"]:
            print(f"\n! {chi} {verbo}, e l'override manca: e' quello.")
        else:
            print(f"\n! {chi} {verbo} pur essendo installat"
                  + ("o." if len(silent) == 1 else "i."))

    if missing_loader:
        print("\nloader non installati: "
              + ", ".join(n for n, _d in missing_loader)
              + "\n  pakrat cp2077 bootstrap")

    if not logs:
        print("\nnessun log trovato: il gioco non e' mai partito con queste mod,\n"
              "oppure non viene caricato niente del tutto.")
        if not missing_loader:
            print()
            print(override_advice(st, install))
        if todo:
            offer_override_fix(st, install)
        return 1

    # la resa a video e' del core; qui si sa solo che i log di redscript ruotano
    total_err, _old_err = core().report_logs(logs, rotated=_rotated)

    # un log piu' vecchio dell'ultima modifica alle mod descrive un'altra
    # configurazione: dirlo evita di dare la caccia a un problema gia' risolto
    last_change = max([int(m.entry.get("installed_at") or 0) for m in mods] or [0])
    if last_change and last_change > newest:
        print(f"\n! i log sono piu' vecchi dell'ultima modifica alle mod "
              f"({_age(last_change)}):\n  rilancia il gioco, quello che leggi qui "
              "descrive la configurazione precedente")

    if not total_err:
        print("\nnessun errore nell'ultima corsa: lo stack si carica.")

    red = [m for m in mods if "redmod" in m.kinds and m.enabled]
    if red:
        print(f"\n{len(red)} REDmod attive: se non le vedi in gioco manca il flag "
              "-modded\n  (pakrat cp2077 deploy)")
    if todo:
        offer_override_fix(st, install)
    return 0 if not (total_err or todo) else 1


def cmd_deploy(_args=None):
    """Spiega come far deployare i REDmod, senza chiamare Wine."""
    install = resolve_install_dir()
    if not install:
        return _no_install()
    _cfg, ns = cfg_load()
    red = [m for m in scan_mods(ns) if "redmod" in m.kinds and m.enabled]
    mods_root = os.path.join(install, "mods")
    on_disk = sorted(d for d in os.listdir(mods_root)
                     if os.path.isdir(os.path.join(mods_root, d))) \
        if os.path.isdir(mods_root) else []
    print(f"REDmod attive secondo pakrat: {len(red)}")
    for m in red:
        print(f"  {m.name}")
    print(f"cartelle in mods/: {len(on_disk)}")
    print("\npakrat non lancia redMod.exe: e' un eseguibile Windows e questo tool\n"
          "non dipende da Wine. Il deploy lo fa REDprelauncher all'avvio, se il\n"
          "gioco parte con il flag -modded.\n")
    print("In Heroic: Impostazioni del gioco -> Argomenti avanzati -> aggiungi\n"
          "  -modded\n")
    print("Il deploy rigenera r6/cache/modded/ e va rifatto a ogni cambio di mod:\n"
          "con il flag attivo succede da solo al lancio successivo.")
    if not red and on_disk:
        print("\nnota: ci sono cartelle in mods/ che pakrat non conosce "
              "(installate a mano).")
    return 0


def _fuzzy(a, b):
    """0..1 di somiglianza fra due nomi, ignorando spazi e punteggiatura."""
    import difflib
    na = re.sub(r"[^a-z0-9]+", "", (a or "").lower())
    nb = re.sub(r"[^a-z0-9]+", "", (b or "").lower())
    if not na or not nb:
        return 0.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    if na in nb:                      # "equipment ex" dentro "equipment-ex ..."
        r = max(r, 0.75 + 0.25 * len(na) / len(nb))
    return r


def search_nexus(term, api_key, limit=15):
    """Cerca su Nexus e ordina per somiglianza col termine.

    Il filtro del server fa match su SOTTOSTRINGA, non approssimato: se sbagli
    una lettera dentro una parola non trova niente, e non c'e' modo di rimediare
    da qui. Quello che si puo' fare, e che si fa, e' cercare anche le singole
    parole quando la frase intera non da' risultati, e riordinare per
    somiglianza cio' che torna.
    """
    c = core()
    found, tried = {}, [term]
    for n in c.nexus_search(term, api_key, count=40, game=NEXUS_GAME):
        found[n["modId"]] = n
    if not found:
        words = [w for w in re.split(r"\s+", term.strip()) if len(w) > 2]
        for w in words[:3]:
            tried.append(w)
            try:
                for n in c.nexus_search(w, api_key, count=25, game=NEXUS_GAME):
                    found[n["modId"]] = n
            except Exception:
                continue
    ranked = sorted(found.values(), key=lambda n: -_fuzzy(term, n.get("name")))
    return ranked[:limit], tried


def cmd_search(args):
    """Cerca mod su Nexus per nome, alla maniera di 'apt search'."""
    terms = [a for a in args if not a.startswith("-")]
    if not terms:
        print("uso: pakrat cp2077 search TERMINE [--limit N]", file=sys.stderr)
        return 1
    limit = 15
    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args) and args[i + 1].isdigit():
            limit = int(args[i + 1])
            terms = [t for t in terms if t != args[i + 1]]
    term = " ".join(terms)
    c = core()
    cfg = c.load_config()
    api_key = cfg.get("nexus_api_key")
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    try:
        hits, tried = search_nexus(term, api_key, limit)
    except Exception as ex:
        print(f"ricerca fallita: {ex}", file=sys.stderr)
        return 1
    if not hits:
        print(f"nessun risultato per '{term}'.")
        print("  il filtro di Nexus cerca sottostringhe, non parole simili:\n"
              "  un refuso dentro una parola non da' risultati. Prova un pezzo\n"
              "  piu' corto del nome, o una parola sola.")
        return 1

    # quali sono gia' installate: e' l'informazione che serve guardando una lista
    _cfg, ns = cfg_load()
    known = {}
    for slug, e in (ns.get("mods") or {}).items():
        if e.get("nexus_id"):
            known[int(e["nexus_id"])] = slug
    if len(tried) > 1:
        print(f"(nessun risultato per '{term}', ho cercato: "
              f"{', '.join(repr(t) for t in tried[1:])})\n")
    print(f"{'#':>3}  {'ID':>6}  {'MOD':<44} {'VERSIONE':>10}  AUTORE")
    for i, n in enumerate(hits, 1):
        mid = n["modId"]
        name = str(n.get("name") or "")[:44]
        ver = str(n.get("version") or "")[:10]
        who = str((n.get("uploader") or {}).get("name") or "")[:18]
        flags = " [18+]" if n.get("adultContent") else ""
        mark = "*" if mid in known else " "
        print(f"{i:3}{mark} {mid:>6}  {name:<44} {ver:>10}  {who}{flags}")
    if any(n["modId"] in known for n in hits):
        print("\n* gia' installata e collegata a Nexus")
    print("\npagina di una mod:  "
          f"https://www.nexusmods.com/{NEXUS_GAME}/mods/ID")
    print("una volta scaricata: pakrat cp2077 add ARCHIVIO.zip"
          "  poi  pakrat cp2077 link MOD ID")
    return 0


def fetch_and_install(mod_id, api_key, install, enable=True, file_id=None):
    """Scarica una mod da Nexus e la installa. Ritorna (slug, errore).

    Estratto da 'get' perche' lo usa anche l'installazione di una catena di
    dipendenze: la parte "prendi il file principale, scarica, installa, registra
    l'associazione a Nexus" e' la stessa, cambia solo chi decide gli ID.
    """
    c = core()
    try:
        info = nexus_get(f"/mods/{mod_id}.json", api_key)
        files = nexus_get(f"/mods/{mod_id}/files.json", api_key).get("files", [])
    except Exception as ex:
        return None, str(ex)
    name = str(info.get("name") or mod_id)
    print(f"{name} (v{info.get('version') or '?'}) — {info.get('author') or ''}")
    if file_id:
        f = next((x for x in files if int(x["file_id"]) == file_id), None)
        if f is None:
            return None, f"file_id {file_id} non trovato fra i file di questa mod"
    else:
        f = c.pick_main_file(files)
    if f is None:
        return None, "nessun file scaricabile"
    # senza premium l'API non da' link diretti: si passa dal browser, ed e'
    # esattamente cio' che fa il pulsante "Mod Manager Download"
    if not c.is_premium(api_key):
        return None, ("serve il download dal sito (account non premium):\n  "
                      + mod_page_url(mod_id, f["file_id"]))
    try:
        links = nexus_get(
            f"/mods/{mod_id}/files/{f['file_id']}/download_link.json", api_key)
    except Exception as ex:
        return None, f"link non ottenibile: {ex}"
    if not links:
        return None, "nessun link: " + mod_page_url(mod_id, f["file_id"])
    cache = os.path.join(c.CONFIG_DIR, "cache")
    os.makedirs(cache, exist_ok=True)
    archive = os.path.join(cache, f["file_name"])
    if not os.path.isfile(archive):
        print(f"  scarico {f['file_name']} ({f.get('size_kb', 0)/1024:.1f} MB)")
        try:
            c.download_url(links[0]["URI"], archive)
        except Exception as ex:
            return None, f"download fallito: {ex}"
    try:
        slug = install_archive(archive, install, enable=enable,
                               log=lambda s: print("  " + s.lstrip()))
    except Exception as ex:
        return None, str(ex)
    if not slug:
        return None, "installazione non riuscita"
    # arrivando da Nexus l'associazione la sappiamo gia': registrarla qui evita
    # il 'link' a mano e fa funzionare 'check' da subito
    cfg, ns = cfg_load()
    link_slug(slug, mod_id, ns, file_id=f["file_id"],
              version=str(info.get("version") or ""))
    e = ns.setdefault("mods", {}).setdefault(slug, {})
    e["display_name"] = name
    cfg_save(cfg)
    return slug, ""


def cmd_get(args):
    """Scarica e installa una mod dal suo ID Nexus: il passo dopo 'search'."""
    refs = [a for a in args if not a.startswith("-")]
    if not refs:
        print("uso: pakrat cp2077 get ID [ID...] [--file FILE_ID] [--no-enable]\n"
              "                          [--with-reqs]", file=sys.stderr)
        return 1
    file_id = None
    if "--file" in args:
        i = args.index("--file")
        if i + 1 < len(args) and args[i + 1].isdigit():
            file_id = int(args[i + 1])
            refs = [r for r in refs if r != args[i + 1]]
    enable = "--no-enable" not in args
    install = resolve_install_dir()
    if not install:
        return _no_install()
    c = core()
    api_key = c.load_config().get("nexus_api_key")
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    if not require_game_closed():
        return 1
    ids = []
    for ref in refs:
        mod_id = parse_ref(ref)
        if not mod_id:
            print(f"non e' un ID o un URL di mod: {ref}", file=sys.stderr)
            return 1
        ids.append(mod_id)
    if "--with-reqs" in args:
        ids = expand_reqs(ids, api_key, install)
    rc, done = 0, []
    for mod_id in ids:
        slug, err = fetch_and_install(mod_id, api_key, install, enable=enable,
                                      file_id=file_id if len(ids) == 1 else None)
        if err:
            print(f"  {err}", file=sys.stderr)
            rc = 1
            continue
        done.append(slug)
    if done:
        print(f"\nfatto. Controlla con: pakrat cp2077 list")
        _cfg, ns = cfg_load()
        need = sorted({fw for s in done
                       for fw in missing_frameworks(
                           (ns.get("mods") or {}).get(s, {}).get("files") or [],
                           install)})
        offer_bootstrap(need, install)
    return rc


# ------------------------------------------------- dipendenze fra mod Nexus ---
# L'API Nexus NON espone i "Requirements" di una mod: stanno solo nella pagina
# web. Ma la descrizione, quella si' che l'API la da', e nella descrizione gli
# autori i prerequisiti li linkano — con l'URL della mod, quindi con il suo ID.
#
# Quindi la catena si deduce invece di cablarla a mano in una tabella che
# invecchia. E' un'euristica, non un grafo: una descrizione linka anche le mod
# consigliate, quelle dell'autore e i ringraziamenti. Per questo si distingue
# "richiesto" da "citato" e non si installa niente senza mostrarlo prima.
_REQ_RE = re.compile(r"\b(require\w*|need\w*|mandatory|prerequisit\w*|"
                     r"obbligator\w*|must have)\b", re.I)
_NEG_RE = re.compile(r"\b(not|no longer|never|non)\b[^.]{0,24}?\b(require\w*|need\w*)\b",
                     re.I)
_OPT_RE = re.compile(r"\b(optional|recommend\w*|suggest\w*|consigliat\w*)\b", re.I)
# Un'intestazione e' una riga CORTA che parla di requisiti e non contiene link:
# "Requirements", "REQUIREMENTS:", "1. Install requirements". Pretendere che la
# riga sia solo quella parola non basta — quasi nessuno la scrive cosi'.
_HEAD_RE = re.compile(r"^(?=.{0,44}$)(?!.*nexusmods\.com)"
                      r".*\b(requirements?|required mods?|prerequisites?)\b.*$", re.I)
_LINK_RE = re.compile(r"nexusmods\.com/" + NEXUS_GAME + r"/mods/(\d+)", re.I)


def _plain_lines(desc):
    """La descrizione Nexus (BBCode + HTML) ridotta a righe leggibili.

    I tag si tolgono ma gli URL no: sono l'informazione che ci interessa, e in
    BBCode stanno dentro l'attributo del tag, non nel testo.
    """
    import html as _html
    t = _html.unescape(desc or "")
    # PRIMA gli url, e con il nome del tag delimitato: '[u...]' come alternativa
    # generica si mangiava anche '[url=...]' quando l'indirizzo era corto, e li'
    # dentro ci sono esattamente le dipendenze che cerchiamo
    t = re.sub(r"\[url=([^\]]+)\]", r" \1 ", t, flags=re.I)
    t = re.sub(r"\[/?url\]", " ", t, flags=re.I)
    t = re.sub(r"\[(/?)(?:b|i|u|size|color|center|left|right|quote|list|\*|img|"
               r"youtube|spoiler|font|line)(?=[\]= ])[^\]]{0,60}\]", " ", t,
               flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<a[^>]{0,200}?href=\"([^\"]+)\"[^>]*>", r" \1 ", t, flags=re.I)
    t = re.sub(r"<[^>]{0,120}>", " ", t)
    return [" ".join(x.split()) for x in t.split("\n")]


def page_requirements(mod_id, api_key, info=None):
    """[(id, forza, riga)] dedotti dalla descrizione. forza: 'richiesto'|'citato'."""
    if info is None:
        info = nexus_get(f"/mods/{mod_id}.json", api_key)
    found = {}
    under_head = 0
    for line in _plain_lines(info.get("description")):
        if _HEAD_RE.match(line):
            under_head = 8              # le righe subito dopo l'intestazione
            continue
        ids = [int(x) for x in _LINK_RE.findall(line)]
        strong = (_REQ_RE.search(line) and not _NEG_RE.search(line)
                  and not _OPT_RE.search(line)) or under_head > 0
        if line:
            under_head = max(0, under_head - 1)
        for i in ids:
            if i == int(mod_id):
                continue
            forza = "richiesto" if strong else "citato"
            old = found.get(i)
            if old is None or (old[0] == "citato" and forza == "richiesto"):
                found[i] = (forza, line[:160])
    return [(i, f, l) for i, (f, l) in found.items()]


_NAME_CACHE = {}


def mod_name(mod_id, api_key):
    """Il nome di una mod dal suo ID, ricordato per non richiederlo due volte."""
    mod_id = int(mod_id)
    if mod_id not in _NAME_CACHE:
        try:
            _NAME_CACHE[mod_id] = str(
                nexus_get(f"/mods/{mod_id}.json", api_key).get("name") or mod_id)
        except Exception:
            _NAME_CACHE[mod_id] = str(mod_id)
    return _NAME_CACHE[mod_id]


def page_url(mod_id):
    return f"https://www.nexusmods.com/{NEXUS_GAME}/mods/{mod_id}"


def _installed_nexus_ids(ns=None):
    if ns is None:
        _cfg, ns = cfg_load()
    out = {}
    for slug, e in (ns.get("mods") or {}).items():
        if e.get("nexus_id"):
            out[int(e["nexus_id"])] = slug
    return out


def _framework_by_name(nome):
    """Il core mod che si chiama cosi', se esiste. Confronto sui nomi e non su
    ID cablati: gli ID Nexus cambiano, e i core mod da noi arrivano da GitHub."""
    k = re.sub(r"[^a-z0-9]+", "", (nome or "").lower())
    for f in FRAMEWORKS:
        if k and k == re.sub(r"[^a-z0-9]+", "", f.lower()):
            return f
    return ""


def skip_reason(mod_id, api_key, install, have):
    """Perche' questa dipendenza non va installata, o stringa vuota.

    Il caso non ovvio e' il secondo: i core mod pakrat li prende dalle release
    GitHub, quindi nel config non hanno un ID Nexus e 'have' non li vede. Senza
    questo controllo una catena di dipendenze reinstallerebbe ArchiveXL e
    compagnia da Nexus, sopra quelli che ci sono gia'.
    """
    if mod_id in have:
        return "gia' installata"
    f = _framework_by_name(mod_name(mod_id, api_key))
    if f and framework_present(f, install):
        return f"gia' presente come core mod ({f})"
    return ""


def expand_reqs(ids, api_key, install, depth=1):
    """Gli ID da installare, prerequisiti prima. Salta quelli gia' installati.

    Si scende di un livello solo: due livelli su una descrizione scritta a mano
    portano dentro mezzo Nexus, e il rapporto fra veri prerequisiti e rumore
    peggiora in fretta.
    """
    have = _installed_nexus_ids()
    out, seen = [], set()

    def add(i, root=False):
        if i in seen:
            return
        seen.add(i)
        why = "" if root else skip_reason(i, api_key, install, have)
        if why:
            print(f"  {mod_name(i, api_key)}: {why}, salto")
            return
        out.append(i)

    for mod_id in ids:
        try:
            reqs = [(i, f, l) for i, f, l in page_requirements(mod_id, api_key)
                    if f == "richiesto"]
        except Exception as ex:
            print(f"  dipendenze di {mod_id} non leggibili: {ex}", file=sys.stderr)
            reqs = []
        for i, _f, _l in reqs:
            if depth > 1:
                for j, f2, _l2 in page_requirements(i, api_key):
                    if f2 == "richiesto":
                        add(j)
            add(i)
        add(mod_id, root=True)
    return out


def cmd_reqs(args):
    """Cosa pretende una mod, dedotto dalla sua pagina Nexus."""
    if not args:
        print("uso: pakrat cp2077 reqs ID|URL", file=sys.stderr)
        return 1
    mod_id = parse_ref(args[0])
    if not mod_id:
        print(f"non e' un ID o un URL di mod: {args[0]}", file=sys.stderr)
        return 1
    api_key = core().load_config().get("nexus_api_key")
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    try:
        info = nexus_get(f"/mods/{mod_id}.json", api_key)
        reqs = page_requirements(mod_id, api_key, info)
    except Exception as ex:
        print(f"errore: {ex}", file=sys.stderr)
        return 1
    print(f"{info.get('name')} (v{info.get('version') or '?'})\n")
    if not reqs:
        print("nessun prerequisito linkato nella descrizione.")
        print("Non vuol dire che non ne abbia: vuol dire che l'autore non l'ha\n"
              "scritto in modo leggibile da qui. Controlla la pagina:\n  "
              + page_url(mod_id))
        return 0
    have = _installed_nexus_ids()
    install = resolve_install_dir()
    reqs.sort(key=lambda r: (r[1] != "richiesto", r[0]))
    shown = 0
    for i, forza, line in reqs:
        if forza == "citato" and shown >= 6:
            continue
        shown += 1 if forza == "citato" else 0
        mark = "gia' installata" if i in have else ""
        print(f"  [{forza:<9}] {i:>6}  {mod_name(i, api_key)[:44]:<44} {mark}")
        print(f"              \"{line[:96]}\"")
    resto = sum(1 for _i, f, _l in reqs if f == "citato") - shown
    if resto > 0:
        print(f"  ... e altre {resto} citate nella descrizione (mod dell'autore,\n"
              "      alternative, ringraziamenti): non le tocco")
    print("\n'richiesto' e 'citato' li deduco dalla frase in cui compare il link:\n"
          "l'API Nexus i Requirements non li espone, la pagina web si'. Controlla\n"
          "prima di fidarti:  " + page_url(mod_id))
    if install:
        miss = [i for i, f, _l in reqs if f == "richiesto" and i not in have]
        if miss:
            print(f"\ninstalla mod e prerequisiti: "
                  f"pakrat cp2077 get {mod_id} --with-reqs")
    return 0


# ------------------------------------------------------- corpi (body mod) ---
# Cyberpunk non ha "il" body replacer: ne ha famiglie che si escludono a vicenda,
# perche' sostituiscono la stessa mesh. Sceglierne uno e' quindi una decisione,
# non un'installazione, e le conseguenze non sono ovvie: un corpo che cambia le
# forme (sculpt) richiede che i VESTITI siano rifatti su quelle forme, altrimenti
# compenetrano. I "refit" sono mod a parte, una per outfit.
#
# Questa tabella e' l'unica parte scritta a mano del meccanismo, e serve perche'
# l'API non sa dire ne' "questa mod e' un corpo" ne' "questa esclude quest'altra".
# I numeri di diffusione NON stanno qui: si chiedono a Nexus al momento, cosi' non
# invecchiano. La selezione e' quella dei piu' scaricati al 2026-08-16.
BODIES = [
    {"id": 7054, "nome": "VTK Vanilla HD Body for FemV", "fam": "VTK",
     "chi": "V femmina", "tipo": "sculpt", "refit": "no",
     "nota": "mesh e texture in alta risoluzione MANTENENDO le proporzioni "
             "vanilla: i vestiti del gioco continuano a calzare. Aggiunge il "
             "supporto a capezzoli, genitali e overlay. E' la base su cui "
             "poggiano quasi tutti gli altri corpi femminili"},
    {"id": 4654, "nome": "Enhanced Big Breasts (EBB)", "fam": "VTK",
     "chi": "V femmina", "tipo": "sculpt", "refit": "SI",
     "nota": "poggia su VTK e ne cambia le forme, molto piu' pronunciate. "
             "Cambiando forma servono i refit dei vestiti (l'autore ne "
             "pubblica due, vanilla e Phantom Liberty) piu' le jiggle physics"},
    {"id": 1424, "nome": "spawn0 - BODY MOD 2.0", "fam": "spawn0",
     "chi": "V femmina", "tipo": "rig", "refit": "no",
     "nota": "non rifa' la mesh: cambia le PROPORZIONI (seno, fianchi, spalle, "
             "cosce, braccia) da menu, in gioco, anche per gli NPC. Niente "
             "refit. E' il piu' aggiornato del gruppo"},
    {"id": 3667, "nome": "spawn0 - HIGH POLY BODY", "fam": "spawn0",
     "chi": "V femmina", "tipo": "add-on", "refit": "no",
     "nota": "non e' un corpo a se': aggiunge poligoni al corpo femminile "
             "perche' le forme spinte restino lisce. Fermo al 2022"},
    {"id": 6423, "nome": "Gymfiend - Body Mod - Male V", "fam": "VTK",
     "chi": "V maschio", "tipo": "sculpt", "refit": "SI",
     "nota": "corpo muscoloso per V maschile, lineage VTK. E' l'unica opzione "
             "maschile con una diffusione seria"},
    {"id": 3725, "nome": "Framework - Unique V Body Shape - Rig", "fam": "rig",
     "chi": "V", "tipo": "rig", "refit": "no",
     "nota": "da' a V una forma diversa da quella degli NPC senza toccare le "
             "mesh. L'autore lo marca LEGACY: usalo solo se una mod te lo "
             "chiede esplicitamente"},
]

# I download si chiedono al feed statistico pubblico di Nexus invece che all'API:
# una richiesta sola copre tutte le mod del gioco, non serve la chiave, e non si
# consuma quota. Il filtro GraphQL per piu' modId non serve allo scopo — mette in
# AND gli ID, quindi con due ID non torna mai niente.
STATS_URL = "https://staticstats.nexusmods.com/live_download_counts/mods/3333.csv"
STATS_TTL = 12 * 3600


def download_counts():
    """{mod_id: (download totali, utenti distinti)}, {} se non raggiungibile."""
    import urllib.request
    cache = os.path.join(core().CONFIG_DIR, "cache", "cp2077-downloads.csv")
    fresh = (os.path.isfile(cache)
             and time.time() - os.stat(cache).st_mtime < STATS_TTL)
    if not fresh:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            req = urllib.request.Request(STATS_URL,
                                         headers={"User-Agent": core().USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            with open(cache, "wb") as f:
                f.write(data)
        except Exception:
            if not os.path.isfile(cache):
                return {}
    out = {}
    try:
        with open(cache, errors="replace") as f:
            for line in f:
                p = line.split(",")
                if len(p) >= 3 and p[0].isdigit():
                    out[int(p[0])] = (int(p[1]), int(p[2]))
    except (OSError, ValueError):
        return {}
    return out

# Corpi diversi della stessa famiglia, o di famiglie diverse, si sovrascrivono:
# installarne due e' il modo piu' rapido per non capire piu' cosa si sta vedendo.
BODY_KEYS = {b["id"] for b in BODIES}


def _wrap(testo, largo):
    """Spezza una nota lunga in righe, senza dipendere da textwrap per due usi."""
    fuori, riga = [], ""
    for w in testo.split():
        if len(riga) + len(w) + 1 > largo:
            fuori.append(riga)
            riga = w
        else:
            riga = f"{riga} {w}".strip()
    return fuori + ([riga] if riga else [])


def cmd_body(args):
    """Scegli un corpo e installa quello che serve per farlo funzionare."""
    install = resolve_install_dir()
    if not install:
        return _no_install()
    api_key = core().load_config().get("nexus_api_key")
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    stats = download_counts()
    have = _installed_nexus_ids()
    refs = [a for a in args if not a.startswith("-")]

    if not refs:
        print("corpi per V (uno per volta: si sostituiscono la stessa mesh)\n")
        print(f"{'#':>2}  {'ID':>5}  {'CORPO':<38} {'CHI':<10} {'TIPO':<7} "
              f"{'REFIT':<5} {'UTENTI':>9}")
        for i, b in enumerate(BODIES, 1):
            _dl, uniq = stats.get(b["id"], (0, 0))
            mark = "  *" if b["id"] in have else ""
            print(f"{i:>2}  {b['id']:>5}  {b['nome'][:38]:<38} {b['chi']:<10} "
                  f"{b['tipo']:<7} {b['refit']:<5} {uniq:>9}{mark}")
            for riga in _wrap(b["nota"], 68):
                print(f"      {riga}")
        if any(b["id"] in have for b in BODIES):
            print("\n* gia' installato")
        print("\nsculpt = rifa' la mesh   rig = cambia le proporzioni dello scheletro")
        print("REFIT  = i vestiti vanno rifatti sulla nuova forma, altrimenti\n"
              "         compenetrano. I refit sono mod a parte, di solito una per\n"
              "         guardaroba (vanilla, Phantom Liberty, ogni mod di vestiti):\n"
              "         e' qui che sta il grosso del lavoro, e non e' automatizzabile.\n"
              "         Un corpo che tiene le proporzioni vanilla non ne ha bisogno.\n")
        print("per installarne uno:  pakrat cp2077 body N   (o l'ID Nexus)")
        print("cosa pretende uno:    pakrat cp2077 reqs ID")
        return 0

    ref = refs[0]
    b = None
    if ref.isdigit() and 1 <= int(ref) <= len(BODIES):
        b = BODIES[int(ref) - 1]
    else:
        mid = parse_ref(ref)
        b = next((x for x in BODIES if x["id"] == mid), None)
        if b is None and mid:
            b = {"id": mid, "fam": "?", "chi": "?", "tipo": "?",
                 "nota": "non e' fra i corpi che conosco: procedo lo stesso"}
    if b is None:
        print(f"non e' un indice ne' un ID: {ref}", file=sys.stderr)
        return 1

    conflitti = [x for x in BODIES if x["id"] in have and x["id"] != b["id"]
                 and x["chi"] == b.get("chi")]
    print(f"{b.get('nome') or b['id']}  ({b['chi']}, {b['tipo']})")
    print(f"  {b['nota']}\n")
    if conflitti:
        print("gia' installati per lo stesso personaggio:")
        for x in conflitti:
            print(f"  {x['nome']}")
        print("  due corpi sulla stessa mesh non si sommano: vince l'ordine di\n"
              "  caricamento, e non e' quello che vuoi. Disattiva l'altro con\n"
              "  'pakrat cp2077 disable' prima o dopo.\n")

    print("dipendenze dedotte dalla pagina Nexus:")
    try:
        reqs = page_requirements(b["id"], api_key)
    except Exception as ex:
        print(f"  non leggibili: {ex}", file=sys.stderr)
        reqs = []
    need, salti = [], {}
    for i, f, _l in reqs:
        if f != "richiesto":
            continue
        why = skip_reason(i, api_key, install, have)
        salti[i] = why
        if not why:
            need.append(i)
    for i, f, line in sorted(reqs, key=lambda r: r[1] != "richiesto"):
        if f != "richiesto":
            continue
        stato = f"  ({salti.get(i)})" if salti.get(i) else ""
        print(f"  {i:>6}  {mod_name(i, api_key)[:46]:<46}{stato}")
        print(f"          \"{line[:88]}\"")
    citate = [i for i, f, _l in reqs if f == "citato"]
    if not need and not any(f == "richiesto" for _i, f, _l in reqs):
        print("  nessuna dichiarata come richiesta nella descrizione")
    if citate:
        print(f"  (+{len(citate)} mod citate ma non dichiarate necessarie: refit dei\n"
              "   vestiti, texture, alternative — quelle le scegli tu)")
    print(f"\n  pagina: {page_url(b['id'])}")

    plan = need + [b["id"]] if b["id"] not in have else need
    if not plan:
        print("\nnon c'e' niente da installare: e' gia' tutto qui.")
        return 0
    print("\ninstallerei, in quest'ordine: " + ", ".join(str(i) for i in plan))
    if "--dry-run" in args:
        print("(--dry-run: non ho scaricato niente)")
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("da script non installo senza conferma. Con un terminale, rilancia\n"
              "senza --dry-run e rispondi si.")
        return 0
    if not require_game_closed():
        return 1
    try:
        ans = input("procedo? [s/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if ans not in ("s", "si", "sì", "y", "yes"):
        print("ok, non faccio niente.")
        return 0
    rc, done = 0, []
    for i in plan:
        slug, err = fetch_and_install(i, api_key, install, enable=True)
        if err:
            print(f"  {err}", file=sys.stderr)
            rc = 1
            continue
        done.append(slug)
    if done:
        print(f"\n{len(done)} installate. Controlla con: pakrat cp2077 list")
        _cfg, ns = cfg_load()
        fw = sorted({x for s in done for x in missing_frameworks(
            (ns.get("mods") or {}).get(s, {}).get("files") or [], install)})
        offer_bootstrap(fw, install)
    return rc


def cmd_link(args):
    if len(args) < 2:
        print("uso: pakrat cp2077 link MOD ID|URL", file=sys.stderr)
        return 1
    c = core()
    m = find_mod(args[0])
    if m is None:
        print(f"mod non trovata (o ambigua): {args[0]}", file=sys.stderr)
        return 1
    try:
        mod_id = parse_ref(args[1])
    except c.NexusError as ex:
        print(str(ex), file=sys.stderr)
        return 1
    cfg, ns = cfg_load()
    api_key = cfg.get("nexus_api_key")
    version = ""
    if api_key:
        try:
            info = nexus_get(f"/mods/{mod_id}.json", api_key)
            version = str(info.get("version") or "").strip()
            ns.setdefault("mods", {}).setdefault(m.slug, {})["display_name"] = \
                str(info.get("name") or m.name)
        except c.NexusError as ex:
            print(f"attenzione: {ex}", file=sys.stderr)
    link_slug(m.slug, mod_id, ns, version=version)
    cfg_save(cfg)
    print(f"{m.slug} -> nexusmods.com/{NEXUS_GAME}/mods/{mod_id}"
          + (f" (v{version})" if version else ""))
    print("nota: senza il file di provenienza il confronto versioni e' parziale;\n"
          "      si registra da solo al primo update o download via nxm://")
    return 0


def cmd_check(_args=None):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    cfg, ns = cfg_load()
    c = core()
    api_key = cfg.get("nexus_api_key")
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    mods = [m for m in scan_mods(ns) if m.nexus_id]
    if not mods:
        print("nessuna mod associata a Nexus (usa: pakrat cp2077 link MOD ID)")
        return 0
    updates = unknown = 0
    for m in mods:
        e = ns["mods"][m.slug]
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
          + ("  (pakrat cp2077 update)" if updates else ""))
    if unknown:
        print(f"{unknown} non verificabili: reinstallale con "
              "'pakrat cp2077 update MOD' per registrare il file di provenienza")
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
        return False, (f"gia' aggiornata ({rv or '?'}); {note} — "
                       "per cambiare variante scaricala dal sito")
    if f is None:
        return False, "nessun file scaricabile"
    if state == "unknown":
        log("  attenzione: file di provenienza non registrato, "
            f"installo il principale ({f.get('name')})")
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
    # stesso slug: l'aggiornamento sostituisce i file della versione precedente
    try:
        got = install_archive(archive, install, enable=True, log=log, slug=m.slug)
    except Exception as ex:
        return False, str(ex)
    if not got:
        return False, "installazione non riuscita"
    cfg, ns = cfg_load()
    link_slug(m.slug, mod_id, ns, file_id=f["file_id"], version=rv)
    cfg_save(cfg)
    return True, f"aggiornata a {rv or '?'}"


def cmd_update(args):
    install = resolve_install_dir()
    if not install:
        return _no_install()
    cfg, ns = cfg_load()
    api_key = cfg.get("nexus_api_key")
    if not api_key:
        print("API key non configurata: pakrat apikey LA_TUA_CHIAVE", file=sys.stderr)
        return 1
    mods = scan_mods(ns)
    if args:
        m = find_mod(args[0], mods)
        if m is None:
            print(f"mod non trovata (o ambigua): {args[0]}", file=sys.stderr)
            return 1
        targets = [m]
    else:
        targets = [m for m in mods if m.nexus_id]
    if not targets:
        print("nessuna mod associata a Nexus (usa: pakrat cp2077 link MOD ID)")
        return 0
    rc = 0
    for m in targets:
        print(f"{m.name}:")
        ok, msg = update_one(m, ns.get("mods", {}).get(m.slug, {}), api_key, install)
        print("  " + msg)
        if not ok and "gia'" not in msg:
            rc = 1
    return rc


def cmd_nxm(url):
    """Handler dei link nxm://cyberpunk2077/... ."""
    import urllib.parse
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
    api_key = cfg.get("nexus_api_key")
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

    version, mod_name = "", ""
    try:
        for f in nexus_get(f"/mods/{mod_id}/files.json", api_key).get("files", []):
            if f.get("file_id") == file_id:
                version = (f.get("version") or "").strip()
                break
        mod_name = str(nexus_get(f"/mods/{mod_id}.json", api_key).get("name") or "")
    except c.NexusError:
        pass

    try:
        slug = install_archive(dest, install, enable=True)
    except Exception as ex:
        print(f"installazione fallita: {ex}", file=sys.stderr)
        print(f"l'archivio resta in {dest}")
        return 1
    if not slug:
        return 1
    cfg, ns = cfg_load()
    e = link_slug(slug, mod_id, ns, file_id=file_id, version=version)
    if mod_name:
        e["display_name"] = mod_name
    cfg_save(cfg)
    print(f"associata a nexusmods.com/{NEXUS_GAME}/mods/{mod_id}"
          + (f" (v{version})" if version else ""))
    print("pronta: le mod attive si caricano al prossimo avvio del gioco")
    return 0


HELP = """pakrat cp2077 - Cyberpunk 2077

  list                  elenco mod, stato, ordine e numero di file
  add ARCHIVIO [...]    installa da zip/7z/rar
                        --no-enable installa senza attivare
                        --name NOME forza il nome della mod
  enable MOD [...]      attiva (rimette i file in gioco)
  disable MOD [...]     disattiva (sposta i file nel deposito)
  order MOD N           prefisso NNN_ sugli .archive ('-' per toglierlo)
  remove MOD [...]      sposta in archivio (--purge per cancellare davvero)
  restore [N|NOME]      elenca l'archivio, o ripristina una mod rimossa
  verify                confronta il manifest col disco, trova gli orfani
  deps                  stato dei core mod e prerequisiti dedotti
  bootstrap [NOME...]   scarica e installa i core mod mancanti, in ordine
                        NOME==1.36.0 fissa una versione (i bootstrap successivi
                        non la toccano), NOME==latest toglie il pin
                        --dry-run mostra cosa farebbe, --force reinstalla
  deploy                cosa serve per far caricare i REDmod
  doctor                dopo una partita: cosa il gioco ha caricato davvero,
                        e se Proton carica i loader nativi (WINEDLLOVERRIDES)
  search TERMINE        cerca mod su Nexus per nome (--limit N)
  get ID [ID...]        scarica e installa da Nexus per ID (premium)
                        --file FILE_ID sceglie un file preciso
                        --with-reqs installa prima i prerequisiti dedotti
  reqs ID               cosa pretende una mod, dedotto dalla pagina Nexus
  body [N|ID]           elenca i corpi opzionali, o ne installa uno con la
                        sua catena (--dry-run mostra e basta)
  link MOD ID           associa una mod alla sua pagina Nexus
  check                 cerca aggiornamenti su Nexus
  update [MOD]          scarica e installa gli aggiornamenti
  setup [PERCORSO]      mostra o imposta l'installazione

MOD si indica per indice (da 'list'), ID Nexus, slug o nome visualizzato.
Un numero e' prima l'indice, poi l'ID Nexus; 'id:6945' toglie il dubbio.

Qui una mod e' un insieme di file sparsi sulla radice del gioco, non una
cartella: pakrat tiene il manifest di cosa ha installato e lavora su quello.
"""


def main(args):
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
        "verify": lambda: cmd_verify(rest),
        "deps": lambda: cmd_deps(rest),
        "search": lambda: cmd_search(rest),
        "get": lambda: cmd_get(rest),
        "reqs": lambda: cmd_reqs(rest),
        "body": lambda: cmd_body(rest),
        "install": lambda: cmd_get(rest),
        "bootstrap": lambda: cmd_bootstrap(rest),
        "core": lambda: cmd_bootstrap(rest),
        "deploy": lambda: cmd_deploy(rest),
        "doctor": lambda: cmd_doctor(rest),
        "link": lambda: cmd_link(rest),
        "check": lambda: cmd_check(rest),
        "update": lambda: cmd_update(rest),
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
