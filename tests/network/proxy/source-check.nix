{ pkgs }:

pkgs.runCommand "controlled-source-checks" {
  nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) ];
} ''
  export PYTHONDONTWRITEBYTECODE=1
  export SOURCE_AUDIT=${../../../system/programs/clash/service/subscription_audit.py}
  python3 ${./test_source.py} ${../../../system/programs/clash/service/source_worker.py}
  python3 ${./test_source_registry.py} ${../../../system/programs/clash/service/source_broker.py} ${../../../system/programs/clash/service/source_worker.py}
  python3 ${./test_isolation.py} ${../../../home/programs/firefox/network.py}
  touch "$out"
''
