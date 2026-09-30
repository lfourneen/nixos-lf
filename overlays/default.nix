final: prev:
{
  # bilibili-appimage.nix owns the AppImage version and falls back to nixpkgs'
  # bilibili when the local file is missing, so no version lives here.
  bilibili = final.callPackage ./bilibili-appimage.nix {
    fallback = prev.bilibili;
  };

  dsh-desktop = final.callPackage ./dsh-desktop-appimage.nix { };

  waywallen = final.callPackage ./waywallen-appimage.nix { };

  wemeet-cursor-hook = final.callPackage ./wemeet-cursor-hook.nix { };

  niri = prev.niri.overrideAttrs (old: {
    doCheck = false;

    patches = (old.patches or [ ]) ++ [
      (prev.fetchurl {
        name = "niri-shm-screencast.diff";
        url = "https://github.com/niri-wm/niri/pull/1791.diff";
        hash = "sha256-s8pciMdiGahY6fsA87ZxeRwFA7LwIZ4/cb5OZD64baA=";
      })
    ];
  });
}

