# Clash Verge owns this mutable file. Only fill an unset preference before launch.
target="${XDG_DATA_HOME:-$HOME/.local/share}/io.github.clash-verge-rev.clash-verge-rev/verge.yaml"
[[ -e "$target" || -L "$target" ]] || exit 0

# A second invocation must not edit settings held by an already running GUI.
if pgrep --ignore-ancestors -u "$(id -u)" -f '(^|/)[.]?clash-verge(-wrapped|-launcher)?( |$)' >/dev/null; then
  exit 0
fi
[[ -f "$target" && ! -L "$target" && -w "$target" ]] || exit 1

# Reject malformed/unexpected settings instead of rewriting them.
[[ "$(yq 'tag' "$target" 2>/dev/null)" == '!!map' ]] || exit 1
kind=$(yq '.default_latency_test | tag' "$target" 2>/dev/null)
case "$kind" in
  '!!null') ;;
  '!!str')
    current=$(yq '.default_latency_test' "$target")
    [[ -z "${current//[[:space:]]/}" ]] || exit 0
    ;;
  *) exit 1 ;;
esac

# Keep unknown preferences, comments and permissions; publish a complete file.
temporary=$(mktemp "${target}.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
LF_GUI_DELAY_DEFAULT="$1" yq '.default_latency_test = strenv(LF_GUI_DELAY_DEFAULT)' \
  "$target" > "$temporary"
chmod --reference="$target" "$temporary"
mv -fT -- "$temporary" "$target"
