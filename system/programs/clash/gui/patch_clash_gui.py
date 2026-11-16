#!/usr/bin/env python3
"""Keep TUN and configuration activation under the system controller."""

from pathlib import Path
import sys


def replace(root, name, old, new):
    path = root / name
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f'GUI source anchor changed: {name}: {old[:80]!r}')
    path.write_text(text.replace(old, new, 1))


def section(root, name, start, end, new):
    text = (root / name).read_text()
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f'GUI section changed: {name}')
    replace(root, name, text[text.index(start):text.index(end)], new)


def main():
    root = Path(sys.argv[1])
    tun = sys.argv[2]
    if tun not in ('true', 'false'):
        raise ValueError('expected the declared TUN boolean')
    hook = 'src/hooks/use-system-state.ts'
    replace(root, hook, "import { useEffect, useRef, useState }", "import { useEffect, useState }")
    replace(root, hook, "import { showNotice } from '@/services/notice-service'\n", '')
    replace(root, hook, "import { useVerge } from './use-verge'\n", '')
    replace(root, hook, '  const { verge, patchVerge } = useVerge()\n', '')
    replace(root, hook, '  const disablingTunRef = useRef(false)\n', '')
    section(root, hook, '  const enable_tun_mode = verge?.enable_tun_mode', '\n  return {', '')

    switch = 'src/components/shared/proxy-control-switches.tsx'
    replace(root, switch, 'disabled={!isTunModeAvailable}', 'disabled={true}')
    tray = 'src-tauri/src/core/tray/mod.rs'
    replace(root, tray, '        tun_mode_available,\n        tun_mode_enabled,',
            '        false,\n        tun_mode_enabled,')

    init = 'src-tauri/src/config/config.rs'
    section(root, init, '        // init Tun mode', '\n        Ok(())\n    }\n\n    pub async fn init_runtime_config',
            f'''        let verge = Self::verge().await;
        verge.edit_draft(|d| d.enable_tun_mode = Some({tun}));
        verge.apply();
        verge.latest_arc().save_file().await?;
''')
    preferences = 'src-tauri/src/config/verge.rs'
    replace(root, preferences, '                    // compatibility',
            f'                    config.enable_tun_mode = Some({tun});\n                    // compatibility')
    replace(root, preferences, '            app_log_max_size: Some(128),',
            f'            enable_tun_mode: Some({tun}),\n            app_log_max_size: Some(128),')
    replace(root, 'src-tauri/src/feat/config.rs',
            'pub async fn patch_verge(patch: &IVerge, not_save_file: bool) -> Result<()> {',
            f'''pub async fn patch_verge(patch: &IVerge, not_save_file: bool) -> Result<()> {{
    if patch.enable_tun_mode.is_some_and(|value| value != {tun}) {{
        anyhow::bail!("TUN is managed by the system controller");
    }}
    let mut managed_patch = patch.clone();
    managed_patch.enable_tun_mode = None;
    let patch = &managed_patch;''')

    window = 'src-tauri/src/feat/window.rs'
    section(root, window, '        logging!(info, Type::System, "disable tun");',
            '        #[cfg(target_os = "windows")]\n        let stop_timeout', '')

    activation = 'src-tauri/src/core/manager/config.rs'
    section(root, activation, '            Err(err) => {\n                logging!(',
            '\n        }\n    }\n\n    async fn reload_config',
            '''            Err(err) => {
                Config::runtime().await.discard();
                Err(anyhow!("System controller refused configuration reload: {err}"))
            }''')


if __name__ == '__main__':
    main()
