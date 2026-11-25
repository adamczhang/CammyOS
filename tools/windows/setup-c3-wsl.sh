#!/usr/bin/env bash
# Set up Cammy Control Center (C3) inside WSL2 Ubuntu on Windows (docs/WINDOWS.md).
# Also works on a plain Ubuntu 24.04 PC. Run it from the repository root:
#
#   ./tools/windows/setup-c3-wsl.sh [--venv NAME] [--model-dir DIR]
#                                   [--lab-from PATH --serve-from PATH] [--check]
#
#   --venv NAME        Python environment folder in the repository (default .venv-live;
#                      use .venv-wsl when the repository is shared with a Linux boot)
#   --model-dir DIR    where the speech spotter model goes (default ~/cammy-models)
#   --lab-from PATH    dual boot only: link /run/media/$USER/LAB to PATH at every WSL
#                      start (PATH is where `wsl --mount` put the Linux project disk)
#   --serve-from PATH  dual boot only: link /run/media/$USER/SERVE to PATH (e.g. /mnt/d)
#   --check            report what is missing; change nothing
#
# It never prints credentials. Status: v1.0-alpha1, not yet run on Windows.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
VENV=.venv-live
MODEL_DIR="$HOME/cammy-models"
LAB_FROM= SERVE_FROM= CHECK=0
MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin
MODEL_SHA=921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f
APT_PACKAGES=(ffmpeg python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 curl ca-certificates iproute2 iputils-ping git
              g++ make libwebrtc-audio-processing-1-3)

while [ $# -gt 0 ]; do
  case "$1" in
    --venv) VENV=$2; shift 2;;
    --model-dir) MODEL_DIR=$2; shift 2;;
    --lab-from) LAB_FROM=$2; shift 2;;
    --serve-from) SERVE_FROM=$2; shift 2;;
    --check) CHECK=1; shift;;
    -h|--help) sed -n '2,17p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

ok()   { printf '  ok    %s\n' "$*"; }
todo() { printf '  TODO  %s\n' "$*"; MISSING=$((MISSING + 1)); }
step() { printf '\n== %s\n' "$*"; }
run()  { if [ "$CHECK" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi; }
MISSING=0

step "Environment"
if grep -qi microsoft /proc/version 2>/dev/null; then ok "running inside WSL"; else ok "not WSL: setting up a plain Linux PC"; fi
. /etc/os-release 2>/dev/null || true
case "${VERSION_ID:-}" in 24.04|24.10|25.*|26.*) ok "${PRETTY_NAME:-Linux}";; *) echo "  note  tested target is Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}";; esac
[ -f "$REPO/host/c3/service.py" ] || { echo "run this from a CammyOS checkout (host/c3 missing)" >&2; exit 1; }
ok "repository: $REPO"

step "systemd (C3 runs as a systemd user service)"
if [ "$(ps -p 1 -o comm= 2>/dev/null)" = systemd ]; then
  ok "systemd is running"
else
  todo "systemd is off. Adding [boot] systemd=true to /etc/wsl.conf; then in PowerShell run 'wsl --shutdown' and start this script again"
  if [ "$CHECK" = 0 ] && ! grep -q '^systemd=true' /etc/wsl.conf 2>/dev/null; then
    printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
  fi
  exit 1
fi

step "System packages"
missing=()
for p in "${APT_PACKAGES[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ ${#missing[@]} -eq 0 ]; then ok "all present"; else
  echo "  installing: ${missing[*]}"
  run sudo apt-get update -q
  run sudo apt-get install -y -q "${missing[@]}"
fi

step "Python environment ($VENV, Python 3.12)"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "  installing uv (https://astral.sh/uv) into ~/.local/bin"
  if [ "$CHECK" = 0 ]; then curl -LsSf https://astral.sh/uv/install.sh | sh; fi
fi
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
PY="$REPO/$VENV/bin/python"
if [ ! -x "$PY" ]; then run "$UV" venv --python 3.12 "$REPO/$VENV"; fi
run "$UV" pip install --python "$PY" -q -r "$REPO/host/requirements-c3.txt"
if [ "$CHECK" = 0 ]; then
  "$PY" -c 'import google.genai, aiohttp, numpy, onnxruntime, pywhispercpp, PIL' && ok "C3 packages import"
fi

step "Echo canceller (build/c3/libc3aec.so; only full duplex needs it)"
if [ -f "$REPO/build/c3/libc3aec.so" ]; then ok "already built"
elif [ "$CHECK" = 1 ]; then echo "  would run: make -C host/c3/aec headers && make -C host/c3/aec"
elif make -C "$REPO/host/c3/aec" headers >/dev/null && make -C "$REPO/host/c3/aec" >/dev/null; then ok "built"
else echo "  note  build failed: C3 still runs, in half duplex only"; fi

