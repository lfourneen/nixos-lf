#!/usr/bin/env bash

# Export into a private workspace, preserving modes and backing up on --force.

set -euo pipefail
umask 077

die() {
  echo "❌ $*" >&2
  exit 1
}

SRC_DIR="${SRC_DIR:-/etc/nixos}"
if [ -z "${DEST_BASE:-}" ]; then
  [ -n "${HOME:-}" ] || die "HOME is unset; set DEST_BASE"
  DEST_BASE="$HOME/Projects/Nix"
fi
DEST_DIR="${DEST_DIR:-}"
DEFAULT_DEST=0
SUDO="${SUDO-sudo}"
FORCE=0

usage() {
  cat <<'EOF'
Usage: scripts/sync-to-git.sh [--force]

  --force  move an existing non-empty DEST_DIR to a timestamped backup and
           copy into a fresh directory

Environment: SRC_DIR, DEST_BASE, DEST_DIR, SUDO
EOF
}

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $arg" ;;
  esac
done

if [ -n "$SUDO" ]; then
  read -r -a SUDO_CMD <<< "$SUDO"
else
  SUDO_CMD=()
fi
if [ "${#SUDO_CMD[@]}" -gt 0 ] && [ "$(id -u)" -ne 0 ]; then
  RUN=("${SUDO_CMD[@]}")
else
  RUN=()
fi

[ -d "$SRC_DIR" ] || die "source directory does not exist: $SRC_DIR"
command -v rsync >/dev/null 2>&1 || die "rsync is required"

SRC_REAL="$(realpath -e -- "$SRC_DIR")"
if [ -z "$DEST_DIR" ]; then
  [ -n "$DEST_BASE" ] || die "DEST_BASE is empty and DEST_DIR is not set"
  STAMP="$(date +%Y%m%d-%H%M%S)-$$"
  DEST_DIR="$DEST_BASE/nixos-$STAMP"
  DEFAULT_DEST=1
fi
[ ! -L "$DEST_DIR" ] || die "destination is a symlink: $DEST_DIR"
DEST_REAL="$(realpath -m -- "$DEST_DIR")"

case "$DEST_REAL" in
  /|/etc|/home|/root)
    die "refusing protected destination: $DEST_REAL" ;;
esac
if [ "$(dirname -- "$DEST_REAL")" = "/" ]; then
  die "refusing a top-level system directory as destination: $DEST_REAL"
fi
if [ "$DEST_REAL" = "$SRC_REAL" ] || [[ "$DEST_REAL" == "$SRC_REAL"/* ]] \
   || [[ "$SRC_REAL" == "$DEST_REAL"/* ]]; then
  die "destination overlaps the source ($SRC_REAL): $DEST_REAL"
fi

BACKUP_DIR=""
if [ -e "$DEST_REAL" ]; then
  [ -d "$DEST_REAL" ] || die "destination is not a directory: $DEST_REAL"
  if [ -n "$(find "$DEST_REAL" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    if [ "$FORCE" -ne 1 ]; then
      die "destination is non-empty: $DEST_REAL; use --force (moves it to a backup) or choose another DEST_DIR"
    fi
    BACKUP_DIR="${DEST_REAL%/}.backup-$(date +%Y%m%d-%H%M%S)-$$"
    [ ! -e "$BACKUP_DIR" ] || die "backup path already exists: $BACKUP_DIR"
    mv -- "$DEST_REAL" "$BACKUP_DIR"
    echo "ℹ️  Moved existing destination to $BACKUP_DIR" >&2
  fi
else
  install -d -m 700 -- "$DEST_REAL"
fi

echo "📂 Copying $SRC_REAL to $DEST_REAL ..."
if ! "${RUN[@]}" rsync -a -- "$SRC_REAL/" "$DEST_REAL/"; then
  if [ -n "$BACKUP_DIR" ]; then
    echo "❌ Copy failed. The previous destination is preserved at $BACKUP_DIR" >&2
  else
    echo "❌ Copy failed: $DEST_REAL" >&2
  fi
  exit 1
fi

TARGET_UID="${SUDO_UID:-$(id -u)}"
TARGET_GID="${SUDO_GID:-$(id -g)}"
# The copy may be root-owned when rsync ran through the privilege prefix, and
# an unprivileged find cannot even traverse those directories.
if [ "${#RUN[@]}" -gt 0 ]; then
  needs_chown="$("${RUN[@]}" find "$DEST_REAL" \( ! -uid "$TARGET_UID" -o ! -gid "$TARGET_GID" \) -print -quit 2>/dev/null)"
else
  needs_chown="$(find "$DEST_REAL" \( ! -uid "$TARGET_UID" -o ! -gid "$TARGET_GID" \) -print -quit 2>/dev/null)"
fi
if [ -n "$needs_chown" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    chown -R -- "$TARGET_UID:$TARGET_GID" "$DEST_REAL"
  elif [ "${#SUDO_CMD[@]}" -gt 0 ]; then
    "${SUDO_CMD[@]}" chown -R -- "$TARGET_UID:$TARGET_GID" "$DEST_REAL"
  else
    die "destination is not owned by $TARGET_UID and no privilege prefix is available"
  fi
fi

if [ "$DEFAULT_DEST" -eq 1 ]; then
  chmod 700 -- "$DEST_REAL"
fi
mode="$(stat -c %a -- "$DEST_REAL")"
echo "✅ Copied with modes preserved (destination mode $mode) to $DEST_REAL."
if [ -n "$BACKUP_DIR" ]; then
  echo "ℹ️  Previous destination: $BACKUP_DIR"
  echo "ℹ️  Restore: move $DEST_REAL aside, then move $BACKUP_DIR back to $DEST_REAL."
fi
