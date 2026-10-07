# Agently CLI (Tencent QQ Mail "Agent Mail"). Upstream ships a statically
# linked linux-x64 Go binary through an npm platform package; fetch that tarball
# directly so the agent gets the CLI without runtime npm (allow_lazy_installs is
# off).
#
# Two things here can drift from upstream: the CLI version and the vendored
# skill. `passthru.check` (exposed as the `agently-check` command) compares both
# against upstream so an update is never silent. Bump `version`/`hash` and
# replace `agently-mail/SKILL.md` together when it reports drift.
{
  lib,
  stdenvNoCC,
  fetchurl,
  writeShellApplication,
  curl,
  coreutils,
  gnugrep,
}:

let
  version = "1.0.18";

  # The checker hashes the *deployed* skill, not the repo or /etc/nixos: Hermes
  # installs it with `install -D` (a real copy, not a symlink) at
  # $HERMES_HOME/skills/agently-mail/SKILL.md, which is exactly what the agent
  # loads. HERMES_HOME defaults to the NixOS service home; override the file
  # with AGENTLY_SKILL_FILE.
  check = writeShellApplication {
    name = "agently-check";
    runtimeInputs = [ curl coreutils gnugrep ];
    text = ''
      skill_file="''${AGENTLY_SKILL_FILE:-''${HERMES_HOME:-/var/lib/hermes/.hermes}/skills/agently-mail/SKILL.md}"
      skill_url="https://agent.qq.com/.well-known/skills/agently-mail/SKILL.md"
      npm_url="https://registry.npmjs.org/@tencent-qqmail%2Fagently-cli/latest"
      pinned=${lib.escapeShellArg version}

      proxy_args=()
      if [ -n "''${AGENTLY_CHECK_PROXY:-}" ]; then
        proxy_args=(-x "$AGENTLY_CHECK_PROXY")
      fi

      echo "== agently-cli =="
      echo "current:  $pinned"
      if latest=$(
        curl -fsS --max-time 20 "''${proxy_args[@]}" "$npm_url" \
          | grep -o '"version":"[^"]*"' | head -n1 | cut -d'"' -f4
      ); then
        echo "latest:   $latest"
        if [ "$latest" = "$pinned" ]; then
          echo "status:   up to date"
        else
          echo "status:   UPDATE AVAILABLE ($pinned -> $latest)"
          echo "          bump version + hash in system/programs/agently-cli.nix, then rebuild"
        fi
      else
        echo "latest:   <unreachable>"
        echo "status:   cannot reach npm registry"
      fi

      echo
      echo "== agently-mail skill (deployed) =="
      echo "path:     $skill_file"
      if [ ! -r "$skill_file" ]; then
        echo "status:   not installed yet - rebuild so Hermes copies it into HERMES_HOME"
        exit 0
      fi
      local_hash=$(sha256sum "$skill_file" | cut -d' ' -f1)
      echo "local:    $local_hash"
      if upstream_hash=$(
        curl -fsS --max-time 20 "''${proxy_args[@]}" "$skill_url" | sha256sum | cut -d' ' -f1
      ); then
        echo "upstream: $upstream_hash"
        if [ "$upstream_hash" = "$local_hash" ]; then
          echo "status:   hash matches upstream"
        else
          echo "status:   CHANGED UPSTREAM"
          echo "          review: curl -s $skill_url | diff - $skill_file"
          echo "          then replace system/programs/agently-mail/SKILL.md and rebuild"
        fi
      else
        echo "upstream: <unreachable>"
        echo "status:   cannot reach $skill_url"
      fi
    '';
  };

in
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "agently-cli";
  inherit version;

  src = fetchurl {
    url = "https://registry.npmjs.org/@tencent-qqmail/agently-cli-linux-x64/-/agently-cli-linux-x64-${finalAttrs.version}.tgz";
    hash = "sha512-6+5aBFFoPtscK3jafN1jZ/OK3f4HK0dAKdmHnja71O7BiTGRAGIUe6Ygd/ysiqouPXkvedZpVbLlHxoykNgpgA==";
  };

  sourceRoot = "package";

  dontBuild = true;
  dontStrip = true;

  installPhase = ''
    runHook preInstall
    install -Dm0755 bin/agently-cli "$out/bin/agently-cli"
    runHook postInstall
  '';

  passthru = { inherit check; };

  meta = {
    description = "Agent-first mail CLI for Agently (Tencent QQ Mail)";
    homepage = "https://agent.qq.com";
    license = lib.licenses.asl20;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    mainProgram = "agently-cli";
    platforms = [ "x86_64-linux" ];
  };
})

