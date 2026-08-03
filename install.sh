#!/usr/bin/env bash
# Installa pakrat: symlink in ~/.local/bin, voce di menu e icona.
# Idempotente: si puo' rilanciare senza danni.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"

echo "sorgente: $SRC"

# --- dipendenze -------------------------------------------------------------
missing=()
python3 -c "import lz4.block" 2>/dev/null || missing+=("python-lz4")
python3 -c "import PySide6.QtWidgets" 2>/dev/null || missing+=("pyside6 (solo per la GUI)")
if ((${#missing[@]})); then
    echo "dipendenze mancanti: ${missing[*]}"
    echo "  su Arch/CachyOS:  sudo pacman -S --needed python-lz4 pyside6 p7zip unrar"
    echo
fi

# --- eseguibili -------------------------------------------------------------
mkdir -p "$BIN"
for f in pakrat pakrat-gui; do
    chmod +x "$SRC/$f"
    ln -sfn "$SRC/$f" "$BIN/$f"
    echo "  $BIN/$f -> $SRC/$f"
done

# --- residui del vecchio nome (bg3mods) -------------------------------------
# Lasciarli in giro significa due comandi che fanno la stessa cosa e due voci
# che si contendono i link nxm://.
for old in "$BIN/bg3mods" "$BIN/bg3mods-gui"; do
    if [ -L "$old" ] || [ -f "$old" ]; then
        rm -f "$old"
        echo "  rimosso $old (vecchio nome)"
    fi
done
for old in "$APPS/bg3mods.desktop" "$APPS/bg3mods-nxm.desktop"; do
    if [ -f "$old" ]; then
        rm -f "$old"
        echo "  rimossa $(basename "$old") (vecchio nome)"
    fi
done
find "$ICONS" -name 'bg3mods.png' -delete 2>/dev/null || true

# --- icona: usa quella del gioco se la si trova, altrimenti una generica -----
mkdir -p "$ICONS/256x256/apps"
icon_name="applications-games"
if command -v magick >/dev/null; then
    for ico in /mnt/*/HeroicGames/"Baldurs Gate 3"/goggame-*.ico \
               "$HOME"/Games/*/"Baldurs Gate 3"/goggame-*.ico; do
        [ -f "$ico" ] || continue
        if magick "$ico[0]" -resize 256x256 "$ICONS/256x256/apps/pakrat.png" 2>/dev/null; then
            for sz in 48 32; do
                mkdir -p "$ICONS/${sz}x${sz}/apps"
                magick "$ico[0]" -resize ${sz}x${sz} "$ICONS/${sz}x${sz}/apps/pakrat.png" 2>/dev/null || true
            done
            icon_name="pakrat"
            echo "  icona estratta da $(basename "$ico")"
            break
        fi
    done
fi
[ "$icon_name" = "applications-games" ] && echo "  icona del gioco non trovata, uso quella generica"

# --- voce di menu -----------------------------------------------------------
mkdir -p "$APPS"
term="$(command -v konsole || command -v gnome-terminal || command -v xterm || true)"
cat > "$APPS/pakrat.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=pakrat
GenericName=Gestore mod
Comment=Gestisce mod e load order di Baldur's Gate 3 e MechWarrior 5 (nativo, senza Wine)
Exec=$BIN/pakrat-gui %f
Icon=$icon_name
Terminal=false
Categories=Game;
Keywords=bg3;baldur;mechwarrior;mw5;mod;nexus;larian;
StartupNotify=true
StartupWMClass=pakrat
EOF

if [ -n "$term" ]; then
    hold=""
    [ "$(basename "$term")" = "konsole" ] && hold="--hold"
    cat >> "$APPS/pakrat.desktop" <<EOF
Actions=TUI;Check;

[Desktop Action TUI]
Name=Apri in terminale (TUI)
Exec=$term -e $BIN/pakrat
Icon=utilities-terminal

[Desktop Action Check]
Name=Controlla aggiornamenti Nexus
Exec=$term $hold -e $BIN/pakrat check
Icon=view-refresh
EOF
fi

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

echo
echo "fatto. Prova con:  pakrat list          (Baldur's Gate 3)"
echo "                  pakrat mw5 list      (MechWarrior 5)"
echo "Per gli aggiornamenti da Nexus:  pakrat apikey LA_TUA_CHIAVE"
echo "Per i pulsanti 'Mod Manager Download' di Nexus:  pakrat handler"
