#!/usr/bin/env bash

# Stage, validate and back up the configuration before a locked installation.
# Failed installations restore the complete previous tree. Readers use
# scripts/rebuild-deployed.sh to share the lock during evaluation and rebuild.
# The destination mount is preserved; the tree update is not atomic.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_FILE="$SCRIPT_DIR/$(basename -- "${BASH_SOURCE[0]}")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="${DEST_DIR:-/etc/nixos}"
SUDO="${SUDO-sudo}"

MARKER_NAME=".nixos-lf-deploy"
MARKER_CONTENT="version=1"
ADOPT_EXISTING=0
TEST_MODE=${DEPLOY_TEST_MODE:-0}
SKIP_EVAL=0

die() {
  echo "❌ $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: scripts/push-to-dir.sh [--adopt-existing] [--skip-eval] [--test-mode]

  --adopt-existing  adopt the existing /etc/nixos layout once.
  --test-mode       allow disposable destinations and adoption for tests.
  --skip-eval       do not run the staged `nix eval`; use for an offline copy
                    of a trusted commit. The rebuild still evaluates it.

Environment: DEST_DIR, SUDO, DEPLOY_STATE_DIR, DEPLOY_LOCK_FILE,
             DEPLOY_SKIP_EVAL
EOF
}

for arg in "$@"; do
  case "$arg" in
    --adopt-existing) ADOPT_EXISTING=1 ;;
    --test-mode) TEST_MODE=1 ;;
    --skip-eval) SKIP_EVAL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $arg" ;;
  esac
done

# Required build inputs. Check before elevating and before touching DEST_DIR so
# an incomplete checkout cannot replace a working deployment.
required_inputs=(local-data/dns-blocklist.txt.xz flake.nix flake.lock .sops.yaml)
for rel in "${required_inputs[@]}"; do
  if [ ! -f "$REPO_DIR/$rel" ]; then
    if [ "$rel" = "local-data/dns-blocklist.txt.xz" ]; then
      echo "❌ $REPO_DIR/$rel is missing." >&2
      echo "   The flake cannot be evaluated without it. Run" >&2
      echo "   scripts/refresh-dns-blocklist.py, then retry (see local-data/README.md)." >&2
    else
      echo "❌ required build input $REPO_DIR/$rel is missing." >&2
    fi
    exit 1
  fi
done

for tool in rsync flock install realpath sha256sum find xargs comm cmp mktemp du df stat findmnt sync python3; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool is missing: $tool"
done

if [ -n "$SUDO" ]; then
  read -r -a SUDO_CMD <<< "$SUDO"
else
  SUDO_CMD=()
fi

if [ "$SKIP_EVAL" -eq 1 ]; then
  export DEPLOY_SKIP_EVAL=1
fi

# Warm the store with the locked flake inputs before elevating. Flake input
# fetching ignores `nix eval --offline`, so when an input is missing from the
# store (for example after a garbage collection) the root-side staged eval
# would download it mid-deploy, and a slow or blocked GitHub fetch aborts the
# run. Fetching them here uses the invoking user's Nix cache and proxy
# configuration, so the staged eval resolves every input from the store.
if [ "${DEPLOY_SKIP_EVAL:-0}" != "1" ]; then
  command -v nix >/dev/null 2>&1 \
    || die "nix is required for the staged evaluation; set DEPLOY_SKIP_EVAL=1 only for tests"
  echo "📦 Fetching locked flake inputs into the store ..."
  # GitHub intermittently truncates a tarball or drops the redirect mid-fetch;
  # every attempt resumes where the previous one stopped, so a few retries
  # converge instead of aborting a deployment over one bad transfer.
  fetched=0
  for attempt in 1 2 3; do
    if nix flake archive --no-write-lock-file "$REPO_DIR" >/dev/null; then
      fetched=1
      break
    fi
    [ "$attempt" -eq 3 ] || echo "⚠️  flake input fetch failed (attempt $attempt/3), retrying ..." >&2
  done
  [ "$fetched" -eq 1 ] \
    || die "could not fetch the locked flake inputs; check GitHub access and the proxy, then retry"
fi

