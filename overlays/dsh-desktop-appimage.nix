{ pkgs, ... }:

let
  pname = "dsh-desktop";
  version = "2.0.13";
  src = ./local-apps/DSH-Desktop-${version}-x86_64.AppImage;

  # Single extraction, used both as the runtime AppDir and as the source for the
  # desktop entry and icons.
  #
  # postExtract applies one vendor patch without which every plugin operation
  # fails on Linux. `dsh-subprocess-local` launches its runner as `<electron>
  # <runner.js>`, and the runner's entry guard is `if (import.meta.main)`.
  # `import.meta.main` is a Node feature; in Electron's GUI main process it is
  # falsy, so the runner exits 0 without consuming the launch request and the
  # caller reports "subprocess scope exited before its bootstrap consumed the
  # launch request" (exit 127). The package already forces
  # ELECTRON_RUN_AS_NODE=1 for this reason, but only on win32, so the Linux
  # desktop app can never install, update, or roll back a plugin.
  #
  # `--replace-fail` is deliberate: if a future AppImage changes this line the
  # build fails loudly instead of silently shipping a broken market.
  appDir = pkgs.appimageTools.extract {
    inherit pname version src;
    postExtract = ''
      substituteInPlace $out/resources/app/node_modules/@deepseek-ai/dsh-subprocess-local/lib/runner-launch-*.js \
        --replace-fail \
        'if (selection === WINDOWS_RUNNER_SELECTION && process.platform === "win32" && process.versions.electron !== void 0) {' \
        'if (process.versions.electron !== void 0) {'
    '';
  };

in
# Single source of truth for the local AppImage version. There is no nixpkgs
# package to fall back to, so a missing file is a hard error instead of a
# silently missing package. See overlays/local-apps/README.md for the filename.
if !builtins.pathExists src then
  throw "dsh-desktop: ${toString src} is missing; see overlays/local-apps/README.md"
else
pkgs.appimageTools.wrapAppImage {
  inherit pname version;
  src = appDir;

  # `git` for git+https plugin installs; libdbusmenu-gtk3 is a transitive
  # NEEDED of the AppDir's libappindicator3.so.1.
  extraPkgs = pkgs: with pkgs; [ git libdbusmenu-gtk3 ];

  # AppRun is not on PATH inside the FHS env, so repoint Exec at the wrapper.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/dsh-desktop.desktop \
      $out/share/applications/dsh-desktop.desktop
    substituteInPlace $out/share/applications/dsh-desktop.desktop \
      --replace-fail 'Exec=AppRun' \
        'Exec=${pname} --ozone-platform-hint=auto --enable-wayland-ime=true'

    # Copy into the existing hicolor tree; `cp -r <dir> $out/share/` would nest
    # it as $out/share/icons/icons.
    mkdir -p $out/share/icons
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/
  '';

  # The bundled Electron 43.3.0 stays: native modules ship prebuilt for ABI 148
  # and the app's own pnpm shim re-execs that binary as Node. No `--no-sandbox`
  # either — AppDir chrome-sandbox is 0755, so the namespace sandbox is what
  # runs, and AppRun already adds the flag if its own probe fails.
  meta = {
    mainProgram = pname;
    description = "DSH Desktop: an Electron shell composed as a DeepSeek Harness Cordis plugin";
    homepage = "https://github.com/anywhere-labs/deepseek-harness-desktop";
    license = pkgs.lib.licenses.mit;
    platforms = pkgs.lib.platforms.linux;
  };
}

