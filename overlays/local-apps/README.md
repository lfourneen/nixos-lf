# local-apps

Put local-only binaries here (not tracked by git).

Expected: a Bilibili Linux AppImage named `bilibili-<version>-x86_64.AppImage`,
matching the `version`/`src` in `overlays/bilibili-appimage.nix`.
If it is missing, the custom package is skipped (falls back to nixpkgs' bilibili) and the build still succeeds.

These files are large and personal, so they are gitignored.

---

本地大文件放这里（不纳入 git）。

需要的文件：名为 `bilibili-<version>-x86_64.AppImage` 的哔哩哔哩 Linux AppImage，
版本与路径以 `overlays/bilibili-appimage.nix` 里的 `version`/`src` 为准。
缺失时会跳过该自定义包（回退到 nixpkgs 的 bilibili），不影响构建。

这些文件体积大且属于个人使用，因此被 `.gitignore` 忽略。