# Elevate once for the whole run. Per-command sudo prompts do not work reliably
# with sudo-rs and a non-tty parent process.
if [ "$(id -u)" -ne 0 ] && [ "${#SUDO_CMD[@]}" -gt 0 ]; then
  echo "🔑 Root privileges are required; authenticating once ..."
  env_args=(SUDO= "DEST_DIR=$DEST_DIR" "DEPLOY_TEST_MODE=$TEST_MODE")
  [ -z "${DEPLOY_STATE_DIR:-}" ] || env_args+=("DEPLOY_STATE_DIR=$DEPLOY_STATE_DIR")
  [ -z "${DEPLOY_LOCK_FILE:-}" ] || env_args+=("DEPLOY_LOCK_FILE=$DEPLOY_LOCK_FILE")
  [ -z "${DEPLOY_SKIP_EVAL:-}" ] || env_args+=("DEPLOY_SKIP_EVAL=$DEPLOY_SKIP_EVAL")
  exec "${SUDO_CMD[@]}" -- env "${env_args[@]}" bash "$SCRIPT_FILE" "$@"
fi

if [ "$(id -u)" -ne 0 ]; then
  case "$(realpath -m -- "$DEST_DIR")" in
    /etc/nixos)
      echo "❌ Not root and SUDO is empty: refusing to touch $DEST_DIR unprivileged." >&2
      echo "   Run without SUDO=, or set DEST_DIR to a throwaway directory." >&2
      exit 1
      ;;
  esac
  OWN=()
  echo "ℹ️  Running unprivileged (SUDO=) against $DEST_DIR." >&2
else
  OWN=(-o root -g root)
fi

if [ -n "${DEPLOY_STATE_DIR:-}" ]; then
  STATE_DIR="$DEPLOY_STATE_DIR"
elif [ "$(id -u)" -eq 0 ]; then
  STATE_DIR=/var/lib/nixos-lf-deploy
else
  STATE_DIR="${TMPDIR:-/tmp}/nixos-lf-deploy-$(id -u)"
fi
[ "$(realpath -m -- "$STATE_DIR")" = "$(realpath -ms -- "$STATE_DIR")" ] \
  || die "state path contains a symlink: $STATE_DIR"
install -d -m 700 -- "$STATE_DIR"
[ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ] || die "state path is not a directory: $STATE_DIR"
[ -O "$STATE_DIR" ] || die "state directory is not owned by the current user: $STATE_DIR"
chmod 700 -- "$STATE_DIR"

if [ -n "${DEPLOY_LOCK_FILE:-}" ]; then
  LOCK_FILE="$DEPLOY_LOCK_FILE"
elif [ "$(id -u)" -eq 0 ]; then
  LOCK_FILE=/run/lock/nixos-lf-deploy.lock
else
  LOCK_FILE="$STATE_DIR/deploy.lock"
fi
LOCK_DIR="$(dirname -- "$LOCK_FILE")"
[ -d "$LOCK_DIR" ] || install -d -m 700 -- "$LOCK_DIR"
[ ! -L "$LOCK_FILE" ] || die "lock file is a symlink: $LOCK_FILE"
exec 9>"$LOCK_FILE" || die "cannot open lock file: $LOCK_FILE"
if ! flock -n 9; then
  die "another deployment holds the lock ($LOCK_FILE); retry after it finishes"
fi

