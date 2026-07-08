final: prev: {
  bilibili = final.callPackage ./bilibili-appimage.nix { };
  airi = final.callPackage ./airi-appimage.nix { };
}

