# local-apps

Put local-only binaries here (not tracked by git).

Expected:

- `bilibili-<version>-x86_64.AppImage` — version per `overlays/bilibili-appimage.nix`.
  Missing → falls back to nixpkgs' bilibili.
- `DSH-NEXT-<version>-next-linux-x64.tar.gz` — version per `overlays/dsh-desktop.nix`.
  Missing → hard error. Download the official Linux tarball from the upstream
  releases page and copy it here (no local build/AppImage step any more).
- `waywallen-<version>-x86_64.AppImage` — version per `overlays/waywallen-appimage.nix`.
  Missing → hard error. Download from the upstream releases page and copy here.

---

本地大文件放这里（不纳入 git）。

需要的文件：

- `bilibili-<version>-x86_64.AppImage` — 版本以 `overlays/bilibili-appimage.nix` 为准。
  缺失时回退到 nixpkgs 的 bilibili。
- `DSH-NEXT-<version>-next-linux-x64.tar.gz` — 版本以 `overlays/dsh-desktop.nix` 为准。
  缺失时直接报错。从上游 releases 页面下载官方 Linux 压缩包后拷到这里即可
  （不再需要本地构建 AppImage）。
- `waywallen-<version>-x86_64.AppImage` — 版本以 `overlays/waywallen-appimage.nix` 为准。
  缺失时直接报错。从上游 releases 页面下载后拷到这里。
