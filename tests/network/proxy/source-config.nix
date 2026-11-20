{ pkgs, config }:

let
  c = config;
  h = c.home-manager.users.${c.my.machine.desktopUser};
  entries = builtins.filter (p: builtins.elem (p.name or "") [ "firefox-identity" "firefox-portal" ]) h.home.packages;
  command = builtins.head (builtins.filter (p: (p.pname or (p.name or "")) == "clash-source") c.environment.systemPackages);
  diagnosticCommands = builtins.filter (p: builtins.elem (p.pname or (p.name or "")) [ "clash-diff" ]) c.environment.systemPackages;

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
assert c.users.users.clash-fetch.uid == c.my.machine.uids.clashFetch;
assert c.systemd.services.socket-guest-dns.enable;
assert builtins.elem "socket-guest-dns.service" c.systemd.services.clash-source-refresh.after;
assert builtins.elem "clash-core" c.systemd.services.clash-source-refresh.serviceConfig.SupplementaryGroups;
assert !(builtins.elem "CAP_NET_ADMIN" c.systemd.services.clash-source-refresh.serviceConfig.CapabilityBoundingSet);
assert c.systemd.services.clash-source-guard.serviceConfig.CapabilityBoundingSet == [ "CAP_NET_ADMIN" ];
assert c.systemd.services.clash-source-guard.serviceConfig.NoNewPrivileges;
assert builtins.length diagnosticCommands == 1;
assert !(builtins.any (p: (p.pname or (p.name or "")) == "clash-apply") c.environment.systemPackages);
assert !(pkgs.lib.hasInfix "clash-apply" h.programs.nushell.extraConfig);

pkgs.runCommand "source-and-browser-config" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  test -x ${command}/bin/clash-source
  ${pkgs.lib.concatMapStringsSep "\n" (p: "${p}/bin/${p.name} --help >/dev/null") diagnosticCommands}
  ${pkgs.lib.concatMapStringsSep "\n" (p: "test -d ${p}/bin") entries}
  python3 - <<'PY'
  import json
  from pathlib import Path
  stage = json.loads(Path('${c.programs.clash-verge.package.stageSettings}').read_text())
  browser = json.loads(Path('${h.programs.firefox.finalPackage.netsec.settings}').read_text())
  assert stage['source_root'] == '/var/lib/clash-source'
  assert stage['audit_auto_approve'] == ['node_identity', 'node_authentication', 'node_transport']
  assert '${c.systemd.timers.clash-source-refresh.timerConfig.OnUnitActiveSec}' == '30min'
  assert browser['requireProxy'] is False and browser['proxyPort'] == 18080
  ${pkgs.lib.concatMapStringsSep "\n" (p: ''
  isolated = json.loads(Path('${p.netsec.settings}').read_text())
  assert isolated['requireProxy'] is (not isolated['portalOnly'])
  assert isolated['ephemeral'] is isolated['portalOnly']
  assert isolated['profile'] != browser['profile']
  '') entries}
  policy = json.loads(Path('${if c.my.proxy.runtimeRouting then c.my.proxy.routingPolicies.rules else c.my.proxy.activeManagedPolicy}').read_text())
  assert policy['tcp-concurrent'] is True
  assert policy['interface-name'] == '${c.my.proxy.coreTopology.peer}' == 'lf-host'
  sources = json.loads(Path('${c.my.proxy.sourceSettings}').read_text())
  assert 'connectivity-check.ubuntu.com' in sources['forced_domains']
  assert 'www.baidu.com' not in sources['forced_domains']
  assert policy['lf-private-targets']['subscription-trust'] == 'fixed'
  assert policy['lf-private-targets']['strict-inlets'] == ['lf-strict']
  assert policy['listeners'][0]['port'] == ${toString c.my.machine.ports.mihomoStrict}
  transport = policy['lf-dns-transport']['dns']
  domestic = ${builtins.toJSON (map (ip: "https://${ip}/dns-query#DIRECT") c.my.machine.mihomoDomesticValidatingDnsUpstreams)}
  assert transport['nameserver'] == ['https://1.1.1.1/dns-query#lf-remote', 'https://8.8.8.8/dns-query#lf-remote']
  assert transport['nameserver-policy'] == {'geosite:cn': domestic}
  assert transport['proxy-server-nameserver'] == ['https://223.5.5.5/dns-query']
  for role, path in ${builtins.toJSON (pkgs.lib.mapAttrs (_: path: toString path) c.my.proxy.coreValidatorConfigs)}.items():
      validator = Path(path).read_text()
      fields = dict(line.strip().split(': ', 1) for line in validator.splitlines() if ': ' in line)
      assert fields['forward-tcp-upstream'] == 'yes' and fields['forward-first'] == 'no'
      assert fields['forward-addr'] == '127.0.0.1@' + {
          'application': '${toString c.my.machine.ports.coreDnsTransport}',
          'infrastructure': '${toString c.my.machine.ports.coreDnsBootstrapTransport}',
      }[role]
      assert fields['module-config'] == '"validator iterator"' and fields['val-permissive-mode'] == 'no'
      assert fields['ignore-cd-flag'] == fields['harden-dnssec-stripped'] == 'yes'
      assert 'cache-min-ttl' not in fields and 'cache-min-negative-ttl' not in fields
      if role == 'application':
          assert fields['msg-cache-size'] == '16m' and fields['rrset-cache-size'] == '32m'
          assert fields['prefetch'] == fields['serve-expired'] == 'yes'
          assert fields['serve-expired-ttl'] == '300' and fields['serve-expired-ttl-reset'] == 'no'
          assert fields['serve-expired-reply-ttl'] == '30'
          assert fields['serve-expired-client-timeout'] == '${toString c.my.proxy.dnsStaleWaitMilliseconds}'
          assert fields['ede-serve-expired'] == 'yes'
      else:
          assert fields['msg-cache-size'] == '4m' and fields['rrset-cache-size'] == '8m'
          assert fields['serve-expired'] == 'no' and 'prefetch' not in fields
      assert 'dot.360.cn' not in validator and '101.226.4.6' not in validator
  PY
  touch "$out"
''
