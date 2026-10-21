{ pkgs, ... }:

{
  # nftables.service gives up after StartLimitBurst (3 failures in 10 s) and systemd
  # then leaves it failed permanently; on a fresh boot that means no firewall at all.
  # Retry once a minute so a transient load failure self-heals, while a persistently
  # broken ruleset keeps failing loudly in the journal.
  systemd.services.nftables-recover = {
    description = "Restart nftables.service if (and only if) it is failed";
    serviceConfig = {
      Type = "oneshot";
    };
    script = ''
      if ${pkgs.systemd}/bin/systemctl is-failed --quiet nftables.service \
         || ${pkgs.systemd}/bin/systemctl is-failed --quiet network-firewall-bootstrap.service; then
        ${pkgs.systemd}/bin/systemctl reset-failed network-firewall-bootstrap.service
        ${pkgs.systemd}/bin/systemctl reset-failed nftables.service
        ${pkgs.systemd}/bin/systemctl start nftables.service
        # A dependency-failed first activation does not get Restart=always.
        ${pkgs.systemd}/bin/systemctl start proxy-mode.service
        for unit in NetworkManager.service dhcpcd.service; do
          if ${pkgs.systemd}/bin/systemctl is-failed --quiet "$unit"; then
            ${pkgs.systemd}/bin/systemctl start "$unit"
          fi
        done
      fi
    '';
  };

  systemd.timers.nftables-recover = {
    description = "Retry a failed nftables.service";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "1min";
      AccuracySec = "10s";
    };
  };
}

