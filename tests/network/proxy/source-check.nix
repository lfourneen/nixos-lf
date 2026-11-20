{ pkgs }:

pkgs.runCommand "controlled-source-checks" {
  nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml p.dnspython ])) ];
} ''
  export PYTHONDONTWRITEBYTECODE=1
  export SOURCE_AUDIT=${../../../system/programs/clash/service/subscription_audit.py}
  export SOURCE_BROKER=${../../../system/programs/clash/service/source_broker.py}
  export SOURCE_WORKER=${../../../system/programs/clash/service/source_worker.py}
  python3 ${./test_source.py} ${../../../system/programs/clash/service/source_worker.py}
  python3 ${./test_source_registry.py} ${../../../system/programs/clash/service/source_broker.py} ${../../../system/programs/clash/service/source_worker.py}
  python3 ${./test_source_review.py} ${../../../system/programs/clash/service/source_review.py} ${../../../system/programs/clash/service/source_broker.py}
  python3 ${./test_source_refresh.py} ${../../../system/programs/clash/service/clash-verge-stage.py} \
    ${../../../system/programs/clash/service/source_refresh.py} ${../../../system/config/network/proxy/state.py}
  python3 ${./test_isolation.py} ${../../../home/programs/firefox/network.py}
  touch "$out"
''
