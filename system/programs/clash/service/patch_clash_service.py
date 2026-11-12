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

READINESS = '''#[derive(Debug)]
struct ManagedStartError { exit_code: Option<i32>, timeout: bool }

impl std::fmt::Display for ManagedStartError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let reason = if self.exit_code == Some(78) { "configuration refused" }
            else if self.timeout { "readiness timeout" } else { "core exited before readiness" };
        write!(f, "managed core start failed: exit code {:?}; {}", self.exit_code, reason)
    }
}
impl std::error::Error for ManagedStartError {}

#[derive(Clone)]
struct ManagedStartMemo {
    signature: String,
    completed: Instant,
    outcome: std::result::Result<(), String>,
    pid: Option<u32>,
}

fn managed_start_signature(config: &ClashConfig) -> Option<String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        std::env::var_os("LF_CLASH_CORE_PATH")?;
        let fixed = std::env::var("LF_CLASH_GUI_CONFIG").ok()?;
        if config.core_config.config_path != fixed { return None; }
        let info = std::fs::symlink_metadata(fixed).ok()?;
        if !info.is_file() { return None; }
        // Metadata identifies retries only. Stage still performs all safe input,
        // ownership, parser and digest checks on every actual new launch.
        return Some(format!("{}:{}:{}:{}:{}:{}:{}:{}",
            serde_json::to_string(config).ok()?, info.dev(), info.ino(), info.len(),
            info.mtime(), info.mtime_nsec(), info.ctime(), info.ctime_nsec()));
    }
    #[cfg(not(unix))]
    { let _ = config; None }
}

#[cfg(unix)]
fn stop_managed_children(child: &Child) {
    if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
        if let Some(pid) = child.id() {
            // An unreaped child owns this new process group; no stored or
            // caller-supplied PID is used for privileged process operations.
            unsafe { platform_lib::kill(-(pid as i32), platform_lib::SIGKILL); }
        }
    }
}

#[cfg(unix)]
async fn wait_managed_ready(guard: &mut ChildGuard) -> Result<()> {
    if std::env::var_os("LF_CLASH_CORE_PATH").is_none() { return Ok(()); }
    wait_spawn_ready(guard, Duration::from_secs(15)).await
}

#[cfg(unix)]
async fn wait_spawn_ready(guard: &mut ChildGuard, budget: Duration) -> Result<()> {
    let deadline = Instant::now() + budget;
    loop {
        let child = guard.inner().ok_or_else(|| anyhow!("managed child missing"))?;
        if let Some(status) = child.try_wait()? {
            return Err(ManagedStartError { exit_code: status.code(), timeout: false }.into());
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            // The unreaped child is the leader of the group created at spawn.
            // Its PID cannot be reused here. Stop its stage/validator children
            // too, so a timed-out launch cannot commit a later configuration.
            if let Some(pid) = child.id() {
                unsafe { platform_lib::kill(-(pid as i32), platform_lib::SIGKILL); }
            }
            return Err(ManagedStartError { exit_code: None, timeout: true }.into());
        }
        if matches!(tokio::time::timeout(remaining.min(Duration::from_millis(50)),
            tokio::net::UnixStream::connect("/run/clash-verge-rev/core/mihomo.sock")).await, Ok(Ok(_))) {
            if let Some(status) = child.try_wait()? {
                return Err(ManagedStartError { exit_code: status.code(), timeout: false }.into());
            }
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(50).min(remaining)).await;
    }
}

#[cfg(all(test, target_os = "linux"))]
#[tokio::test]
async fn managed_readiness_timeout_cleans_descendant_pipes() {
    use tokio::io::AsyncReadExt;
    let mut child = Command::new("sh").args(["-c", "sleep 30 & wait"])
        .process_group(0).stdout(Stdio::piped()).spawn().unwrap();
    let mut output = child.stdout.take().unwrap();
    let mut guard = ChildGuard { child: Some(child), readers: Vec::new() };
    let error = wait_spawn_ready(&mut guard, Duration::from_millis(100)).await.unwrap_err();
    assert!(error.to_string().contains("readiness timeout"));
    let mut bytes = Vec::new();
    tokio::time::timeout(Duration::from_secs(1), output.read_to_end(&mut bytes))
        .await.expect("launcher descendants kept running").unwrap();
    guard.inner().unwrap().wait().await.unwrap();
}

'''


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

    # Wait in the common spawn path, covering /clash/start, desired-state
    # restore and watchdog restarts. No caller-supplied IPC path is touched.
    text = replace_once(text, 'pub struct ChildGuard {', READINESS + 'pub struct ChildGuard {')
    text = replace_once(text, 'pub struct CoreManager {',
        'pub struct CoreManager {\n    managed_start_memo: Mutex<Option<ManagedStartMemo>>,')
    text = replace_once(text, '        CoreManager {',
        '        CoreManager {\n            managed_start_memo: Mutex::new(None),')
    text = replace_once(text,
        '''            .pre_exec(|| {
                platform_lib::umask(0o007);''',
        '''            .process_group(0)
            .pre_exec(|| {
                platform_lib::umask(0o007);''')
    kill_anchor = 'if let Err(e) = child.kill().await {'
    if text.count(kill_anchor) != 2:
        raise ValueError('upstream child cleanup anchors changed')
    text = text.replace(kill_anchor,
        '#[cfg(unix)]\n            stop_managed_children(&child);\n            ' + kill_anchor)
    text = replace_once(text, '    Ok(child_guard)',
        '''    #[cfg(unix)]
    wait_managed_ready(&mut child_guard).await?;
    Ok(child_guard)''')
    text = replace_once(text,
        '''        let child_guard =
            run_with_logging(&config.core_config.core_path, &args, &config.log_config).await?;''',
        '''        let child_guard = match
            run_with_logging(&config.core_config.core_path, &args, &config.log_config).await {
            Ok(guard) => guard,
            Err(error) => {
                *self.managed_start_memo.lock().await = signature.map(|signature| ManagedStartMemo {
                    signature, completed: Instant::now(), outcome: Err(error.to_string()), pid: None,
                });
                if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
                    *self.last_core_exit_reason.lock().await = Some(error.to_string());
                    set_service_lifecycle_state(ServiceLifecycleState::Fatal);
                }
                return Err(error);
            }
        };''')
    text = replace_once(text,
        '''                let now = Instant::now();
                restart_timestamps''',
        '''                if std::env::var_os("LF_CLASH_CORE_PATH").is_some() && status.code() == Some(78) {
                    *last_exit_reason_arc.lock().await = Some("exit code 78; configuration refused".into());
                    error!("Managed configuration refused (exit code 78); retries stopped");
                    set_service_lifecycle_state(ServiceLifecycleState::Fatal);
                    break 'watchdog;
                }
                let now = Instant::now();
                restart_timestamps''')
    text = replace_once(text,
        '''                            error!("Failed to restart core: {}", error);''',
        '''                            error!("Failed to restart core: {}", error);
                            if error.downcast_ref::<ManagedStartError>().is_some_and(|e| e.exit_code == Some(78)) {
                                *last_exit_reason_arc.lock().await = Some(error.to_string());
                                set_service_lifecycle_state(ServiceLifecycleState::Fatal);
                                break 'watchdog;
                            }''')

    # No managed request may make root chmod/unlink a caller-chosen path.
    # The launcher/core/desktop forwarder own the two socket lifecycles.
    text = replace_once(text,
        'pub async fn start_core(&self, config: ClashConfig) -> Result<()> {',
        '''pub async fn start_core(&self, mut config: ClashConfig) -> Result<()> {
        if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
            config.core_config.core_ipc_path = "/run/clash-verge-rev/core/mihomo.sock".into();
        }
        let signature = managed_start_signature(&config);
        let memo = self.managed_start_memo.lock().await.clone();
        if !std::path::Path::new("/run/proxy-mode/authorize-window").exists() {
            if let Some(memo) = memo.filter(|memo| signature.as_ref() == Some(&memo.signature)
                && memo.completed.elapsed() < Duration::from_secs(30)) {
                match memo.outcome {
                    Err(error) => return Err(anyhow!(error)),
                    Ok(()) => {
                        #[cfg(unix)]
                        if memo.pid.is_some() && memo.pid == Some(self.running_pid.load(Ordering::Relaxed))
                            && matches!(tokio::time::timeout(Duration::from_millis(50),
                                tokio::net::UnixStream::connect("/run/clash-verge-rev/core/mihomo.sock")).await, Ok(Ok(_))) {
                            return Ok(());
                        }
                    }
                }
            }
        }''')
    text = replace_once(text,
        '        self.start_watchdog(child_guard, config).await;',
        '''        self.start_watchdog(child_guard, config).await;
        *self.managed_start_memo.lock().await = signature.map(|signature| ManagedStartMemo {
            signature, completed: Instant::now(), outcome: Ok(()), pid: child_pid,
        });''')
    text = replace_once(text, '    pub async fn stop_core(&self) -> Result<()> {',
        '''    pub async fn stop_core(&self) -> Result<()> {
        self.managed_start_memo.lock().await.take();''')
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

    # Kode Bridge's default five-second handler budget cancels the managed
    # readiness wait before its own fifteen-second deadline can report errors.
    path = root / 'src/core/server.rs'
    text = replace_once(path.read_text(),
        'let server = IpcHttpServer::new(paths.ipc_path())?;',
        '''let server = if std::env::var_os("LF_CLASH_CORE_PATH").is_some() {
        IpcHttpServer::with_config(paths.ipc_path(), kode_bridge::ipc_http_server::ServerConfig {
            write_timeout: Duration::from_secs(20),
            ..Default::default()
        })?
    } else { IpcHttpServer::new(paths.ipc_path())? };''')
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
