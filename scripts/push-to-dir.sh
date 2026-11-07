#!/usr/bin/env bash

# Push this repo's NixOS config to /etc/nixos (used by
# `nixos-rebuild --flake .#lfour`).
#
# Run it as your normal user: the script elevates itself once via sudo, so the
# password is asked at most once for the whole run (zero times while sudo's
# timestamp is still valid). Running it as root (`su -`) works too.
#
# Copied:  home/ (incl. wallpapers), overlays/,
#          system/ (incl. secrets/secrets.yaml), tests/ (flake check sources),
#          local-data/ (generated inputs kept out of Git, e.g.
#          dns-blocklist.txt.xz - the flake cannot evaluate without it),
#          flake.nix, flake.lock, .sops.yaml
# Skipped: .git/, .gitignore, scripts/, *.md (GitHub-only READMEs, except
#          system/programs/hermes-skills/** which the Hermes build needs),
#          LICENSE, and junk (result*, *.swp, *~, *.bak, .DS_Store, .direnv/).
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

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_FILE="$SCRIPT_DIR/$(basename -- "${BASH_SOURCE[0]}")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="${DEST_DIR:-/etc/nixos}"
SUDO="${SUDO-sudo}"

# local-data/dns-blocklist.txt.xz is git-ignored and read at evaluation time,
# so a fresh clone does not have it. Check before elevating (and before
# touching DEST_DIR) instead of leaving behind a tree that cannot be evaluated.
if [ ! -f "$REPO_DIR/local-data/dns-blocklist.txt.xz" ]; then
  echo "❌ $REPO_DIR/local-data/dns-blocklist.txt.xz is missing." >&2
  echo "   The flake cannot be evaluated without it. Run" >&2
  echo "   scripts/refresh-dns-blocklist.py, then retry (see local-data/README.md)." >&2
  exit 1
fi

if [ -n "$SUDO" ]; then
  read -r -a SUDO_CMD <<< "$SUDO"
else
  SUDO_CMD=()
fi

# Elevate exactly once for the whole run and work as root afterwards.
#
# Do NOT go back to prefixing individual commands with sudo. This script used
# to call `sudo readlink` once per PID in /proc (~600 invocations). sudo-rs
# (enabled in system/config/security.nix) caches credentials in a session
# record scoped to the tty session or - when no tty is attached - to the
# *parent PID*. A sudo started from `"$( ... )"` gets a fresh parent process
# every iteration, so the record never matches and every call asks for the
# password again. The prompt is written to /dev/tty (so `2>/dev/null` cannot
# suppress it) while sudo's error output is discarded, which makes it look like
# an endless password loop that never reports a wrong password.
if [ "$(id -u)" -ne 0 ] && [ "${#SUDO_CMD[@]}" -gt 0 ]; then
  echo "🔑 Root privileges are required; authenticating once ..."
  exec "${SUDO_CMD[@]}" -- env SUDO= DEST_DIR="$DEST_DIR" bash "$SCRIPT_FILE" "$@"
fi

if [ "$(id -u)" -ne 0 ]; then
  # SUDO= : unprivileged self-test; only sane against a throwaway DEST_DIR.
  case "$DEST_DIR" in
    /etc/nixos)
      echo "❌ Not root and SUDO is empty: refusing to touch $DEST_DIR unprivileged." >&2
      echo "   Run without SUDO=, or set DEST_DIR to a throwaway directory." >&2
      exit 1
      ;;
  esac
  echo "ℹ️  Running unprivileged (SUDO=) against $DEST_DIR." >&2
  OWN=()
else
  # install -o/-g needs real privileges.
  OWN=(-o root -g root)
fi

# Never work from inside the destination: a process whose cwd is inside it is
# the usual cause of "Device or resource busy" during cleanup.
cd /

echo "🧹 Cleaning $DEST_DIR ..."

if mountpoint -q "$DEST_DIR" 2>/dev/null; then
  echo "ℹ️  $DEST_DIR is a mountpoint (impermanence bind mount); replacing its contents only, the mount itself is left intact." >&2
