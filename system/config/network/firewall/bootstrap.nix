{ pkgs, config, lib, ... }:

let
  m = config.my.machine;
  dnsUplinks = import ./dns-uplinks.nix { inherit config lib; };
  bootstrap = pkgs.writeText "lf-bootstrap.nft" ''
    destroy table inet lf_bootstrap
    table inet lf_bootstrap {
      chain input {
        type filter hook input priority -10; policy drop;
        iifname "lo" accept
        ct state invalid drop
        ${import ./ndp-validation.nix}
        ct state established,related accept
        ${dnsUplinks.match "iifname"} udp sport 67 udp dport 68 accept
        ${dnsUplinks.match "iifname"} udp sport 547 udp dport 546 accept
        meta l4proto icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert, packet-too-big, destination-unreachable, time-exceeded } accept
      }
      chain forward {
        type filter hook forward priority -10; policy drop;
      }
      chain output {
        type filter hook output priority -196; policy accept;
        oifname "lo" accept
        ${lib.concatMapStringsSep "\n" (rule: "${dnsUplinks.match "oifname"} ${rule}")
          (dnsUplinks.dotRules config.users.users.unbound.uid "")}
        # Both DNS modes start with the encrypted upstream. The full
        # policy owns any subsequent split or portal address leases.
        meta l4proto { tcp, udp } th dport { 53, ${toString m.ports.dot} } counter drop
      }
    }
  '';

in
{
  # First-load failure retains this independent ingress and DNS baseline. The complete
  # static+mode transaction removes it only when the full policy is installed.
  systemd.services.network-firewall-bootstrap = {
    description = "Install bootstrap ingress and DNS rules before physical networking";
    wantedBy = [ "network-pre.target" ];
    wants = [ "sysinit.target" "network-pre.target" ];
    after = [ "sysinit.target" ];
    before = [ "network-pre.target" "nftables.service" "NetworkManager.service" "dhcpcd.service" ];

    unitConfig = {
      DefaultDependencies = false;
      OnFailure = [ "netsec-alert@%n.service" ];
    };

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "network-firewall-bootstrap" ''
        set -eu
        ${pkgs.nftables}/bin/nft -f ${bootstrap}
        ${lib.optionalString (config.my.proxy.bootMode == "proxy") ''
        mkdir -p /run/proxy-mode
        if [ ! -e /run/proxy-mode/intent ]; then
          printf 'proxy\n' > /run/proxy-mode/intent
        fi
        ${config.my.proxy.modeScript} --reload
        ''}
      '';

      TimeoutStartSec = "30s";
      ExecStartPost = "${pkgs.python3}/bin/python3 ${./alerts.py} clear network-firewall-bootstrap.service";
      # No ExecStop: stopping an owner must never tear down protection.
    };
  };

  systemd.services.nftables = {
    requires = [ "network-firewall-bootstrap.service" ];
    after = [ "network-firewall-bootstrap.service" ];
    restartTriggers = [ bootstrap ];
  };

  systemd.services.NetworkManager = {
    requires = [ "network-firewall-bootstrap.service" ];
    after = [ "network-firewall-bootstrap.service" ];
  };

  systemd.services.dhcpcd = {
    requires = [ "network-firewall-bootstrap.service" ];
    after = [ "network-firewall-bootstrap.service" ];
  };
}

