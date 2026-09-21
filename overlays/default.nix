final: prev:
{
  # bilibili-appimage.nix owns the AppImage version and falls back to nixpkgs'
  # bilibili when the local file is missing, so no version lives here.
  bilibili = final.callPackage ./bilibili-appimage.nix {
    fallback = prev.bilibili;
  };

  dsh-desktop = final.callPackage ./dsh-desktop-appimage.nix { };

  waywallen-layer-shell = final.callPackage ./waywallen-layer-shell.nix { };
}

