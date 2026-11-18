{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  cfg = p.ipTunnel;
  option = type: description: lib.mkOption { inherit type description; };
  address = option lib.types.str;
  port = option lib.types.port;
  
in
{
  options.my.proxy = {
    ipTunnel = lib.mkOption {
      default = null;
      type = lib.types.nullOr (lib.types.submodule {
        options = {
          endpointAddress = address "Fixed public WireGuard endpoint address.";
          endpointPort = port "UDP port of the WireGuard peer.";
          listenPort = lib.mkOption {
            type = lib.types.port;
            default = 51821;
            description = "Reserved local UDP port for WireGuard's outer socket.";
          };
          publicKey = address "WireGuard public key of the remote exit.";
          privateKeyFile = address "Absolute runtime path to the root-only WireGuard private key.";
          clientIpv4 = address "IPv4 address assigned to this client inside WireGuard.";
          clientIpv6 = address "IPv6 address assigned to this client inside WireGuard.";
          relayAddress = address "Public literal address of the peer's tunnel-only SOCKS5 listener.";
          relayPort = port "Fixed port used by the peer for SOCKS5 TCP and UDP.";
          probeIpv4 = address "Controlled public IPv4 echo target beyond the peer.";
          probeIpv6 = address "Controlled public IPv6 echo target beyond the peer.";
          mtu = lib.mkOption {
            type = lib.types.ints.between 1280 1500;
            default = 1380;
            description = "WireGuard interface MTU.";
          };
        };
      });
      description = "Use a fixed WireGuard exit for proxy-only, with native IPv4 and IPv6 ICMP.";
    };
    ipTunnelPolicy = lib.mkOption { type = lib.types.attrs; internal = true; };
    ipTunnelSettings = lib.mkOption { type = lib.types.nullOr lib.types.attrs; internal = true; };
  };

  config = {
    my.proxy.ipTunnelPolicy = if cfg == null then { } else {
      inherit (cfg) endpointAddress endpointPort listenPort publicKey clientIpv4 clientIpv6 relayAddress relayPort probeIpv4 probeIpv6 mtu;
      device = "lf-wg-exit";
      table = 763;
      priority = 4900;
      outerMark = "0x4c460010";
      probeMark = "0x4c460011";
      echoMark = "0x4c460012";
      uplinks = [ config.my.machine.wired.name config.my.machine.wireless.name ];
    };

    my.proxy.ipTunnelSettings = if cfg == null then null else {
      policy = p.ipTunnelPolicy;
      inherit (cfg) privateKeyFile;
      ownership = "/run/proxy-mode/ip-tunnel-owner.json";
      ip = "${pkgs.iproute2}/bin/ip";
      wg = "${pkgs.wireguard-tools}/bin/wg";
      ping = "${pkgs.iputils}/bin/ping";
      module = ./ip_tunnel.py;
    };

    boot.kernelModules = lib.optional (cfg != null) "wireguard";
    networking.networkmanager.unmanaged = lib.optional (cfg != null) "interface-name:${p.ipTunnelPolicy.device}";
    assertions = lib.optional (cfg != null) {
      assertion = p.runtimeRouting && config.networking.enableIPv6
        && cfg.listenPort > 0 && cfg.endpointPort > 0 && cfg.relayPort > 0
        && p.routeTable != p.ipTunnelPolicy.table && lib.hasPrefix "/" cfg.privateKeyFile
        && !(lib.hasPrefix builtins.storeDir cfg.privateKeyFile)
        && lib.all (value: builtins.match "[0-9a-fA-F:.]+" value != null) [
          cfg.endpointAddress cfg.clientIpv4 cfg.clientIpv6 cfg.relayAddress cfg.probeIpv4 cfg.probeIpv6
        ] && builtins.match "[a-zA-Z0-9+/]{43}=" cfg.publicKey != null;
      message = "The strict IP tunnel requires runtime routing, IPv6, a separate route table, literal addresses and a runtime WireGuard key.";
    };
  };
}
