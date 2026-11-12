{ config, lib, pkgs, ... }:

let
  cfg = config.my.homeGuard;
  homeDir = "/home/${cfg.user}";

  # chattr/lsattr live in e2fsprogs. Both are plain ioctls (FS_IOC_SETFLAGS),
  # not a daemon, so there is nothing to keep running.
  chattr = "${pkgs.e2fsprogs}/bin/chattr";
  lsattr = "${pkgs.e2fsprogs}/bin/lsattr";

  # One literal per line; shell globs are expanded at run time and entries
  # that match nothing are skipped, so a missing ~/.ssh does not break boot.
  pathLines = lib.concatStringsSep "\n    " (map (p: "'${p}'") cfg.paths);

  homeGuard = pkgs.writeShellScriptBin "home-guard" ''
    set -u

    CHATTR=${chattr}
    LSATTR=${lsattr}
    OWNER=${cfg.user}

    # FS_IOC_SETFLAGS on the immutable bit requires CAP_LINUX_IMMUTABLE, which
    # only root holds. That is the whole point: an agent running as $OWNER
    # cannot clear the flag, so the protection cannot be undone from inside
    # the process tree it compromised. The unit below runs this as PID 1's
    # child; interactive use re-execs itself through sudo.
    PATHS=(
    ${pathLines}
    )

    targets() {
      for entry in "''${PATHS[@]}"; do
        # Unquoted on purpose: expands the globs, and an unmatched glob stays
        # literal and is then dropped by the -e test.
        for path in $entry; do
          [ -e "$path" ] && printf '%s\n' "$path"
        done
      done
    }

    usage() {
      cat >&2 <<'EOF'
    usage: home-guard <lock|unlock|status>

      lock     set the immutable bit on every protected path (default)
      unlock   clear it, e.g. to accept a new ssh host key or save a document
      status   print each path with its current flag; exits 1 if any is loose
    EOF
      exit 2
    }

    if [ "$(id -u)" -ne 0 ]; then
      exec sudo -- "$0" "$@"
    fi

    cmd="''${1:-lock}"

    case "$cmd" in
      status)
        loose=0
        while IFS= read -r path; do
          line=$("$LSATTR" -d "$path" 2>/dev/null) || line=""
          flags="''${line%% *}"
          case "$flags" in
            *i*) printf '  locked    %s\n' "$path" ;;
            *)   printf '  UNLOCKED  %s\n' "$path"; loose=1 ;;
          esac
        done < <(targets)
        exit "$loose"
        ;;
      lock) flag=+i ;;
      unlock) flag=-i ;;
      *) usage ;;
    esac

    # A path that vanished between expansion and here is not an error worth
    # failing the boot transaction over, so report and keep going.
    failed=0
    while IFS= read -r path; do
      if err=$("$CHATTR" "$flag" "$path" 2>&1); then
        printf '  ok      %s %s\n' "$flag" "$path"
      else
        printf '  FAILED  %s %s: %s\n' "$flag" "$path" "$err" >&2
        failed=1
      fi
    done < <(targets)
    exit "$failed"
  '';

in
{
  options.my.homeGuard = {
    # On by default: this is the one layer that also covers agents started
    # outside the DSH desktop launcher (codex/opencode CLI, plain shells).
    enable = lib.mkEnableOption "immutable-flag guard for the home directory" // {
      default = true;
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "lfour";
      description = "Owner the protected paths belong to; the flags are set by root, this only names who is protected.";
    };

    home = lib.mkOption {
      type = lib.types.str;
      default = homeDir;
      description = "Home directory the default path list is built from.";
    };

    paths = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        # Irreplaceable data. A directory-level immutable flag makes `rm -rf`
        # fail on every entry instead of unlinking it; it also blocks creating
        # new files there, so `sudo home-guard unlock` before saving something.
        # Deliberately absent: ~/Pictures (niri writes screenshots there),
        # ~/Desktop, ~/Downloads, ~/Projects (high churn, agent workspace).
        #"${homeDir}/Documents"
        #"${homeDir}/Videos"
        #"${homeDir}/Music"

        # Credentials. Lock the files rather than ~/.ssh itself: ssh rewrites
        # known_hosts with temp-file + rename, which a locked directory would
        # block, while a locked key file can still be read by ssh.
        "${homeDir}/.ssh/id_*"
        "${homeDir}/.ssh/config"
        "${homeDir}/.ssh/authorized_keys"
        "${homeDir}/.config/sops/age/keys.txt"
      ];
      description = ''
        Paths to keep immutable. Shell globs are expanded when the helper runs.
        Directories stop accepting new entries while locked; lock files instead
        when that matters.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ homeGuard ];

    systemd.services.home-guard = {
      description = "Apply immutable flags to protected paths under ${cfg.home}";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" ];
      requires = [ "local-fs.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = "${homeGuard}/bin/home-guard lock";
    };
  };
}
