{ config, lib, pkgs, ... }:

let
  serviceNames = [ "proxy-mode" "NetworkManager" "dhcpcd" ]
    ++ lib.optional config.services.chrony.enable "chronyd";
  units = map (name: name + ".service") serviceNames;
  systemctl = "${pkgs.systemd}/bin/systemctl";
  pending = "/run/netsec/firewall-startup-pending";
  bootstrap = "network-firewall-bootstrap.service";
  bootEnabled = ''
    boot_enabled() {
      case "$(${systemctl} is-enabled "$1" 2>/dev/null || true)" in
        enabled|enabled-runtime) return 0 ;;
        *) return 1 ;;
      esac
    }
  '';
in
{
  systemd.services = lib.genAttrs serviceNames (_: {
    unitConfig.OnFailure = [ "network-firewall-boot-deferred@%n.service" ];
  }) // {
    # PID 1 dispatches OnFailure for a dependency-failed start job even when
    # the daemon never left inactive. Only capture the initial bootstrap
    # failure, before either the bootstrap or that daemon has started.
    "network-firewall-boot-deferred@" = {
      description = "Record a network startup job blocked by the bootstrap firewall";
      serviceConfig.Type = "oneshot";
      scriptArgs = "%i";
      script = ''
        ${bootEnabled}
        unit=$1
        case "$unit" in
          ${lib.concatStringsSep "|" units}) ;;
          *) exit 0 ;;
        esac
        boot_enabled "$unit" || exit 0
        [ "$(${systemctl} show -p InactiveExitTimestampMonotonic --value "$unit")" = 0 ] || exit 0
        dependency=${bootstrap}
        # The coordinator can also be canceled while the full policy is failing.
        [ "$unit" != proxy-mode.service ] || dependency=nftables.service
        [ "$(${systemctl} show -p ActiveEnterTimestampMonotonic --value "$dependency")" = 0 ] || exit 0
        if ${systemctl} is-failed --quiet ${bootstrap} \
           || { [ "$unit" = proxy-mode.service ] && ! ${systemctl} is-active --quiet "$dependency"; }; then
          ${pkgs.coreutils}/bin/touch "${pending}/$unit"
        fi
      '';
    };

    # A failed first load stops retrying when nftables hits its start limit.
    # The timer repairs it and consumes only recorded first-start jobs.
    nftables-recover = {
      description = "Recover failed firewall loads and deferred network startup jobs";
      serviceConfig.Type = "oneshot";
      script = ''
        ${bootEnabled}
        if ${systemctl} is-failed --quiet nftables.service \
           || ${systemctl} is-failed --quiet ${bootstrap}; then
          ${systemctl} reset-failed ${bootstrap} nftables.service
          ${systemctl} start nftables.service
          ${systemctl} start proxy-mode.service
        fi

        if ${systemctl} is-active --quiet nftables.service && ${systemctl} is-active --quiet ${bootstrap}; then
          for unit in ${lib.escapeShellArgs units}; do
            [ -f "${pending}/$unit" ] || continue
            if boot_enabled "$unit" \
               && [ "$(${systemctl} show -p InactiveExitTimestampMonotonic --value "$unit")" = 0 ]; then
              # Fail if a conflicting operator job already owns this unit.
              ${systemctl} start --no-block --job-mode=fail "$unit" || continue
            fi
            ${pkgs.coreutils}/bin/rm -f "${pending}/$unit"
          done
        fi
      '';
    };
  };

  systemd.tmpfiles.rules = [ "d ${pending} 0700 root root -" ];

  systemd.timers.nftables-recover = {
    description = "Retry firewall and deferred network startup";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "1min";
      AccuracySec = "10s";
    };
  };
}

