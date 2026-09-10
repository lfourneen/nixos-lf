#!/usr/bin/env bash

# Push this repo's NixOS config to /etc/nixos (used by
# `nixos-rebuild --flake .#lfour`). Run as your normal user (uses sudo).
#
# Copied:  home/ (incl. wallpapers), overlays/ (incl. local-apps/*.AppImage),
#          system/ (incl. secrets/secrets.yaml), flake.nix, flake.lock, .sops.yaml
# Skipped: .git/, .gitignore, scripts/, *.md (GitHub-only READMEs), LICENSE,
#          and junk (result*, *.swp, *~, *.bak, .DS_Store, .direnv/).
# IMPORTANT: do NOT filter by .gitignore — that would drop the AppImage and
# secrets.yaml and break the build.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="/etc/nixos"

# Never work from inside the destination: a process whose cwd is inside it is
# the usual cause of "Device or resource busy" during cleanup.
cd /

echo "🧹 Cleaning $DEST_DIR ..."

if mountpoint -q "$DEST_DIR" 2>/dev/null; then
  echo "❌ $DEST_DIR is a mountpoint; refusing to touch it." >&2
  exit 1
fi

# Warn (do not fail) about PIDs whose cwd is inside DEST_DIR.
busy=""
for p in /proc/[0-9]*/cwd; do
  pid="${p#/proc/}"; pid="${pid%/cwd}"
  tgt="$(sudo readlink "$p" 2>/dev/null)" || continue
  case "$tgt" in
    "$DEST_DIR"|"$DEST_DIR"/*) busy="$busy $pid" ;;
  esac
done
if [ -n "$busy" ]; then
  echo "⚠️  PIDs with cwd inside $DEST_DIR:$busy  (cd out of it if cleanup complains)"
fi

if [ ! -d "$DEST_DIR" ]; then
  sudo install -d -m 700 "$DEST_DIR"
elif sudo find "$DEST_DIR" -mindepth 1 -delete 2>/dev/null; then
  sudo chmod 700 "$DEST_DIR"
else
  # A busy entry blocked the in-place delete. Move the old tree aside (same
  # filesystem), recreate a clean dir, then remove the old tree best-effort.
  trash="/etc/.nixos-old.$$"
  echo "⚠️  In-place cleanup hit a busy entry; moving old tree to $trash and recreating."
  sudo rm -rf "$trash" 2>/dev/null || true
  if ! sudo mv "$DEST_DIR" "$trash"; then
    echo "❌ Could not move $DEST_DIR aside (still busy); nothing changed." >&2
    exit 1
  fi
  sudo install -d -m 700 "$DEST_DIR"
  if ! sudo rm -rf "$trash" 2>/dev/null; then
    echo "⚠️  Some entries in $trash are still busy; left in place (safe to remove later)."
  fi
fi

echo "📂 Syncing config to $DEST_DIR ..."

# Junk that must never end up in /etc/nixos. *.md = GitHub-only README/docs.
excludes=(
  --exclude '.git/' --exclude '.direnv/'
  --exclude 'result' --exclude 'result-*'
  --exclude '*.swp' --exclude '*~' --exclude '*.bak' --exclude '.DS_Store'
  --exclude '*.md'
)

# home/ : keep local wallpapers; README placeholders are dropped by *.md.
sudo rsync -a --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/home/" "$DEST_DIR/home/"
# overlays/ : MUST keep local-apps/*.AppImage (bilibili build dep).
sudo rsync -a --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/overlays/" "$DEST_DIR/overlays/"
# system/ : must keep secrets/secrets.yaml.
sudo rsync -a --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/system/" "$DEST_DIR/system/"

for f in flake.nix flake.lock .sops.yaml; do
  sudo install -m 600 -o root -g root "$REPO_DIR/$f" "$DEST_DIR/$f"
done

echo "🔒 Setting secure permissions (dirs 700 / files 600) ..."
sudo find "$DEST_DIR" -type d -exec chmod 700 {} +
sudo find "$DEST_DIR" -type f -exec chmod 600 {} +

echo "✅ Done! Config pushed to $DEST_DIR."

