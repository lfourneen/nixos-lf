#!/usr/bin/env bash
# Preview or apply ownership changes to stopped libvirt images and QEMU state.

set -euo pipefail

readonly default_root=/var/lib/libvirt
readonly owner="${LIBVIRT_QEMU_MIGRATE_OWNER:-qemu-libvirtd}"
readonly group="${LIBVIRT_QEMU_MIGRATE_GROUP:-qemu-libvirtd}"
root="${LIBVIRT_QEMU_MIGRATE_ROOT:-$default_root}"

usage() {
  cat <<'EOF'
Usage: libvirt-qemu-migrate.sh [--apply]

Preview the ownership change by default. --apply performs it and must run as
root. Only <root>/images and <root>/qemu are changed.

Environment overrides (tests only):
  LIBVIRT_QEMU_MIGRATE_ROOT   target root, default /var/lib/libvirt
  LIBVIRT_QEMU_MIGRATE_OWNER  owner, default qemu-libvirtd
  LIBVIRT_QEMU_MIGRATE_GROUP  group, default qemu-libvirtd
EOF
}

die() {
  printf 'refusing: %s\n' "$*" >&2
  exit 1
}

apply=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$owner" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || die "invalid owner name: $owner"
[[ "$group" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || die "invalid group name: $group"

[ -n "$root" ] || die "empty target root"
case "$root" in
  /*) ;;
  *) die "target root must be absolute: $root" ;;
esac
[ -e "$root" ] || die "target root does not exist: $root"
[ -d "$root" ] || die "target root is not a directory: $root"

root_norm="$(realpath -s -m -- "$root")"
root_real="$(realpath -e -- "$root")"
[ "$root_real" = "$root_norm" ] || die "target root contains a symlink: $root"

case "$root_real" in
  / | /var | /var/lib | /var/log | /etc | /usr | /nix | /home | /root | /run | /tmp | /var/tmp | /boot | /dev | /proc | /sys | /srv | /opt)
    die "unsafe target root: $root_real"
    ;;
esac
for unsafe in /etc /usr /nix /boot /dev /proc /sys /run /root /home /srv /opt; do
  case "$root_real/" in
    "$unsafe"/*) die "unsafe target root: $root_real" ;;
  esac
done

getent passwd "$owner" >/dev/null || die "owner does not exist: $owner"
getent group "$group" >/dev/null || die "group does not exist: $group"

targets=()
for name in images qemu; do
  path="$root_real/$name"
  [ -e "$path" ] || continue
  if [ -L "$path" ]; then
    die "target is a symlink: $path"
  fi
  [ -d "$path" ] || die "target is not a directory: $path"
  resolved="$(realpath -e -- "$path")"
  [ "$resolved" = "$path" ] || die "target contains a symlink: $path"
  case "$resolved/" in
    "$root_real"/*) ;;
    *) die "target escapes root: $path" ;;
  esac
  targets+=("$path")
done
[ "${#targets[@]}" -gt 0 ] || die "no migration targets under $root_real"

while IFS= read -r -d '' link; do
  die "symlink inside target tree: $link"
done < <(find -P "${targets[@]}" -xdev -type l -print0)

owner_spec="$owner:$group"
changes=()
while IFS= read -r -d '' path; do
  [ "$(stat -c '%U:%G' -- "$path")" = "$owner_spec" ] || changes+=("$path")
done < <(find -P "${targets[@]}" -xdev -print0)

if [ "${#changes[@]}" -eq 0 ]; then
  printf 'already migrated: %s already belongs to %s\n' "${targets[*]}" "$owner_spec"
  exit 0
fi

printf 'target root: %s\n' "$root_real"
printf 'owner: %s\n' "$owner_spec"
printf 'paths: %s\n' "${targets[*]}"
printf 'entries needing chown: %s\n' "${#changes[@]}"

print_rollback() {
  printf 'rollback (if the previous owner was root:root): sudo chown -R root:root --'
  printf ' %q' "${targets[@]}"
  printf '\n'
}

if [ "$apply" -eq 0 ]; then
  printf 'DRY-RUN: no ownership changed. Use --apply to change these entries:\n'
  shown=0
  for path in "${changes[@]}"; do
    [ "$shown" -lt 20 ] || break
    printf '  %q\n' "$path"
    shown=$((shown + 1))
  done
  if [ "${#changes[@]}" -gt "$shown" ]; then
    printf '  ... and %s more\n' "$(( ${#changes[@]} - shown ))"
  fi
  print_rollback
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "--apply requires root"

print_rollback
printf '%s\0' "${changes[@]}" | xargs -0 -r -n 128 chown -- "$owner_spec"

for path in "${changes[@]}"; do
  [ "$(stat -c '%U:%G' -- "$path")" = "$owner_spec" ] || die "ownership verification failed: $path"
done
printf 'changed ownership of %s entries to %s\n' "${#changes[@]}" "$owner_spec"