step "Speech spotter model (whisper.cpp tiny.en, 78 MB)"
MODEL="$MODEL_DIR/ggml-tiny.en.bin"
if [ -f "$MODEL" ] && [ "$(sha256sum "$MODEL" | cut -c1-64)" = "$MODEL_SHA" ]; then ok "$MODEL"; else
  run mkdir -p "$MODEL_DIR"
  run curl -L --fail -o "$MODEL.part" "$MODEL_URL"
  if [ "$CHECK" = 0 ]; then
    [ "$(sha256sum "$MODEL.part" | cut -c1-64)" = "$MODEL_SHA" ] || { echo "model checksum mismatch" >&2; exit 1; }
    mv "$MODEL.part" "$MODEL"; ok "$MODEL"
  fi
fi

step "Dual-boot links (optional)"
if [ -n "$LAB_FROM$SERVE_FROM" ]; then
  rules=""
  [ -n "$LAB_FROM" ] && rules+="L+ /run/media/$USER/LAB - - - - $LAB_FROM"$'\n'
  [ -n "$SERVE_FROM" ] && rules+="L+ /run/media/$USER/SERVE - - - - $SERVE_FROM"$'\n'
  if [ "$CHECK" = 0 ]; then
    printf 'd /run/media/%s 0755 %s %s -\n%s' "$USER" "$USER" "$USER" "$rules" | sudo tee /etc/tmpfiles.d/cammy-dualboot.conf >/dev/null
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/cammy-dualboot.conf
  fi
  ok "links recreated at every WSL start (/etc/tmpfiles.d/cammy-dualboot.conf)"
else
  ok "not requested"
fi

step "Credentials (contents are never shown)"
private_file() {  # path, what it is
  if [ -s "$1" ]; then
    mode=$(stat -c %a "$1"); [ "${mode#?}" = 00 ] && ok "$2 ($1)" || todo "$2 is readable by others: chmod 600 '$1'"
  else todo "$2: create $1 (see docs/WINDOWS.md, step 4)"; fi
}
private_file "$REPO/private/credentials/gemini.key" "Gemini API key"
CONFIG="$REPO/private/host/config.json"
if [ -s "$CONFIG" ]; then
  TOKEN_FILE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token_file",""))' "$CONFIG")
  [ -n "$TOKEN_FILE" ] && private_file "$TOKEN_FILE" "camera portal token" || todo "private/host/config.json has no token_file"
else
  todo "camera settings: create $CONFIG (see docs/WINDOWS.md, step 4)"
fi

step "C3 service"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/cammy-c3.service"
unit_text="# Generated by tools/windows/setup-c3-wsl.sh. Edit the script, not this file.
[Unit]
Description=Cammy Control Center (C3)
After=network-online.target
ConditionPathExists=$REPO/host/c3/service.py

[Service]
WorkingDirectory=$REPO/host
Environment=CAMMY_SPOTTER_MODEL=$MODEL
ExecStart=$REPO/$VENV/bin/python -m c3 --deployment adult-standalone --model gemini-3.1-flash-live-preview
Restart=always
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=default.target
"
if [ -L "$UNIT" ]; then
  todo "$UNIT is a link (a Linux boot's own unit); leaving it alone"
elif [ "$CHECK" = 0 ]; then
  mkdir -p "$UNIT_DIR"; printf '%s' "$unit_text" >"$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable cammy-c3.service >/dev/null 2>&1 && ok "installed and enabled (not started)"
  # Lingering keeps the user's services running without an open terminal.
  loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes || sudo loginctl enable-linger "$USER"
else
  echo "  would write $UNIT and enable it"
fi

step "CCC window (shows in the Windows Start menu through WSLg)"
DESKTOP="$HOME/.local/share/applications/cammy-c3.desktop"
if [ "$CHECK" = 0 ]; then
  mkdir -p "$(dirname "$DESKTOP")"
  printf '[Desktop Entry]\nType=Application\nName=Cammy Control Center\nExec=/usr/bin/python3 %s/host/c3/gui.py\nTerminal=false\nCategories=Utility;\nStartupWMClass=io.cammy.ControlCenter\n' "$REPO" >"$DESKTOP"
fi
ok "$DESKTOP"

step "Finding Cammy on the network"
if [ "$CHECK" = 0 ] && [ -x "$PY" ]; then
  found=$(cd "$REPO/host" && "$PY" -c 'from c3 import discover; print(discover.sweep() or "")' 2>/dev/null || true)
  if [ -n "$found" ]; then ok "Cammy's portal answers at $found"
  else todo "no Cammy portal found. Is the camera on, running CammyOS, on this Wi-Fi? On Windows, mirrored networking must be on (Setup-CammyWSL.ps1 does it)."; fi
fi

printf '\n'
if [ "$MISSING" -gt 0 ]; then echo "$MISSING item(s) above need attention, then run this script again."; exit 1; fi
echo "Ready. Start C3:  systemctl --user start cammy-c3"
echo "Open the window: Start menu > Cammy Control Center (or: python3 $REPO/host/c3/gui.py)"
