{ bilibili, ... }:

bilibili.overrideAttrs (old: {
  version = "1.17.6-1";
  src = ./local-apps/io.github.msojocs.bilibili_1.17.6-1_amd64.deb;
  postInstall = (old.postInstall or "") + ''
    substituteInPlace $out/share/applications/*.desktop \
      --replace-fail "StartupWMClass=" "StartupWMClass=bilibili" || true
    echo "StartupWMClass=bilibili" >> $out/share/applications/*.desktop
  '';
})

