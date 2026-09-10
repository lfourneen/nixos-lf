final: prev:
let
  appimage = ./local-apps/bilibili-1.18.0-x86_64.AppImage;

in
prev.lib.optionalAttrs (builtins.pathExists appimage) {
  bilibili = final.callPackage ./bilibili-appimage.nix { };
}