# Validate the destination.
case "$DEST_DIR" in
  /*) ;;
  *) die "DEST_DIR must be an absolute path: $DEST_DIR" ;;
esac

case "$DEST_DIR" in
  /|/etc|/home|/root)
    die "refusing protected DEST_DIR: $DEST_DIR" ;;
esac
if [ "$(dirname -- "$DEST_DIR")" = "/" ]; then
  die "refusing a top-level system directory as DEST_DIR: $DEST_DIR"
fi

# Production ancestors must remain root owned and unwritable to other users.
symlink_prefix=""
IFS=/ read -r -a dest_parts <<< "$DEST_DIR"
for part in "${dest_parts[@]}"; do
  [ -n "$part" ] || continue
  symlink_prefix="$symlink_prefix/$part"
  [ ! -L "$symlink_prefix" ] || die "DEST_DIR path component is a symlink: $symlink_prefix"
done

DEST_REAL="$(realpath -m -- "$DEST_DIR")"
case "$DEST_REAL" in
  /|/etc|/home|/root)
    die "refusing protected DEST_DIR after normalization: $DEST_REAL" ;;
esac
if [ "$(dirname -- "$DEST_REAL")" = "/" ]; then
  die "refusing a top-level system directory as DEST_DIR after normalization: $DEST_REAL"
fi
REPO_REAL="$(realpath -e -- "$REPO_DIR")"
if [ "$DEST_REAL" = "$REPO_REAL" ] || [[ "$DEST_REAL" == "$REPO_REAL"/* ]] \
   || [[ "$REPO_REAL" == "$DEST_REAL"/* ]]; then
  die "DEST_DIR overlaps the repository ($REPO_REAL): $DEST_REAL"
fi

STATE_REAL="$(realpath -m -- "$STATE_DIR")"
if [ "$DEST_REAL" = "$STATE_REAL" ] || [[ "$DEST_REAL" == "$STATE_REAL"/* ]] \
   || [[ "$STATE_REAL" == "$DEST_REAL"/* ]]; then
  die "DEST_DIR overlaps the deployment state ($STATE_REAL): $DEST_REAL"
fi

if [ "$DEST_REAL" != /etc/nixos ] && [ "$TEST_MODE" != 1 ]; then
  die "DEST_DIR must be /etc/nixos; use --test-mode for disposable destinations"
fi
if [ "$TEST_MODE" != 1 ]; then
  prefix=""
  IFS=/ read -r -a components <<< "$DEST_REAL"
  for part in "${components[@]}"; do
    [ -n "$part" ] || continue
    prefix="$prefix/$part"
    [ -e "$prefix" ] || continue
    [ "$(stat -c %u -- "$prefix")" = 0 ] || die "untrusted path owner: $prefix"
    mode=$(stat -c %a -- "$prefix")
    [ $((8#$mode & 0022)) -eq 0 ] || die "writable path component: $prefix"
  done
fi
if command -v findmnt >/dev/null; then
  mount_targets=$(findmnt -rn -o TARGET) || die "cannot inspect nested mounts"
  while IFS= read -r mounted; do
    case "$mounted" in "$DEST_REAL"/*) die "nested mount under DEST_DIR: $mounted" ;; esac
  done <<< "$mount_targets"
fi

for recovery in "$STATE_DIR"/backup-*/recovery.log; do
  [ -f "$recovery" ] || continue
  recorded_dest=$(sed -n 's/^destination=//p' "$recovery")
  recorded_status=$(awk -F= '/^status=/{s=$2} END {print s}' "$recovery")
  if [ "$recorded_dest" = "$DEST_REAL" ] && [[ "$recorded_status" = in-progress || "$recorded_status" = recovery-required ]]; then
    rsync -a --delete-before --inplace -- "$(dirname -- "$recovery")/files/" "$DEST_REAL/" \
      || die "could not recover interrupted installation: $recovery"
    printf 'status=rolled-back\n' >> "$recovery"
  fi
done

