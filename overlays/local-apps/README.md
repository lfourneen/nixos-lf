# local-apps

Put local-only binaries here (not tracked by git).

Expected:

- `bilibili-<version>-x86_64.AppImage` — version per `overlays/bilibili-appimage.nix`.
  Missing → falls back to nixpkgs' bilibili.
- `DSH-Desktop-<version>-x86_64.AppImage` — version per `overlays/dsh-desktop-appimage.nix`.
  Missing → `pkgs.dsh-desktop` is simply not defined, so `nixos-rebuild` still succeeds.
  Rebuild with `corepack yarn workspace dsh-plugin-desktop dist:linux` in the dsh-desktop
  checkout, then copy `dsh-plugin-desktop/dist/linux/*.AppImage` here.

---

本地大文件放这里（不纳入 git）。

需要的文件：

- `bilibili-<version>-x86_64.AppImage` — 版本以 `overlays/bilibili-appimage.nix` 为准。
  缺失时回退到 nixpkgs 的 bilibili。
- `DSH-Desktop-<version>-x86_64.AppImage` — 版本以 `overlays/dsh-desktop-appimage.nix` 为准。
  缺失时只是不定义 `pkgs.dsh-desktop`，`nixos-rebuild` 照常成功。
  在 dsh-desktop 仓库执行 `corepack yarn workspace dsh-plugin-desktop dist:linux` 重新构建，
  再把 `dsh-plugin-desktop/dist/linux/*.AppImage` 拷到这里。
