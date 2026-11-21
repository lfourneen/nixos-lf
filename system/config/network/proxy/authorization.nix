{ config, lib, pkgs, ... }:

let
  enabled = config.my.proxy.authorizationLeaseSeconds != null;
  chains = [ "lf_lease_output" "lf_lease_input" "lf_lease_forward" "lf_lease_ingress" ];
  policyGuard = import ../firewall/policy-guard.nix { inherit lib; leaseEnabled = enabled; };

  reset = ''
    flush set inet lf_filter b_authorization
    ${lib.concatMapStrings (chain: "flush chain inet lf_filter ${chain}\n") chains}
  '';

  # Reuse the exact C rules, including reply ownership and recovery exceptions.
  # This avoids maintaining a second, subtly different expiry policy.
  copy = source: target: lib.concatMapStrings (line:
    let
      prefix = "add rule inet lf_filter ${source} ";
      proof = lib.any (name: line == prefix + policyGuard.stamp [ name ]) [ "mode" "service" ];
    in lib.optionalString (lib.hasPrefix prefix line && !proof)
      (builtins.replaceStrings [ prefix ] [ "add rule inet lf_filter ${target} " ] line + "\n"))
    (map lib.strings.trim (lib.splitString "\n" config.my.proxy.blockedModeRules));
  settings = pkgs.writeText "proxy-authorization.json" (builtins.toJSON {
    nft = "${pkgs.nftables}/bin/nft";
    seconds = config.my.proxy.authorizationLeaseSeconds;
    ip_tunnel = config.my.proxy.ipTunnelSettings;
  });

  command = pkgs.writeShellScript "proxy-authorization" ''
    exec ${pkgs.python3}/bin/python3 ${./authorization.py} ${settings} "$@"
  '';

in
{
  options.my.proxy = {
    authorizationRules = lib.mkOption { type = lib.types.str; internal = true; };
    authorizationReset = lib.mkOption { type = lib.types.str; internal = true; };
    authorizationCommand = lib.mkOption { type = lib.types.path; internal = true; };
  };

  config.my.proxy = {
    authorizationCommand = command;
    authorizationReset = reset;
    authorizationRules = reset + lib.optionalString enabled ''
      ${lib.concatMapStrings (chain: ''
        add rule inet lf_filter ${chain} ${policyGuard.stamp [ "lease" ]}
        add rule inet lf_filter ${chain} meta nfproto @b_authorization return
      '') chains}
      ${copy "lf_blocked_guard" "lf_lease_output"}
      ${copy "lf_tailnet_guard" "lf_lease_input"}
      ${copy "lf_strict_reply_guard" "lf_lease_input"}
      ${copy "proxymode_forward" "lf_lease_forward"}
      ${copy "lf_guest_guard" "lf_lease_ingress"}
    '';
  };
}