DEST_EXISTS=0
if [ -e "$DEST_REAL" ]; then
  [ -d "$DEST_REAL" ] || die "DEST_DIR is not a directory: $DEST_REAL"
  DEST_EXISTS=1
  if [ -n "$(find "$DEST_REAL" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    if [ -L "$DEST_REAL/$MARKER_NAME" ]; then
      die "deployment marker is a symlink: $DEST_REAL/$MARKER_NAME"
    elif [ -f "$DEST_REAL/$MARKER_NAME" ]; then
      marker_seen="$(cat -- "$DEST_REAL/$MARKER_NAME")"
      [ "$marker_seen" = "$MARKER_CONTENT" ] \
        || die "unknown deployment marker in $DEST_REAL (expected '$MARKER_CONTENT')"
    elif [ "$ADOPT_EXISTING" -ne 1 ]; then
      die "DEST_DIR is non-empty and has no $MARKER_NAME marker: $DEST_REAL; pass --adopt-existing to replace it"
    fi
  fi
fi

if mountpoint -q "$DEST_REAL" 2>/dev/null; then
  echo "ℹ️  $DEST_REAL is a mountpoint; replacing its contents only, the mount itself stays intact." >&2
fi

# Warn about processes whose cwd is inside DEST_DIR, a common source of EBUSY.
busy=""
for p in /proc/[0-9]*/cwd; do
  pid="${p#/proc/}"; pid="${pid%/cwd}"
  tgt="$(readlink "$p" 2>/dev/null)" || continue
  case "$tgt" in
    "$DEST_REAL"|"$DEST_REAL"/*) busy="$busy $pid" ;;
  esac
done
if [ -n "$busy" ]; then
  echo "⚠️  PIDs with cwd inside $DEST_REAL:$busy" >&2
fi

# Space needed for the staging copy (the payload trees only).
need_kib=0
for tree in home overlays system tests scripts maintenance local-data; do
  if [ -d "$REPO_DIR/$tree" ]; then
    tree_kib="$(du -sk -- "$REPO_DIR/$tree" | cut -f1)"
    need_kib=$((need_kib + tree_kib))
  fi
done
avail_kib="$(df -Pk -- "$STATE_DIR" | awk 'NR == 2 { print $4 }')"
if [ -n "$avail_kib" ] && [ "$avail_kib" -lt $((need_kib + 16384)) ]; then
  die "not enough free space under $STATE_DIR for the staging copy (${need_kib} KiB payload, ${avail_kib} KiB free)"
fi

# Keep complete skill trees and discover literal path resources in local Nix.
resource_includes=(
  '/home/config/agent-skills/'
  '/home/config/agent-skills/**'
  '/system/programs/hermes/hermes-skills/'
  '/system/programs/hermes/hermes-skills/**'
)
if [ -f "$SCRIPT_DIR/deployment-resources.py" ]; then
  discovered_resources=$(python3 "$SCRIPT_DIR/deployment-resources.py" "$REPO_DIR") \
    || die "could not derive deployment resources"
  while IFS= read -r resource; do
    [ -z "$resource" ] || resource_includes+=("/$resource")
  done <<< "$discovered_resources"
fi

base_excludes=(
  --exclude '.git/' --exclude '.direnv/'
  --exclude 'result' --exclude 'result-*'
  --exclude '*.swp' --exclude '*~' --exclude '*.bak' --exclude '.DS_Store'
  --exclude '__pycache__/' --exclude '*.pyc'
)

stage_tree() {
  local tree="$1" dest="$2" pattern includes=()
  for pattern in "${resource_includes[@]}"; do
    case "$pattern" in
      "/$tree"|"/$tree/"*) includes+=(--include "${pattern#/$tree}") ;;
    esac
  done
  rsync -a --no-owner --no-group "${base_excludes[@]}" "${includes[@]}" \
    --exclude '*.md' "$REPO_DIR/$tree/" "$dest/$tree/"
}

copy_payload() {
  local dest="$1" tree
  for tree in home overlays system tests scripts maintenance; do
    [ -d "$REPO_DIR/$tree" ] || continue
    stage_tree "$tree" "$dest"
  done
  # local-data/README.md is kept: the DNS module's error message points at it.
  rsync -a --no-owner --no-group \
    --exclude 'result' --exclude 'result-*' \
    "$REPO_DIR/local-data/" "$dest/local-data/"
  for f in flake.nix flake.lock .sops.yaml; do
    install -m 600 "${OWN[@]}" -- "$REPO_DIR/$f" "$dest/$f"
  done
  printf '%s\n' "$MARKER_CONTENT" > "$dest/$MARKER_NAME"
  chmod 600 -- "$dest/$MARKER_NAME"
}

manifest_of() {
  ( cd -- "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum )
}

paths_of() {
  ( cd -- "$1" && find . -type f -print | LC_ALL=C sort )
}

# Build and verify the staged tree.
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
STAGING="$(mktemp -d "$STATE_DIR/staging.XXXXXX")"
chmod 700 -- "$STAGING"
trap 'status=$?; if [ "$status" -eq 0 ]; then case "$STAGING" in "$STATE_DIR"/staging.*) rm -rf -- "$STAGING" ;; esac; else echo "ℹ️  Staging copy preserved: $STAGING" >&2; fi' EXIT

echo "🧱 Building staged payload in $STAGING ..."
copy_payload "$STAGING"

if [ -n "$(find "$STAGING" -type l -print -quit)" ]; then
  die "staged payload contains a symlink; refusing to deploy it"
fi
find "$STAGING" -type d -exec chmod 700 {} +
find "$STAGING" -type f -exec chmod 600 {} +
if [ -n "$(find "$STAGING" \( -type f ! -perm 600 -o -type d ! -perm 700 \) -print -quit)" ]; then
  die "staged payload has unexpected permissions; refusing to deploy it"
fi

STAGED_MANIFEST="$STATE_DIR/manifest-$STAMP.sha256"
manifest_of "$STAGING" > "$STAGED_MANIFEST"
chmod 600 -- "$STAGED_MANIFEST"

if [ "${DEPLOY_SKIP_EVAL:-0}" != "1" ]; then
  command -v nix >/dev/null 2>&1 \
    || die "nix is required for the staged evaluation; set DEPLOY_SKIP_EVAL=1 only for tests"
  echo "🧪 Evaluating the staged flake ..."
  if ! nix eval --offline --no-write-lock-file --raw \
      "$STAGING#nixosConfigurations.lfour.config.system.build.toplevel.drvPath" >/dev/null; then
    die "staged flake failed to evaluate; $DEST_REAL was not modified"
  fi
  # Refuse to install a configuration that would fail its activation because
  # the root-only Age key has not been adopted yet.
  key_file="$(nix eval --offline --no-write-lock-file --raw \
    "$STAGING#nixosConfigurations.lfour.config" \
    --apply 'c: if c.my.secrets.systemAgeKey.enable then c.my.secrets.systemAgeKey.file else ""')" \
    || die "could not read the system Age key settings from the staged flake"
  if [ -n "$key_file" ]; then
    [ -f "$key_file" ] && [ ! -L "$key_file" ] \
      || die "my.secrets.systemAgeKey is enabled but $key_file is missing; run scripts/system-age-key.py --adopt as your desktop user first"
    [ "$(stat -c %u -- "$key_file")" = 0 ] \
      || die "system Age key $key_file must be owned by root"
    case "$(stat -c %a -- "$key_file")" in
      400|600) ;;
      *) die "system Age key $key_file must be mode 400 or 600" ;;
    esac
  fi

  manifest_of "$STAGING" > "$STATE_DIR/manifest-post-eval-$STAMP.sha256"
  if ! cmp -s -- "$STAGED_MANIFEST" "$STATE_DIR/manifest-post-eval-$STAMP.sha256"; then
    die "staged tree changed during evaluation; $DEST_REAL was not modified"
  fi
  if [ -n "$(find "$STAGING" \( -type f ! -perm 600 -o -type d ! -perm 700 \) -print -quit)" ]; then
    die "staged permissions changed during evaluation; $DEST_REAL was not modified"
  fi
else
  echo "⚠️  Staged flake evaluation skipped; deploy only a trusted checkout." >&2
fi

# Back up the old tree before installation.
BACKUP_DIR="$STATE_DIR/backup-$STAMP"
BACKUP_FILES="$BACKUP_DIR/files"
install -d -m 700 -- "$BACKUP_DIR" "$BACKUP_FILES"
RECOVERY_LOG="$BACKUP_DIR/recovery.log"
OLD_MANIFEST="$BACKUP_DIR/manifest-before.sha256"
OLD_PATHS="$BACKUP_DIR/paths-before.txt"
NEW_PATHS="$BACKUP_DIR/paths-after.txt"
ADDED_PATHS="$BACKUP_DIR/added-by-deployment.txt"
REMOVED_PATHS="$BACKUP_DIR/removed-by-deployment.txt"

if [ "$DEST_EXISTS" -eq 1 ]; then
  manifest_of "$DEST_REAL" > "$OLD_MANIFEST"
  paths_of "$DEST_REAL" > "$OLD_PATHS"
else
  : > "$OLD_MANIFEST"
  : > "$OLD_PATHS"
fi
chmod 600 -- "$OLD_MANIFEST" "$OLD_PATHS"

{
  printf 'timestamp=%s\n' "$(date -Is)"
  printf 'destination=%s\n' "$DEST_REAL"
  printf 'staging=%s\n' "$STAGING"
  printf 'staged_manifest=%s\n' "$STAGED_MANIFEST"
  printf 'staged_manifest_sha256=%s\n' "$(sha256sum -- "$STAGED_MANIFEST" | cut -d' ' -f1)"
  printf 'commit=%s\n' "$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  printf 'backup_dir=%s\n' "$BACKUP_DIR"
  printf 'backup_files=%s\n' "$BACKUP_FILES"
  printf '# Restore the complete previous tree while holding the deployment lock:\n'
  printf 'restore_command=rsync -a --delete-before --inplace %q/ %q/\n' "$BACKUP_FILES" "$DEST_REAL"
  printf 'status=prepared\n'
} > "$RECOVERY_LOG"
chmod 600 -- "$RECOVERY_LOG"

# Keep a full private image, including modes and files added by a failed run.
if [ "$DEST_EXISTS" -eq 1 ]; then
  available=$(df -Pk -- "$STATE_DIR" | awk 'NR == 2 { print $4 }')
  previous=$(du -sk -- "$DEST_REAL" | cut -f1)
  [ "$available" -ge $((previous + need_kib + 16384)) ] || die "not enough free space for backup and recovery"
  available=$(df -Pk -- "$DEST_REAL" | awk 'NR == 2 { print $4 }')
  [ "$available" -ge $((need_kib + 16384)) ] || die "not enough destination space"
  rsync -a -- "$DEST_REAL/" "$BACKUP_FILES/"
fi
install -d -m 700 -- "$DEST_REAL"
exec 8<"$DEST_REAL"
DEST_ANCHOR="/proc/$$/fd/8"
printf 'status=in-progress\n' >> "$RECOVERY_LOG"
sync -f "$BACKUP_DIR"
COMMITTING=1
rollback() {
  local status=$?
  trap - EXIT INT TERM HUP
  if [ "$COMMITTING" = 1 ]; then
    if rsync -a --delete-before --inplace -- "$BACKUP_FILES/" "$DEST_ANCHOR/"; then
      sync -f "$DEST_ANCHOR"
      printf 'status=rolled-back\n' >> "$RECOVERY_LOG"
      echo "Installation failed; restored the previous tree." >&2
    else
      printf 'status=recovery-required\n' >> "$RECOVERY_LOG"
      echo "Recovery required: $BACKUP_DIR" >&2
    fi
  fi
  exit "$status"
}
trap rollback EXIT
trap 'exit 143' TERM HUP
trap 'exit 130' INT

echo "📂 Installing staged tree into $DEST_REAL ..."
if ! rsync -a --delete-after --no-owner --no-group \
    "$STAGING/" "$DEST_ANCHOR/"; then
  echo "❌ rsync failed; previously replaced or removed files are in $BACKUP_DIR" >&2
  echo "   Recovery log: $RECOVERY_LOG" >&2
  exit 1
fi

if ! find "$DEST_ANCHOR/" -type d -exec chmod 700 {} +; then
  die "could not set directory modes under $DEST_REAL; backup: $BACKUP_DIR"
fi
if ! find "$DEST_ANCHOR/" -type f -exec chmod 600 {} +; then
  die "could not set file modes under $DEST_REAL; backup: $BACKUP_DIR"
fi
if [ -n "$(find "$DEST_ANCHOR/" \( -type f ! -perm 600 -o -type d ! -perm 700 \) -print -quit)" ]; then
  die "permission check failed under $DEST_REAL; backup: $BACKUP_DIR"
fi

NEW_MANIFEST="$BACKUP_DIR/manifest-after.sha256"
manifest_of "$DEST_ANCHOR" > "$NEW_MANIFEST"
chmod 600 -- "$NEW_MANIFEST"
if ! cmp -s -- "$STAGED_MANIFEST" "$NEW_MANIFEST"; then
  die "deployed tree does not match the staged manifest; backup: $BACKUP_DIR"
fi

paths_of "$DEST_ANCHOR" > "$NEW_PATHS"
LC_ALL=C comm -13 "$OLD_PATHS" "$NEW_PATHS" > "$ADDED_PATHS" || true
LC_ALL=C comm -23 "$OLD_PATHS" "$NEW_PATHS" > "$REMOVED_PATHS" || true
chmod 600 -- "$NEW_PATHS" "$ADDED_PATHS" "$REMOVED_PATHS"
sync -f "$DEST_ANCHOR"

{
  printf 'status=success\n'
  printf 'completed_at=%s\n' "$(date -Is)"
  printf 'deployed_manifest_sha256=%s\n' "$(sha256sum -- "$NEW_MANIFEST" | cut -d' ' -f1)"
} >> "$RECOVERY_LOG"
sync -f "$BACKUP_DIR"

COMMITTING=0
trap - EXIT INT TERM HUP
rm -rf -- "$STAGING"

echo "✅ Done! Config pushed to $DEST_REAL."
echo "ℹ️  Backup of replaced/removed files: $BACKUP_DIR"
echo "ℹ️  Recovery log: $RECOVERY_LOG"
echo "ℹ️  The destination is marked; later runs can omit --adopt-existing."
