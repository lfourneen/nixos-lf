# Only desktop helper reads may reach the actual OS settings.
tool=${0##*/}
real=
case "$tool" in
  kwriteconfig5|kwriteconfig6) exit 0 ;;
  kreadconfig5|kreadconfig6) real=${LF_GUI_REAL_KREADCONFIG:-} ;;
  gsettings|dconf)
    command=
    skip=0
    for arg do
      if [ "$skip" = 1 ]; then skip=0; continue; fi
      case "$arg" in
        --schemadir|-s) skip=1 ;;
        -*) ;;
        *) command=$arg; break ;;
      esac
    done
    case "$tool:$command" in
      gsettings:get|gsettings:list-schemas|gsettings:list-relocatable-schemas|\
      gsettings:list-keys|gsettings:list-children|gsettings:list-recursively|\
      gsettings:range|gsettings:writable|gsettings:describe|gsettings:monitor|\
      gsettings:help|gsettings:version)
        real=${LF_GUI_REAL_GSETTINGS:-}
        unset GSETTINGS_BACKEND
        ;;
      dconf:read|dconf:list|dconf:dump|dconf:watch|dconf:help)
        real=${LF_GUI_REAL_DCONF:-}
        ;;
      *) exit 0 ;;
    esac
    ;;
  *) printf '%s\n' "clash-verge control shim: unsupported helper $tool" >&2; exit 2 ;;
esac
# A missing read helper must not trigger the GUI's restart fallback.
[ -n "$real" ] && [ -f "$real" ] && [ -x "$real" ] || exit 0
exec "$real" "$@"
