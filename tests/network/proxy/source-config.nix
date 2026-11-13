{ pkgs, config }:

let
  c = config;
  h = c.home-manager.users.${c.my.machine.desktopUser};
  entries = builtins.filter (p: builtins.elem (p.name or "") [ "firefox-identity" "firefox-portal" ]) h.home.packages;
  command = builtins.head (builtins.filter (p: (p.pname or (p.name or "")) == "clash-source") c.environment.systemPackages);

in
assert c.my.proxy.controlledSources && c.my.proxy.strictBrowser;
assert builtins.length entries == 2;
assert c.systemd.sockets.clash-strict.socketConfig.ListenStream == "/run/clash-strict/proxy.sock";
assert builtins.elem "sockets.target" c.systemd.sockets.clash-strict.wantedBy;
assert !h.my.firefox.dailyRequireProxy;
assert c.systemd.paths.clash-source-select.pathConfig ? PathChanged;
assert builtins.elem "CAP_DAC_READ_SEARCH" c.systemd.services.clash-source-select.serviceConfig.CapabilityBoundingSet;
assert builtins.elem "/var/lib/clash-source" c.systemd.services.clash-verge.serviceConfig.ReadWritePaths;
assert builtins.elem "/var/lib/clash-source" c.systemd.services.profile-refresh-verify.serviceConfig.ReadWritePaths;
assert c.my.proxy.dnsEncryption == "strict";

pkgs.runCommand "source-and-browser-config" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  test -x ${command}/bin/clash-source
  ${pkgs.lib.concatMapStringsSep "\n" (p: "test -d ${p}/bin") entries}
  python3 - <<'PY'
  import json
  from pathlib import Path
  stage = json.loads(Path('${c.programs.clash-verge.package.stageSettings}').read_text())
  browser = json.loads(Path('${h.programs.firefox.finalPackage.netsec.settings}').read_text())
  assert stage['source_root'] == '/var/lib/clash-source'
  assert browser['requireProxy'] is False and browser['proxyPort'] == 18080
  ${pkgs.lib.concatMapStringsSep "\n" (p: ''
  isolated = json.loads(Path('${p.netsec.settings}').read_text())
  assert isolated['requireProxy'] is (not isolated['portalOnly'])
  assert isolated['ephemeral'] is isolated['portalOnly']
  assert isolated['profile'] != browser['profile']
  '') entries}
  policy = json.loads(Path(stage['managed_policy']).read_text())
  assert policy['lf-private-targets']['subscription-trust'] == 'fixed'
  assert policy['lf-private-targets']['strict-inlets'] == ['lf-strict']
  assert policy['listeners'][0]['port'] == ${toString c.my.machine.ports.mihomoStrict}
  PY
  touch "$out"
''