fi

# Warn (do not fail) about PIDs whose cwd is inside DEST_DIR. Running as root
# already, so this is a plain readlink per PID - no sudo, no password prompts.
busy=""
for p in /proc/[0-9]*/cwd; do
  pid="${p#/proc/}"; pid="${pid%/cwd}"
  tgt="$(readlink "$p" 2>/dev/null)" || continue
  case "$tgt" in
    "$DEST_DIR"|"$DEST_DIR"/*) busy="$busy $pid" ;;
  esac
done
if [ -n "$busy" ]; then
  echo "⚠️  PIDs with cwd inside $DEST_DIR:$busy  (cd out of it if cleanup complains)"
fi

if [ ! -d "$DEST_DIR" ]; then
  install -d -m 700 "$DEST_DIR"
else
  # Replace the contents only - `-mindepth 1` never touches DEST_DIR itself, so
  # a mountpoint stays mounted. Anything that survives the delete is reported
  # and left to `rsync --delete` below.
  if ! find "$DEST_DIR" -mindepth 1 -delete 2>/dev/null; then
    left="$(find "$DEST_DIR" -mindepth 1 2>/dev/null || true)"
    if [ -n "$left" ]; then
      echo "⚠️  Some entries in $DEST_DIR could not be deleted (rsync --delete will handle them):" >&2
      printf '%s\n' "$left" >&2
    fi
  fi
  chmod 700 "$DEST_DIR"
fi

echo "📂 Syncing config to $DEST_DIR ..."

# Junk that must never end up in /etc/nixos. *.md = GitHub-only README/docs.
excludes=(
  --exclude '.git/' --exclude '.direnv/'
  --exclude 'result' --exclude 'result-*'
  --exclude '*.swp' --exclude '*~' --exclude '*.bak' --exclude '.DS_Store'
  # Hermes skills are *.md and the build needs them: re-include before the *.md
  # cut. rsync uses the first matching rule, so these must precede --exclude.
  --include 'programs/hermes-skills/'
  --include 'programs/hermes-skills/**'
  --exclude '*.md'
)

# --delete: stale files inside the synced trees are dropped even if the cleanup
# above could not empty the directory completely.
# home/ : keep local wallpapers; README placeholders are dropped by *.md.
rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/home/" "$DEST_DIR/home/"
# overlays/
rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/overlays/" "$DEST_DIR/overlays/"
# local-data/ : generated build inputs that are git-ignored (see
# local-data/README.md). `system/config/dns-blocklist.nix` reads
# dns-blocklist.txt.xz from here, so /etc/nixos needs it too. Keep the README
# (the module's error message points at it), hence no *.md cut for this tree.
rsync -a --delete --no-owner --no-group \
  --exclude 'result' --exclude 'result-*' \
  "$REPO_DIR/local-data/" "$DEST_DIR/local-data/"
# system/ : must keep secrets/secrets.yaml.
rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/system/" "$DEST_DIR/system/"
# tests/ : flake checks import the test modules, scripts and fixtures directly.
rsync -a --delete --no-owner --no-group "${excludes[@]}" \
  "$REPO_DIR/tests/" "$DEST_DIR/tests/"

for f in flake.nix flake.lock .sops.yaml; do
  install -m 600 "${OWN[@]}" "$REPO_DIR/$f" "$DEST_DIR/$f"
done

echo "🔒 Setting secure permissions (dirs 700 / files 600) ..."
# Best effort: an entry we cannot descend into (leftover from the cleanup
# above) must not turn an otherwise completed push into a failure.
if ! find "$DEST_DIR" -type d -exec chmod 700 {} + 2>/dev/null; then
  echo "⚠️  Some directories under $DEST_DIR could not be tightened to 700." >&2
fi
if ! find "$DEST_DIR" -type f -exec chmod 600 {} + 2>/dev/null; then
  echo "⚠️  Some files under $DEST_DIR could not be tightened to 600." >&2
fi

echo "✅ Done! Config pushed to $DEST_DIR."
