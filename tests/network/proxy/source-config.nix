{ pkgs, config }:

let
  c = config;
  h = c.home-manager.users.${c.my.machine.desktopUser};
  entries = builtins.filter (p: builtins.elem (p.name or "") [ "firefox-identity" "firefox-portal" ]) h.home.packages;
  command = builtins.head (builtins.filter (p: (p.pname or (p.name or "")) == "clash-source") c.environment.systemPackages);
  diagnosticCommands = builtins.filter (p: builtins.elem (p.pname or (p.name or "")) [ "clash-diff" ]) c.environment.systemPackages;
  applicationProbe = c.systemd.services.proxy-application-dns-probe.serviceConfig;

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
assert c.systemd.services.socket-guest-dns.serviceConfig.User == "socket-guest-dns";
assert c.systemd.services.unbound-infrastructure.serviceConfig.User == "unbound-infrastructure";
assert builtins.elem "socket-guest-dns.service" c.systemd.services.clash-source-refresh.after;
assert builtins.elem "clash-core" c.systemd.services.clash-source-refresh.serviceConfig.SupplementaryGroups;
assert !(builtins.elem "CAP_NET_ADMIN" c.systemd.services.clash-source-refresh.serviceConfig.CapabilityBoundingSet);
assert c.systemd.services.profile-refresh-verify.environment.LF_MANAGED_PROXY_POLICY == c.my.proxy.activeManagedPolicy;
assert c.systemd.timers.clash-source-refresh.timerConfig.Unit == "clash-source-refresh-due.service";
assert c.systemd.timers.clash-source-refresh.timerConfig.OnUnitActiveSec == "1min";
assert c.systemd.sockets.clash-source-gui.socketConfig.Accept;
assert c.systemd.sockets.clash-source-gui.socketConfig.SocketMode == "0600";
assert c.systemd.sockets.clash-source-gui.socketConfig.SocketUser == c.my.machine.desktopUser;
assert c.systemd.services."clash-source-gui@".serviceConfig.RestrictAddressFamilies == [ "AF_UNIX" ];
assert c.systemd.sockets.clash-source-gui-http.socketConfig.ListenStream == "127.0.0.1:45129";
assert c.systemd.services."clash-source-gui-http@".serviceConfig.IPAddressAllow == "localhost";
assert c.systemd.paths.clash-source-download.pathConfig.PathExists == "/run/proxy-mode/source-refresh-ready";
assert c.systemd.paths.clash-source-apply.pathConfig.PathExists == "/run/proxy-mode/source-apply.json";
assert c.systemd.services.clash-source-guard.serviceConfig.CapabilityBoundingSet == [ "CAP_NET_ADMIN" ];
assert c.systemd.services.clash-source-guard.serviceConfig.NoNewPrivileges;
assert applicationProbe.User == c.my.machine.desktopUser;
assert applicationProbe.Slice == "untrusted.slice" && applicationProbe.IPAddressDeny == "any";
assert applicationProbe.NoNewPrivileges && applicationProbe.CapabilityBoundingSet == "";
assert builtins.length diagnosticCommands == 1;
assert !(builtins.any (p: (p.pname or (p.name or "")) == "clash-apply") c.environment.systemPackages);
assert !(pkgs.lib.hasInfix "clash-apply" h.home.file.".brushrc".text);

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
  assert browser['requireProxy'] is False and browser['proxyPort'] == 18080
  ${pkgs.lib.concatMapStringsSep "\n" (p: ''
  isolated = json.loads(Path('${p.netsec.settings}').read_text())
  assert isolated['requireProxy'] is (not isolated['portalOnly'])
  assert isolated['ephemeral'] is isolated['portalOnly']
  assert isolated['profile'] != browser['profile']
  '') entries}
  policy = json.loads(Path('${if c.my.proxy.runtimeRouting then c.my.proxy.routingPolicies.rules else c.my.proxy.activeManagedPolicy}').read_text())
  assert policy['tcp-concurrent'] is True
  assert policy['profile.store-selected'] is True
  assert policy['interface-name'] == '${c.my.proxy.coreTopology.peer}' == 'lf-host'
  sources = json.loads(Path('${c.my.proxy.sourceSettings}').read_text())
  assert stage['renderer_settings'] == {
      key: sources[key] for key in ('forced_domains', 'probe_domains', 'remote_only_catchall')
  }
  assert 'connectivity-check.ubuntu.com' in sources['forced_domains']
  assert 'www.baidu.com' not in sources['forced_domains']
  routing = json.loads(Path('${c.my.proxy.routingSettings}').read_text())
  audit = json.loads((Path(routing['artifacts']['rules']) / 'audit.json').read_text())
  assert audit['protected_routes']['cp.cloudflare.com'] == 'proxy'
  assert audit['protected_routes']['connectivity-check.ubuntu.com'] == 'proxy'
  assert 'www.baidu.com' not in audit['protected_routes']
  assert policy['lf-private-targets']['subscription-trust'] == 'fixed'
  assert policy['lf-private-targets']['require-protected-node-transport'] is True
  assert policy['lf-private-targets']['strict-inlets'] == ['lf-strict']
  assert policy['listeners'][0]['port'] == ${toString c.my.machine.ports.mihomoStrict}
  assert policy['lf-private-targets']['delay-test-url'] == ${builtins.toJSON c.my.proxy.delayTestUrl}
  assert 'https://www.gstatic.com/generate_204' in policy['lf-private-targets']['health-test-urls']
  transport = policy['lf-dns-transport']['dns']
  domestic = ${builtins.toJSON (map (ip: "https://${ip}/dns-query#DIRECT") c.my.machine.mihomoDomesticValidatingDnsUpstreams)}
  assert transport['nameserver'] == ['https://1.1.1.1/dns-query#lf-dns', 'https://8.8.8.8/dns-query#lf-dns']
  assert transport['nameserver-policy'] == {'geosite:cn': domestic}
  assert policy['lf-dns-transport']['validation-nameserver'] == domestic
  assert transport['proxy-server-nameserver'] == ['https://223.5.5.5/dns-query']
  assert policy['dns.proxy-server-nameserver'] == ['tcp://127.0.0.1:${toString c.my.machine.ports.socketGuestDns}#DIRECT']
  strict = json.loads(Path('${c.my.proxy.routingPolicies.proxy-only}').read_text())
  assert strict['lf-private-targets']['single-egress'] is True
  assert strict['lf-private-targets']['require-protected-node-transport'] is True
  assert routing['validator_port'] != routing['infrastructure_validator_port']
  assert routing['real_dns_port'] != routing['infrastructure_dns_port']
  for role in ('nameserver', 'proxy-server-nameserver'):
      assert strict['lf-dns-transport']['dns'][role] == [
          'https://1.1.1.1/dns-query#lf-remote', 'https://8.8.8.8/dns-query#lf-remote']
  assert strict['lf-dns-transport']['validation-nameserver'] == []
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
