{ osConfig, lib, pkgs, ... }:

let
  inherit (osConfig.my) proxy;
  proxyEnv = import ../../system/config/network/proxy/env.nix { inherit lib; };
  gostHttp = proxyEnv.gostHttp osConfig.my.machine.ports.gostHttp;
  gui = "${pkgs.python3}/bin/python3 ${../../system/programs/clash/gui/clash-verge-gui.py}";
  gtk3Schema = "${pkgs.gtk3}/share/gsettings-schemas/${pkgs.gtk3.name}";
  gtk4Schema = "${pkgs.gtk4}/share/gsettings-schemas/${pkgs.gtk4.name}";
  gstBase = "${pkgs.gst_all_1.gst-plugins-base}/lib/gstreamer-1.0";
  gstGood = "${pkgs.gst_all_1.gst-plugins-good}/lib/gstreamer-1.0";

in
{
  xdg.configFile."brush/config.toml".text = ''
    [ui]
    syntax-highlighting = true

    [experimental]
    terminal-shell-integration = true
  '';

  # Interactive login sessions read the profile. Ordinary terminals read .brushrc directly.
  home.file.".bash_profile".text = ''
    if [ -r "$HOME/.bash_login" ]; then
      . "$HOME/.bash_login"
    elif [ -r "$HOME/.profile" ]; then
      . "$HOME/.profile"
    fi

    case $- in
      *i*)
        if [ -n "''${BRUSH_VERSION:-}" ] && [ -r "$HOME/.brushrc" ]; then
          . "$HOME/.brushrc"
        fi
        ;;
    esac
  '';

  home.file.".brushrc".text = ''
    case $- in *i*) ;; *) return ;; esac

    case ":$PATH:" in
      *":$HOME/.local/bin:"*) ;;
      *) export PATH="$HOME/.local/bin''${PATH:+:$PATH}" ;;
    esac
    export EDITOR=nvim VISUAL=nvim
    export DOCKER_HOST="unix://''${XDG_RUNTIME_DIR:-/run/user/$UID}/docker.sock"

    # Brush normally flushes history only when the session exits. Flush unsaved
    # entries before each prompt so a newly opened terminal can load them from
    # HISTFILE (default ~/.brush_history) instead of only seeing this session.
    PROMPT_COMMAND='history -a'

    _brush_git_prompt() {
      local ref
      ref=$(git branch --show-current 2>/dev/null) || return 0
      if [ -z "$ref" ]; then
        ref=$(git rev-parse --short HEAD 2>/dev/null) || return 0
      fi
      printf ' (git:%s)' "$ref"
    }

    PS1='[\u@\H|\w$(_brush_git_prompt)]->>> '

    _brush_append_path() {
      case ":$1:" in
        *":$2:"*) printf '%s' "$1" ;;
        *) printf '%s%s' "''${1:+$1:}" "$2" ;;
      esac
    }

    XDG_DATA_DIRS=$(_brush_append_path "''${XDG_DATA_DIRS:-}" '${gtk3Schema}')
    XDG_DATA_DIRS=$(_brush_append_path "$XDG_DATA_DIRS" '${gtk4Schema}')
    GST_PLUGIN_SYSTEM_PATH_1_0=$(_brush_append_path "''${GST_PLUGIN_SYSTEM_PATH_1_0:-}" '${gstBase}')
    GST_PLUGIN_SYSTEM_PATH_1_0=$(_brush_append_path "$GST_PLUGIN_SYSTEM_PATH_1_0" '${gstGood}')
    export XDG_DATA_DIRS GST_PLUGIN_SYSTEM_PATH_1_0
    unset -f _brush_append_path

    # Color output when writing directly to a terminal.
    alias ls='ls --color=auto'
    alias grep='grep --color=auto'
    alias diff='diff --color=auto'

    # Keep interactive removal recoverable. See trash-list and trash-restore.
    alias rm='trash-put'

    _brush_usage() {
      local usage=$1
      shift
      if [ "$#" -eq 1 ] && [ "$1" = --help ]; then
        printf 'Usage: %s\n' "$usage"
      else
        printf 'Usage: %s\n' "$usage" >&2
        return 2
      fi
    }

    _brush_option() {
      local name=$1 flag=$2 fallback=$3
      shift 3
      case $# in
        0) printf '%s' "$fallback"; return ;;
        1)
          case $1 in
            "$flag="*) printf '%s' "''${1#*=}"; return ;;
          esac
          ;;
        2)
          if [ "$1" = "$flag" ]; then
            printf '%s' "$2"
            return
          fi
          ;;
      esac
      printf 'Usage: %s [%s VALUE]\n' "$name" "$flag" >&2
      return 2
    }

    _brush_switch() {
      printf '%s\n' 'Rebuilding system with nh...'
      sudo nh os switch --ask --bypass-root-check "$1" -- --verbose
    }

    _brush_update_flake() {
      printf 'Updating flake inputs in %s...\n' "$1"
      local status
      sudo nix flake update --flake "$1" || {
        status=$?
        printf 'Flake update failed [exit code: %s]\n' "$status" >&2
        return "$status"
      }
    }

    update() {
      [ "''${1:-}" != --help ] || { _brush_usage 'update [--flake VALUE]' "$@"; return; }
      local flake status
      flake=$(_brush_option update --flake /etc/nixos#lfour "$@") || return
      if _brush_switch "$flake"; then
        printf '%s\n' 'System updated successfully.'
      else
        status=$?
        printf 'Update failed [exit code: %s]\n' "$status" >&2
        return "$status"
      fi
    }

    upgrade() {
      [ "''${1:-}" != --help ] || { _brush_usage 'upgrade [--flake VALUE]' "$@"; return; }
      local flake status
      flake=$(_brush_option upgrade --flake /etc/nixos#lfour "$@") || return
      _brush_update_flake "''${flake%%#*}" || return
      if _brush_switch "$flake"; then
        printf '%s\n' 'Upgrade completed successfully.'
      else
        status=$?
        printf 'Upgrade failed [exit code: %s]\n' "$status" >&2
        return "$status"
      fi
    }

    flake() {
      [ "''${1:-}" != --help ] || { _brush_usage 'flake [--dir VALUE]' "$@"; return; }
      local dir
      dir=$(_brush_option flake --dir /etc/nixos "$@") || return
      _brush_update_flake "$dir" && printf '%s\n' 'Flake updated successfully.'
    }

    fix() {
      [ "$#" -eq 0 ] || { _brush_usage fix "$@"; return; }
      sudo nix-store --verify --check-contents --repair
    }

    garbage() {
      [ "$#" -eq 0 ] || { _brush_usage garbage "$@"; return; }
      nh clean all --ask
    }

    g3d() {
      [ "''${1:-}" != --help ] || { _brush_usage 'g3d [--days VALUE]' "$@"; return; }
      local days
      days=$(_brush_option g3d --days 3 "$@") || return
      case $days in
        ""|*[!0-9]*) printf '%s\n' 'g3d: --days expects a non-negative integer' >&2; return 2 ;;
      esac
      sudo nix-collect-garbage --delete-older-than "''${days}d" &&
        nix-collect-garbage --delete-older-than "''${days}d"
    }

    ${lib.optionalString (!proxy.transparentApplications) ''
    proxy-on() {
      [ "$#" -eq 0 ] || { _brush_usage proxy-on "$@"; return; }
      export http_proxy='${gostHttp}' https_proxy='${gostHttp}'
    }
    proxy-on
    ''}

    proxy-off() {
      [ "$#" -eq 0 ] || { _brush_usage proxy-off "$@"; return; }
      unset ${lib.concatStringsSep " " proxyEnv.unsetNames}
      printf '%s\n' 'Shell proxy variables cleared. The host keeps its current proxy mode.'
    }

    clash-gui-running() {
      [ "$#" -eq 0 ] || { _brush_usage clash-gui-running "$@"; return; }
      if ${gui} --running; then
        printf '%s\n' true
      else
        printf '%s\n' false
        return 1
      fi
    }

    _brush_detach_clash_gui() {
      command -v "$1" >/dev/null || {
        printf 'Clash Verge GUI command unavailable: %s\n' "$1" >&2
        return 127
      }
      # Keep GUI processes independent of the interactive shell's job table.
      ${lib.getExe' pkgs.util-linux "setsid"} --fork -- "$@" </dev/null >/dev/null 2>&1
    }

    clash-off() {
      [ "$#" -eq 0 ] || { _brush_usage clash-off "$@"; return; }
      sudo proxy-control off || return
      _brush_detach_clash_gui ${gui} --stop
    }

    clash-on() {
      [ "$#" -eq 0 ] || { _brush_usage clash-on "$@"; return; }
      local status
      printf '%s\n' 'Starting the Clash Verge service and GUI...'
      sudo proxy-control on || {
        status=$?
        ${lib.optionalString (!proxy.runtimeRouting) ''
        printf '%s\n' 'Proxy startup failed. See the status below.' >&2
        proxy-status
        ${if proxy.controlledSources then ''
        printf '%s\n' 'Use sudo proxy-control preflight to check the source and registration.'
        '' else ''
        if _brush_detach_clash_gui clash-verge; then
          printf '%s\n' 'Run clash-on again after the GUI has written its profile.'
        fi
        ''}
        ''}
        return "$status"
      }
      # The launcher replaces an older GUI singleton before requesting a core.
      _brush_detach_clash_gui clash-verge || return
      ${lib.optionalString (!proxy.runtimeRouting) ''
      printf '%s\n' 'Waiting for A -> C -> B...'
      proxy-control wait proxy || {
        status=$?
        proxy-status
        return "$status"
      }
      ''}
      printf '%s\n' 'Mode B ready.'
    }

    clash-routing() {
      if [ "$#" -ne 1 ] || [ "$1" = --help ]; then
        _brush_usage 'clash-routing rules|proxy-only' "$@"
        return
      fi
      sudo proxy-control routing "$@"
    }

    clash-rules() {
      [ "$#" -eq 0 ] || { _brush_usage clash-rules "$@"; return; }
      clash-routing rules
    }

    clash-proxy-only() {
      [ "$#" -eq 0 ] || { _brush_usage clash-proxy-only "$@"; return; }
      clash-routing proxy-only
    }

    clash-egress() {
      if [ "$#" -gt 1 ] || [ "''${1:-}" = --help ]; then
        _brush_usage 'clash-egress [list|ID]' "$@"
        return
      fi
      sudo proxy-control egress "''${1:-list}"
    }

    egress-audit() {
      sudo bpftrace /run/current-system/sw/share/bpftrace/tools/tcpconnect.bt "$@"
    }

    hermes() {
      printf '%s\n' 'Launching Hermes Agent in isolated sandbox mode...'
      sudo -u hermes -i hermes "$@"
    }

    usbguard-off() {
      [ "$#" -eq 0 ] || { _brush_usage usbguard-off "$@"; return; }
      printf '%s\n' 'Stopping USBGuard and authorizing USB devices...'
      sudo systemctl stop usbguard &&
        sudo sh -c 'echo 1 > /sys/module/usbcore/parameters/authorized_default' &&
        sudo sh -c 'for f in /sys/bus/usb/devices/*/authorized; do echo 1 > "$f"; done' &&
        printf '%s\n' 'USBGuard disabled.'
    }

    usbguard-on() {
      [ "$#" -eq 0 ] || { _brush_usage usbguard-on "$@"; return; }
      printf '%s\n' 'Restoring USBGuard...'
      sudo sh -c 'echo 2 > /sys/module/usbcore/parameters/authorized_default' &&
        sudo systemctl start usbguard &&
        printf '%s\n' 'USBGuard enabled.'
    }
  '';
}
