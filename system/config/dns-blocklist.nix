{ pkgs, ... }:

# Domain names answered NXDOMAIN at the shared DNS front door (dnsmasq :1054).
# That port is what every mode and the guest DHCP range resolve through, so a
# name blocked here is blocked in Mode A, in Mode B and for guests alike.
# dnsmasq matches suffixes, so one entry covers the domain and every name below
# it.
#
# The bulk set lives in ../../local-data/dns-blocklist.txt.xz: one bare
# lowercase name per line, merged and de-duplicated from the upstream lists
# recorded below. It is kept out of Git (see local-data/README.md); the flake
# is evaluated from /etc/nixos, where scripts/push-to-dir.sh copies it together
# with this tree. Run that script before nixos-rebuild. It is
# parsed once at dnsmasq start and nothing is fetched at build time or at run
# time.
#
# Refresh, from the repository root:
#   ./scripts/refresh-dns-blocklist.py          # fetch, merge, rewrite data + header
#   ./scripts/refresh-dns-blocklist.py --check  # report drift, write nothing
#
# BEGIN GENERATED
#   stevenblack    76734  https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
#   peterlowe       3561  https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext
#   adaway          6540  https://adaway.org/hosts.txt
#   oisd_big      235065  https://big.oisd.nl/dnsmasq
#   1hosts_lite   202951  https://raw.githubusercontent.com/badmojr/1Hosts/master/Lite/hosts.txt
#   notracking    250165  https://raw.githubusercontent.com/notracking/hosts-blocklists/master/hostnames.txt
#   total         678813  merged and de-duplicated
# Snapshot: 2026-10-31
# END GENERATED
#
# Additions that are specific to this host belong in `my.proxy.blockedDomains`
# (see system/config/proxy-options.nix); they render as their own address=
# lines and leave the merged file alone.
let
  data = ../../local-data/dns-blocklist.txt.xz;
  # The file is git-ignored, so a fresh clone does not have it and a pure
  # evaluation from the checkout cannot see it. Say where it comes from instead
  # of failing with a bare "path does not exist".
  dataExists = builtins.pathExists data;
  dataSrc =
    if dataExists then data
    else throw ''
      local-data/dns-blocklist.txt.xz is missing.
      Run ./scripts/refresh-dns-blocklist.py from the repository root, or copy
      local-data/ next to this flake (scripts/push-to-dir.sh does that for
      /etc/nixos). See local-data/README.md.
    '';
  rules = pkgs.runCommand "dnsmasq-dns-blocklist.d" { } ''
    mkdir -p $out
    ${pkgs.xz}/bin/xz -dc ${dataSrc} \
      | ${pkgs.gnugrep}/bin/grep -v '^$' \
      | ${pkgs.gnused}/bin/sed -e 's|^|address=/|' -e 's|$|/|' \
      > $out/blocklist.conf
  '';

in
{
  # conf-dir is read as configuration, so the generated file is just a batch of
  # address= directives, the same form the hand-written list uses.
  config.services.dnsmasq.settings.conf-dir = "${rules}";
}
