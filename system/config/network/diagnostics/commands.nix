{ pkgs, config, lib, ... }:

let
  statusPython = pkgs.python3.withPackages (p: [ p.pyyaml ]);
  settings = import ./settings.nix { inherit pkgs config lib; };

in
{
  environment.systemPackages = [ (pkgs.writeShellScriptBin "proxy-status" ''
    exec ${statusPython}/bin/python3 ${../../../../home/config/clash/status.py} ${../proxy/state.py} \
      --profiles ${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml \
      --max-age ${toString config.my.proxy.stateMaxAgeSeconds} \
      --verification-max-age ${toString config.my.proxy.verificationMaxAgeSeconds} ${if config.my.proxy.proxyOnly then "--proxy-only" else ""} ${if config.my.proxy.controlledSources then "--controlled-source" else ""} "$@"
  '') (pkgs.writeShellScriptBin "proxy-inspect" ''
    exec ${statusPython}/bin/python3 ${./inspect.py} ${settings} "$@"
  '') (pkgs.writeShellScriptBin "proxy-policy" ''
    exec ${statusPython}/bin/python3 ${./inspect.py} ${settings} --policy-only
  '') (pkgs.writeShellScriptBin "proxy-subscription" ''
    exec ${statusPython}/bin/python3 ${./subscription_check.py} \
      ${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml \
      --proxy http://127.0.0.1:${toString config.my.machine.ports.mihomoMixed} "$@"
  '') ];
}
