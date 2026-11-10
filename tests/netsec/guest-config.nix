{ config, lib, pkgs, ... }:

let
  m = config.my.machine;
  h = config.my.hardening;
  profile = import ../../system/config/guest-policy.nix { inherit config lib; };

  variant = hardening: mode: let
    candidate = config // { my = config.my // { hardening = h // hardening; }; };
    network = import ../../system/config/network.nix { inherit pkgs lib; config = candidate; };
  in network.networking.nftables.ruleset + "\n" +
    (if mode == "proxy" then network.my.proxy.proxyModeRules else "");

  restrict = lib.mapAttrs (_: n: n // { trust = "restricted"; hostTcpPorts = [ ]; hostUdpPorts = [ ]; }) h.guestNetworks;
  trustedAp = h.guestNetworks // { ${m.hotspot.interface} = h.guestNetworks.${m.hotspot.interface} // { trust = "trusted"; }; };

  fixture = {
    python = "${pkgs.python3}/bin/python3";
    dnsmasq = "${config.services.dnsmasq.package}/bin/dnsmasq";
    dhcpcd = "${pkgs.dhcpcd}/bin/dhcpcd";
    listenerCheck = config.my.proxy.listenerCheck;
    pasta = "${pkgs.passt}/bin/pasta";
    rootlesskit = "${pkgs.rootlesskit}/bin/rootlesskit";
    backendPath = lib.makeBinPath [ pkgs.slirp4netns pkgs.iproute2 pkgs.util-linux pkgs.coreutils ] + ":/run/wrappers/bin";
    networks = map (n: { inherit (n) interface source4 source6 gateway4 gateway6 trust; }) profile.networks;
    private4 = "100.67.0.1";
    other4 = "100.67.0.3";
    private6 = "fd42:feed::1";
    public4 = "203.0.113.10";
    public6 = "2001:db8:dead::1";
  };

in
{
  my.hardening = {
    hotspot.enable = true;
    guestNetworks = {
      virbr0 = { trust = "trusted"; sourceCidrs = [ m.vmSubnet "fd42:122::/64" ]; gateways = [ m.vmAddress "fd42:122::1" ]; hostTcpPorts = [ 18081 ]; hostUdpPorts = [ 18082 ]; };
      waydroid0 = { sourceCidrs = [ m.waydroidSubnet "fd42:240::/64" ]; gateways = [ m.waydroidAddress "fd42:240::1" ]; };
      ap0 = { sourceCidrs = [ m.hotspot.subnet "fd42:42::/64" ]; gateways = [ m.hotspot.address "fd42:42::1" ]; };
    };

    guestPrivateExceptions = [
      { interface = "waydroid0"; cidr = "100.67.0.1/32"; tcpPorts = [ 18081 ]; udpPorts = [ 18082 ]; }
      { interface = "waydroid0"; cidr = "fd42:feed::1/128"; tcpPorts = [ 18081 ]; udpPorts = [ 18082 ]; }
    ];

    socketGuestUsers = [ "guestnet" ];
    socketGuestPrivateExceptions = [
      { user = "guestnet"; interface = "ens1"; cidr = "100.67.0.1/32"; tcpPorts = [ 18081 ]; udpPorts = [ 18082 ]; }
      { user = "guestnet"; interface = "ens1"; cidr = "fd42:feed::1/128"; tcpPorts = [ 18081 ]; udpPorts = [ 18082 ]; }
    ];
  };

  my.proxy.transparentGuestInterfaces = [ "virbr0" ];
  my.proxy.blockedDomains = [ "blocked.guest.fixture.example" ];
  users.users.lfour.subUidRanges = [{ startUid = 200000; count = 65536; }];
  users.users.lfour.subGidRanges = [{ startGid = 200000; count = 65536; }];
  users.groups.guestnet = { };

  users.users.guestnet = {
    isNormalUser = true;
    group = "guestnet";
    uid = 1100;
    subUidRanges = [{ startUid = 400000; count = 65536; }];
    subGidRanges = [{ startGid = 400000; count = 65536; }];
  };

  services.unbound.settings.server.local-zone = [ ''"guest.fixture.example." static'' ];
  services.unbound.settings.server.local-data = [
    ''"public.guest.fixture.example. 0 IN A 203.0.113.10"''
    ''"private.guest.fixture.example. 0 IN A 100.67.0.1"''
    ''"blocked.guest.fixture.example. 0 IN A 203.0.113.10"''
  ];

  # A's external DNS is deliberately absent in this VM. Keep one explicit
  # local name usable through the normal guest gateway in every mode.
  services.dnsmasq.settings.server = [ "/public.guest.fixture.example/127.0.0.1#1055" ];
  networking.networkmanager.unmanaged = [ "interface-name:virbr0" "interface-name:waydroid0" "interface-name:ap0" "interface-name:gv*" ];
  environment.etc."netsec-guests.json".text = builtins.toJSON fixture;
  environment.etc."netsec-guests.py".source = ./guest_fixture.py;
  environment.etc."netsec-guests-base-a.nft".text = variant { } "direct";
  environment.etc."netsec-guests-base-b.nft".text = variant { } "proxy";
  environment.etc."netsec-guests-trust-ap.nft".text = variant { guestNetworks = trustedAp; } "proxy";
  environment.etc."netsec-guests-revoke.nft".text = variant {
    guestNetworks = restrict;
    guestPrivateExceptions = [ ];
    socketGuestPrivateExceptions = [ ];
  } "proxy";

  environment.etc."netsec-guests-blocked.nft".text = config.my.proxy.blockedModeRules;
  environment.etc."netsec-guests-dhcpcd.conf".text = ''
    nohook resolv.conf,hostname,ntp.conf
    noipv6
    noipv4ll
  '';

  environment.systemPackages = [ pkgs.passt pkgs.rootlesskit pkgs.slirp4netns ];
  virtualisation.memorySize = lib.mkForce 2048;
}
