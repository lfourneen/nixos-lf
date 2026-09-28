{ lib
, stdenv
, fetchFromGitHub
, pkg-config
, dbus
}:

stdenv.mkDerivation {
  pname = "wemeet-cursor-hook";
  version = "unstable-2026-09-27";

  src = fetchFromGitHub {
    owner = "OneZ3r0";
    repo = "wemeet-cursor-hook";
    rev = "bd1ccf0f7bae72307fd7dd6711d8a73487e18423";
    hash = "sha256-+/A3z3FU2KQmlUithVcsTrKAU6dgwVX595Dg2tCAOP4=";
  };

  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ dbus ];

  preBuild = ''
    rm -f libwemeet-cursor-hook.so
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 libwemeet-cursor-hook.so $out/lib/libwemeet-cursor-hook.so
    runHook postInstall
  '';

  meta = {
    description = "LD_PRELOAD hook forcing an embedded cursor for wemeet ScreenCast on niri";
    homepage = "https://github.com/OneZ3r0/wemeet-cursor-hook";
    platforms = lib.platforms.linux;
  };
}

