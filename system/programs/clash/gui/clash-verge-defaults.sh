# Clash Verge owns these files. Normalize them only while the GUI is stopped.
data="${XDG_DATA_HOME:-$HOME/.local/share}/io.github.clash-verge-rev.clash-verge-rev"
[[ -d "$data" ]] || exit 0
# Serialize concurrent GUI launches: the pgrep check must run under the lock.
exec 9>"$data/.clash-verge-defaults.lock" || exit 1
flock -w 5 9 || exit 0
if python3 - <<'PY'
import os
from pathlib import Path
names = {'clash-verge', '.clash-verge-wrapped', '.clash-verge-launcher'}
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        if entry.stat().st_uid == os.getuid():
            argv0 = os.fsdecode((entry / 'cmdline').read_bytes().split(b'\0', 1)[0])
            if Path(argv0).name in names:
                raise SystemExit(0)
    except OSError:
        continue
raise SystemExit(1)
PY
then
  exit 0
fi

temporary=
trap '[[ -z "$temporary" ]] || rm -f -- "$temporary"' EXIT
publish() {
  local target=$1 expression=$2
  temporary=$(mktemp "${target}.XXXXXX") || return 1
  yq "$expression" "$target" > "$temporary" || return 1
  chmod --reference="$target" "$temporary" || return 1
  mv -fT -- "$temporary" "$target" || return 1
  temporary=
}
regular_map() {
  [[ -f "$1" && ! -L "$1" && -w "$1" ]] && [[ "$(yq 'tag' "$1" 2>/dev/null)" == '!!map' ]]
}
preferences() {
  local target="$data/verge.yaml" kind current
  [[ -e "$target" || -L "$target" ]] || return 0
  regular_map "$target" || return 1
  kind=$(yq '.default_latency_test | tag' "$target" 2>/dev/null) || return 1
  case "$kind" in
    '!!null') ;;
    '!!str')
      current=$(yq '.default_latency_test' "$target") || return 1
      [[ -z "${current//[[:space:]]/}" ]] || return 0
      ;;
    *) return 1 ;;
  esac
  export LF_GUI_DELAY_DEFAULT="$1"
  publish "$target" '.default_latency_test = strenv(LF_GUI_DELAY_DEFAULT)'
}
profiles() {
  local target="$data/profiles.yaml" kind bad changed
  [[ -e "$target" || -L "$target" ]] || return 0
  regular_map "$target" || return 1
  kind=$(yq '.items | tag' "$target" 2>/dev/null) || return 1
  [[ "$kind" != '!!null' ]] || return 0
  [[ "$kind" == '!!seq' ]] || return 1
  bad=$(yq '[.items[] | select(tag != "!!map" or (.type == "remote" and .option != null and (.option | tag) != "!!map"))] | length' "$target" 2>/dev/null) || return 1
  [[ "$bad" == 0 ]] || return 1
  changed=$(yq '[.items[] | select(.type == "remote" and (.option | tag) == "!!map" and (.option.update_interval | tag) == "!!int" and .option.update_interval >= 1 and .option.update_interval <= 1439)] | length' "$target" 2>/dev/null) || return 1
  [[ "$changed" != 0 ]] || return 0
  publish "$target" '(.items[] | select(.type == "remote" and (.option | tag) == "!!map" and (.option.update_interval | tag) == "!!int" and .option.update_interval >= 1 and .option.update_interval <= 1439) | .option.update_interval) = 1440'
}
result=0
preferences "$1" || { echo 'Clash Verge: invalid GUI preferences' >&2; result=1; }
profiles || { echo 'Clash Verge: invalid profile index; interval unchanged' >&2; result=1; }
exit "$result"
