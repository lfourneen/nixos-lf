#!/usr/bin/env python3
"""Pin the clash-verge-service core spawn to the managed launcher.

The packaged helper (2.3.3) executes the client-supplied `core_path` as root
without any ownership or allow-list check. This patcher installs a single
validation at `run_with_logging`, the only place that spawns the core, and
requires the canonical path to equal `LF_CLASH_CORE_PATH` when that variable is
set. Root-owned sticky ancestors (notably /nix/store) are accepted; writable
files and non-sticky writable ancestors are rejected. The service unit sets
it to the packaged launcher; the upstream test
suite runs without it and keeps its fixture paths.
"""

from __future__ import annotations

import pathlib
import sys

FUNCTION = '''#[cfg(unix)]
fn validate_core_path(bin_path: &str) -> Result<()> {
    use std::os::unix::fs::MetadataExt;

    // The managed deployment pins the launcher with LF_CLASH_CORE_PATH. Other
    // builds (including the upstream test suite) keep the unmanaged behaviour.
    let expected = match std::env::var("LF_CLASH_CORE_PATH") {
        Ok(value) if !value.is_empty() => value,
        _ => return Ok(()),
    };

    let path = std::path::Path::new(bin_path);
    if !path.is_absolute() {
        return Err(anyhow!("core path must be absolute: {bin_path}"));
    }

    let canonical = std::fs::canonicalize(path)
        .map_err(|err| anyhow!("core path cannot be resolved ({bin_path}): {err}"))?;
    let expected = std::fs::canonicalize(&expected).map_err(|err| {
        anyhow!("configured core path cannot be resolved ({expected}): {err}")
    })?;
    if canonical != expected {
        return Err(anyhow!(
            "core path is not the configured launcher: {}",
            canonical.display()
        ));
    }

    let metadata = std::fs::metadata(&canonical)
        .map_err(|err| anyhow!("core path cannot be inspected ({}): {err}", canonical.display()))?;
    if !metadata.is_file() {
        return Err(anyhow!("core path is not a regular file: {}", canonical.display()));
    }

    for component in canonical.ancestors() {
        let metadata = std::fs::metadata(component).map_err(|err| {
            anyhow!(
                "core path component cannot be inspected ({}): {err}",
                component.display()
            )
        })?;
        if metadata.uid() != 0 {
            return Err(anyhow!(
                "core path component is not owned by root: {}",
                component.display()
            ));
        }
        // /nix/store is root-owned and group-writable with the sticky bit.
        // A sticky directory cannot let another UID replace the root-owned
        // next component we inspect. Writable non-sticky ancestors remain
        // forbidden, as do writable regular files.
        if metadata.mode() & 0o022 != 0
            && !(metadata.is_dir() && metadata.mode() & 0o1000 != 0)
        {
            return Err(anyhow!(
                "core path component is writable by group or other: {}",
                component.display()
            ));
        }
    }

    Ok(())
}

'''

CALL_ANCHOR = "    set_or_update_writer(writer_config).await?;"
FUNCTION_ANCHOR = "pub async fn run_with_logging("
CALL = "    #[cfg(unix)]\n    validate_core_path(bin_path)?;\n\n" + CALL_ANCHOR


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    path = root / "src/core/manager.rs"
    if not path.is_file():
        print(f"patch_clash_service: missing {path}", file=sys.stderr)
        return 1

    text = path.read_text()
    if "fn validate_core_path(" in text:
        print(f"patch_clash_service: {path} is already patched", file=sys.stderr)
        return 1

    if text.count(FUNCTION_ANCHOR) != 1:
        print(
            f"patch_clash_service: expected one {FUNCTION_ANCHOR!r} anchor, "
            f"found {text.count(FUNCTION_ANCHOR)}",
            file=sys.stderr,
        )
        return 1
    if text.count(CALL_ANCHOR) != 1:
        print(
            f"patch_clash_service: expected one spawn anchor, found {text.count(CALL_ANCHOR)}",
            file=sys.stderr,
        )
        return 1

    text = text.replace(FUNCTION_ANCHOR, FUNCTION + FUNCTION_ANCHOR, 1)
    text = text.replace(CALL_ANCHOR, CALL, 1)
    def replace_once(value, old, new):
        if value.count(old) != 1:
            raise ValueError(f"upstream helper anchor changed: {old!r}")
        return value.replace(old, new, 1)

    # No managed request may make root chmod/unlink a caller-chosen path.
    # The launcher/core/desktop forwarder own the two socket lifecycles.
    text = replace_once(text,
        'pub async fn start_core(&self, config: ClashConfig) -> Result<()> {',
        '''pub async fn start_core(&self, mut config: ClashConfig) -> Result<()> {
        if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
            config.core_config.core_ipc_path = "/run/clash-verge-rev/core/mihomo.sock".into();
        }''')
    text = replace_once(text,
        'fn after_start(&self, core_ipc_path: String) {',
        '''fn after_start(&self, core_ipc_path: String) {
        if std::env::var_os("LF_CLASH_CORE_PATH").is_some() { return; }''')
    text = replace_once(text,
        'async fn after_stop(&self, core_ipc_path: Option<String>) {',
        '''async fn after_stop(&self, core_ipc_path: Option<String>) {
        if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
            LOGGER_MANAGER.clear_logs().await;
            return;
        }''')
    path.write_text(text)

    # Both /clash/start and /writer, including desired-state restores, pass
    # through this logger constructor. Never open user-owned log symlinks as
    # root: they can target authorize-window and mint a fresh import grant.
    path = root / 'src/core/logger.rs'
    text = replace_once(path.read_text(),
        'pub fn service_writer(config: &WriterConfig) -> Result<FileLogWriter> {',
        '''pub fn service_writer(config: &WriterConfig) -> Result<FileLogWriter> {
    let config = if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
        WriterConfig {
            directory: "/var/lib/clash-verge-service/logs".into(),
            max_log_size: 10 * 1024 * 1024,
            max_log_files: 8,
        }
    } else { config.clone() };''')
    path.write_text(text)

    # systemd owns all managed child processes. Old runtime records may contain
    # caller-provided paths/PIDs from the previous version; do not act on them.
    path = root / 'src/core/reconcile.rs'
    text = replace_once(path.read_text(),
        'pub async fn reconcile_service_startup() -> Result<()> {',
        '''pub async fn reconcile_service_startup() -> Result<()> {
    if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
        remove_core_runtime_record().await;
        return Ok(());
    }''')
    path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
