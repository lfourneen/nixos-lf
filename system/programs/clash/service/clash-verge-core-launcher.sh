# Root launcher for the packaged mihomo core. Installed as bin/verge-mihomo;
# the package's real binary is bin/.verge-mihomo-wrapped.
#
# The helper pins the requested core path to a root-owned, non-writable
# location. This launcher makes the packaged path the only useful one:
#   * read-only modes pass through (the GUI uses them for diagnostics);
#   * a real start requires EUID 0, an explicit clash-on intent, a configuration
#     owned by the desktop user under the GUI data directory, a successful parse
#     as the unprivileged core user, and a digest authorized by the configured
#     refresh policy or this clash-on session;
#   * the core then runs as @clashCore@ with only CAP_NET_ADMIN and CAP_NET_RAW,
#     never as root and never with the desktop user's identity.

set -eu
umask 077

case "$0" in
  */*) self=$(readlink -f "$0") ;;
  *) self=$(command -v -- "$0") || { echo "verge-mihomo: cannot resolve the launcher path" >&2; exit 78; }
     self=$(readlink -f "$self") ;;
esac
real="$(dirname "$self")/.verge-mihomo-wrapped"
intent_file=/run/proxy-mode/intent
core_dir=/run/clash-verge-rev/core
core_sock="$core_dir/mihomo.sock"
data_dir=/var/lib/clash-core
front_dir=/run/user/@desktopUid@/clash-verge-rev
front_sock="$front_dir/verge-mihomo.sock"
forwarder_pid=/run/clash-verge-rev/forwarder.pid
setpriv_args="--reuid=@clashCore@ --regid=@clashCore@ --init-groups --inh-caps=+net_admin,+net_raw --ambient-caps=+net_admin,+net_raw --bounding-set=-all,+net_admin,+net_raw --no-new-privs"

die() {
  echo "verge-mihomo: $*" >&2
  exit 78
}

config=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -v|--version|-h|--help)
      exec "$real" "$@"
      ;;
    -t|--test)
      # Parse-only, no listeners and no TUN; the GUI and operators use it.
      exec "$real" "$@"
      ;;
    -d)
      shift
      [ "$#" -gt 0 ] || die "-d requires a value"
      ;;
    -f)
      shift
      [ "$#" -gt 0 ] || die "-f requires a value"
      config=$1
      ;;
    -ext-ctl-unix)
      shift
      [ "$#" -gt 0 ] || die "-ext-ctl-unix requires a value"
      ;;
    *)
      die "unexpected core argument: $1"
      ;;
  esac
  shift
done

[ "$(id -u)" = 0 ] || die "the core starts only through the root helper"
[ -n "$config" ] || die "-f is required"
[ -f "$intent_file" ] || die "start Clash with clash-on; the core is not authorized in Mode A"
[ "$(cat "$intent_file" 2>/dev/null || true)" = proxy ] \
  || die "start Clash with clash-on; the core is not authorized in Mode A"

# Runtime snapshots survive helper restarts; stage enforces the refresh policy.
# The service user can write caches, but cannot replace configuration/geodata.
# Match RuntimeDirectory's group: changing it to root would make systemd
# recursively re-own the preserved snapshots on the next helper restart.
install -d -m 0755 -o root -g @controlGroup@ /run/clash-verge-rev
install -d -m 0755 -o root -g root /run/proxy-mode
install -d -m 0750 -o @clashCore@ -g @clashCore@ "$core_dir" "$data_dir"
setpriv --reuid=@clashCore@ --regid=@clashCore@ --clear-groups \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
  sh -c 'umask 077; mkdir -p "$1/proxies"; touch "$1/cache.db"' sh "$data_dir"
@python@ @stageTool@ stage @stageSettings@ "$real" "$config"

if [ -d "/run/user/@desktopUid@" ]; then
  # Operate on a directory descriptor, never chmod/chown a desktop-supplied
  # symlink. The forwarder itself runs without root or network capabilities.
  @python@ - "$front_dir" <<'PYFRONT'
import os
import sys
path = sys.argv[1]
os.makedirs(path, mode=0o700, exist_ok=True)
fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    if os.fstat(fd).st_uid not in (0, @desktopUid@):
        raise ValueError("unexpected GUI socket directory owner")
    os.fchown(fd, @desktopUid@, -1)
    os.fchmod(fd, 0o700)
finally:
    os.close(fd)
PYFRONT
  if [ -f "$forwarder_pid" ]; then
    old=$(cat "$forwarder_pid" 2>/dev/null || true)
    if [ -n "$old" ] && [ -r "/proc/$old/cmdline" ] \
      && tr '\0' ' ' <"/proc/$old/cmdline" | grep -q "UNIX-LISTEN:$front_sock"; then
      kill "$old" 2>/dev/null || true
      sleep 0.2
    fi
    rm -f "$forwarder_pid"
  fi
  rm -f "$front_sock"
  setpriv --reuid=@desktopUid@ --regid=@clashCore@ --clear-groups \
    --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
    socat "UNIX-LISTEN:$front_sock,mode=0600,fork,unlink-early" \
    "UNIX-CONNECT:$core_sock" >>/run/clash-verge-rev/forwarder.log 2>&1 &
  echo $! >"$forwarder_pid"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -S "$front_sock" ] && break
    sleep 0.1
  done
else
  echo "verge-mihomo: /run/user/@desktopUid@ is absent; the GUI control socket is unavailable" >&2
fi

exec setpriv $setpriv_args "$real" -d /run/clash-verge-rev/effective -f /run/clash-verge-rev/effective/clash-verge.yaml -ext-ctl-unix "$core_sock"
