# Overlay and desktop package maintenance

English | [简体中文](maintenance_CN.md)

Local overlay definitions are registered in [default.nix](default.nix). AppImage and portable
packages pin upstream release assets and hashes in their package expressions; other packages
follow their supplying input in [flake.lock](../flake.lock).

| Entry | Customization / maintenance |
| --- | --- |
| [default.nix](default.nix) | Niri SHM screencast diff fetched with a fixed hash; checks disabled in this override. Compare upstream integration and exercise screencasting before retiring the diff |
| [dsh-desktop-appimage.nix](dsh-desktop-appimage.nix) | ASAR menu changes, bundled pnpm git-install behavior, unpack/executable preservation and Landlock launcher. Review exact JS anchors, bundled pnpm, sharp WASM and executable assets on each AppImage bump |
| [dsh-tui.nix](dsh-tui/dsh-tui.nix) | Prebuilt release extraction, native DSH dependency links, persistent plugin profile and FHS launcher. Check the pkg asset layout, host peer range, shared Cordis instance and plugin persistence; see [packaging and plugin guide](dsh-tui/README.md) |
| [bilibili-appimage.nix](bilibili-appimage.nix) | Desktop command replacement and Wayland launcher; verify changed desktop layout and sandbox flags |
| [wemeet-cursor-hook.nix](wemeet-cursor-hook.nix) | Pinned external cursor-hook library, built from source; verify preload/cursor behavior with compositor updates |

The related Rime package override lives in [layout.nix](../system/config/layout.nix): it installs
the Rime Ice suggestion YAML as the default. Check upstream data filenames on upgrades.

When updating a release, review its asset layout and local patches as well as its version and
hash. Keep exact-match failures so changed upstream code requires review. Build the affected
package before activation and exercise the behavior it customizes: desktop launch, screencasting,
input, or plugin installation and persistence. For packages with native addons, include their
runtime loading paths in the check.
