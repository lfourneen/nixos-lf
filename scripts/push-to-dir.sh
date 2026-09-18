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
#
# Works when DEST_DIR is an impermanence bind mount (e.g. /etc/nixos backed by
# /persist): only the *contents* are replaced. The directory/mount itself is
# never removed, moved or recreated — it cannot be (EBUSY), and the boot-time
# bind mount must stay intact.
#
# Overridable for testing:
#   DEST_DIR  target directory (default /etc/nixos)
#   SUDO      privilege prefix (default `sudo`); `SUDO=` runs every step
#             unprivileged, e.g. against a throwaway DEST_DIR.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="${DEST_DIR:-/etc/nixos}"
SUDO="${SUDO-sudo}"

# Privileged-command prefix; empty when SUDO= (unprivileged self-test).
# NB: expanding an empty array under `set -u` is safe in bash >= 4.4.
if [ -n "$SUDO" ]; then SUDO_CMD=("$SUDO"); else SUDO_CMD=(); fi
# install -o/-g needs real privileges: keep them for the normal (sudo) run and
# skip only the ownership change when running unprivileged.
if [ "${#SUDO_CMD[@]}" -gt 0 ]; then OWN=(-o root -g root); else OWN=(); fi

# Never work from inside the destination: a process whose cwd is inside it is
# the usual cause of "Device or resource busy" during cleanup.
cd /

echo "🧹 Cleaning $DEST_DIR ..."

if mountpoint -q "$DEST_DIR" 2>/dev/null; then
  echo "ℹ️  $DEST_DIR is a mountpoint (impermanence bind mount); replacing its contents only, the mount itself is left intact." >&2
fi

# Warn (do not fail) about PIDs whose cwd is inside DEST_DIR.
busy=""
for p in /proc/[0-9]*/cwd; do
  pid="${p#/proc/}"; pid="${pid%/cwd}"
  tgt="$("${SUDO_CMD[@]}" readlink "$p" 2>/dev/null)" || continue
  case "$tgt" in
    "$DEST_DIR"|"$DEST_DIR"/*) busy="$busy $pid" ;;
  esac
done
if [ -n "$busy" ]; then
  echo "⚠️  PIDs with cwd inside $DEST_DIR:$busy  (cd out of it if cleanup complains)"
fi

if [ ! -d "$DEST_DIR" ]; then
  "${SUDO_CMD[@]}" install -d -m 700 "$DEST_DIR"
else
  # Replace the contents only - `-mindepth 1` never touches DEST_DIR itself, so
  # a mountpoint stays mounted. Anything that survives the delete is reported
  # and left to `rsync --delete` below.
  if ! "${SUDO_CMD[@]}" find "$DEST_DIR" -mindepth 1 -delete 2>/dev/null; then
    left="$("${SUDO_CMD[@]}" find "$DEST_DIR" -mindepth 1 2>/dev/null || true)"
    if [ -n "$left" ]; then
      echo "⚠️  Some entries in $DEST_DIR could not be deleted (rsync --delete will handle them):" >&2
      printf '%s\n' "$left" >&2
    fi
  fi
  "${SUDO_CMD[@]}" chmod 700 "$DEST_DIR"
fi

echo "📂 Syncing config to $DEST_DIR ..."

# Junk that must never end up in /etc/nixos. *.md = GitHub-only README/docs.
excludes=(
  --exclude '.git/' --exclude '.direnv/'
  --exclude 'result' --exclude 'result-*'
  --exclude '*.swp' --exclude '*~' --exclude '*.bak' --exclude '.DS_Store'
  --exclude '*.md'
)

# --delete: stale files inside the synced trees are dropped even if the cleanup
# above could not empty the directory completely.
# home/ : keep local wallpapers; README placeholders are dropped by *.md.
"${SUDO_CMD[@]}" rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/home/" "$DEST_DIR/home/"
# overlays/ : MUST keep local-apps/*.AppImage (bilibili build dep).
"${SUDO_CMD[@]}" rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/overlays/" "$DEST_DIR/overlays/"
# system/ : must keep secrets/secrets.yaml.
"${SUDO_CMD[@]}" rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/system/" "$DEST_DIR/system/"

for f in flake.nix flake.lock .sops.yaml; do
  "${SUDO_CMD[@]}" install -m 600 "${OWN[@]}" "$REPO_DIR/$f" "$DEST_DIR/$f"
done

echo "🔒 Setting secure permissions (dirs 700 / files 600) ..."
# Best effort: an entry we cannot descend into (leftover from the cleanup
# above) must not turn an otherwise completed push into a failure.
if ! "${SUDO_CMD[@]}" find "$DEST_DIR" -type d -exec chmod 700 {} + 2>/dev/null; then
  echo "⚠️  Some directories under $DEST_DIR could not be tightened to 700." >&2
fi
if ! "${SUDO_CMD[@]}" find "$DEST_DIR" -type f -exec chmod 600 {} + 2>/dev/null; then
  echo "⚠️  Some files under $DEST_DIR could not be tightened to 600." >&2
fi

echo "✅ Done! Config pushed to $DEST_DIR."
