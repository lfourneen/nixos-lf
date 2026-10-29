{ lib, pkgs, ... }:

let
  # Cluster socket families are outside this desktop's network contract.
  disabledProtocols = [ "rds" "tipc" ];

in
{
  boot.blacklistedKernelModules = disabledProtocols;
  # A blacklist only suppresses aliases. Also deny dependency/explicit modprobe
  # loads, including the kernel's socket-triggered module helper.
  boot.extraModprobeConfig = lib.concatMapStringsSep "\n"
    (name: "install ${name} ${pkgs.coreutils}/bin/false") disabledProtocols;

  boot.kernel.sysctl = {
    # Keep existing interfaces and newly created TUN/guest links consistent.
    # systemd-sysctl also applies these globs when an interface appears.
    "net.ipv4.conf.all.accept_redirects" = 0;
    "net.ipv4.conf.default.accept_redirects" = 0;
    "net.ipv4.conf.*.accept_redirects" = 0;
    "net.ipv6.conf.all.accept_redirects" = 0;
    "net.ipv6.conf.default.accept_redirects" = 0;
    "net.ipv6.conf.*.accept_redirects" = 0;
    "net.ipv4.conf.all.secure_redirects" = 0;
    "net.ipv4.conf.default.secure_redirects" = 0;
    "net.ipv4.conf.*.secure_redirects" = 0;
    # send_redirects uses all OR interface, so all=0 alone is insufficient.
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.conf.default.send_redirects" = 0;
    "net.ipv4.conf.*.send_redirects" = 0;
    "net.ipv4.conf.all.accept_source_route" = 0;
    "net.ipv4.conf.default.accept_source_route" = 0;
    "net.ipv4.conf.*.accept_source_route" = 0;
    "net.ipv6.conf.all.accept_source_route" = 0;
    "net.ipv6.conf.default.accept_source_route" = 0;
    "net.ipv6.conf.*.accept_source_route" = 0;

    "net.ipv4.conf.all.log_martians" = 1;
    "net.ipv4.conf.default.log_martians" = 1;
    # Loose validation supports the TUN and asymmetric multi-uplink routing.
    "net.ipv4.conf.all.rp_filter" = 2;
    "net.ipv4.conf.default.rp_filter" = 2;
    "net.ipv4.conf.all.arp_ignore" = 1;
    "net.ipv4.conf.default.arp_ignore" = 1;
    "net.ipv4.conf.all.arp_announce" = 2;
    "net.ipv4.conf.default.arp_announce" = 2;
    "net.ipv4.icmp_echo_ignore_broadcasts" = 1;
    "net.ipv4.icmp_ignore_bogus_error_responses" = 1;
    "net.ipv4.tcp_syncookies" = 1;
    "net.ipv4.tcp_rfc1337" = 1;

    # Required by libvirt/Waydroid; the nftables forward policy remains in charge.
    "net.ipv4.ip_forward" = 1;
  };
}
