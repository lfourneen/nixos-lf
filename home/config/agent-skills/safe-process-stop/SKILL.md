---
name: safe-process-stop
description: Use when stopping Linux processes, freeing a port, or cleaning up background jobs. Verify each PID, its process ancestry, group, and start identity before signaling it, and protect active terminals, agents, and login sessions.
---

# Stop processes by PID

Confirm that each process belongs to the target task before sending a signal. Use verified positive integer PIDs greater than 1 as targets. Use PGIDs and SIDs to identify shared process groups and sessions.

## Identify and verify the process

Inspect a known PID directly or use `pgrep` to find candidates.

```bash
pgrep -a -x 'worker'
pgrep -a -f -- '^/absolute/path/to/program( |$)'
ps -eo user,pid,ppid,pgid,sid,lstart,stat,args --forest
```

Process-name matching has a length limit. Find long names or interpreter scripts by full command line. Patterns are regular expressions and, even with `-x`, can match multiple processes. Inspect each candidate before signaling it.

Check the owner, command, start time, ancestry, and group of each candidate.

```bash
ps -p "$target_pid,$$,$PPID" -o user,pid,ppid,pgid,sid,lstart,stat,args
pstree -sp "$target_pid"
pgrep -a -P "$target_pid"
```

These shell variable examples use Bash. Follow the ancestor chain to identify the current shell, agent host, terminal, and login session. Exclude them from ordinary task cleanup. Use `ps --forest` when `pstree` is unavailable. `pgrep -P` lists direct children, so inspect the tree one level at a time when descendants are in scope. Use absolute procfs paths to distinguish tasks with the same name.

```bash
readlink -- "/proc/$target_pid/exe" "/proc/$target_pid/cwd"
cat -- "/proc/$target_pid/cgroup"
```

A verified individual PID can be signaled even if its PGID is shared. A group signal affects other members. Leave the process running if its ownership remains uncertain, and state what evidence is missing.

## Send a signal

Prefer a task-scoped shutdown or cancellation interface if the application provides one. If a signal is needed, recheck the process identity immediately before sending TERM. The Bash condition below only checks PID syntax and range. Verify the process identity beforehand.

```bash
if [[ ${target_pid:-} =~ ^[1-9][0-9]*$ && $target_pid != 1 ]]; then
  kill -TERM -- "$target_pid"
else
  printf '%s\n' 'A single verified PID greater than 1 is required' >&2
fi
```

Allow time for normal shutdown, then check the process and the port in question. Use `kill -KILL -- "$target_pid"` only if the same process remains alive, the task requires a forced stop, and its identity has been checked again. Stop escalation if the process exits, its identity changes, or verification fails.

Checking the start time and command reduces errors from PID reuse. A race remains between `ps` and `kill`. Use a pidfd interface when automation needs a stable process identity and the interface is available.

## Common mistakes

| Command or symptom | Effect and handling |
| --- | --- |
| `kill -TERM -PGID`, `kill -TERM -- -PGID`, `pkill -g`, or a shell job group such as `%1` | Targets a group or job. Verify and signal individual task PIDs. A negative PID retains its meaning after `--`. |
| `kill 0` or a PID of `-1` | Targets the current group or a broad set of accessible processes. Use positive PIDs greater than 1. `kill -0 PID` checks existence and permission and provides no proof of identity or health. |
| `pkill -f`, `pkill -x`, or `killall` | Can match other instances, wrappers, or projects. Find candidates with `pgrep` and verify each PID. |
| `kill $(pgrep ...)` or search output piped to `xargs kill` | Skips identity checks. Inspect search results before signaling. |
| A saved PID, pidfile, or `$!` | The PID may have been reused or belong to a wrapper. Verify the current command, start time, and ancestry before signaling. |
| Children remain after the parent exits | Descendants can keep running. Identify and verify those belonging to the task, then handle them individually. |
| A new PID appears after the process exits | Check the ancestry and supervisor. Use the verified task management interface. Avoid repeatedly killing respawned processes or stopping a shared service. |
| KILL is the first signal, or the search broadens after a failure | KILL gives the process no chance to clean up. Use TERM first and escalate only for the same verified process when necessary. |

A process in state `Z` must be reaped by its parent. Investigate I/O for state `D`, where signals may not take effect immediately. Use the client's authorization mechanism for permission failures. Confirm the target task has exited and that related sessions and other tasks are still running normally.

[Linux kill PID semantics](https://man7.org/linux/man-pages/man2/kill.2.html) · [pgrep and pkill](https://man7.org/linux/man-pages/man1/pgrep.1.html)
