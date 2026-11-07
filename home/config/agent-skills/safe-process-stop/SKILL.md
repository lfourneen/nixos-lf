---
name: safe-process-stop
description: 停止或清理 Linux 进程、处理端口占用或残留后台任务时使用。核对 PID、父子关系、PGID 和启动身份，按精确 PID 发送信号，避免影响终端、agent 和登录会话。
---

# 按 PID 停止进程

先确认进程属于目标任务，再发送信号。信号目标只使用核对过且大于 1 的正整数 PID；PGID 和 SID 用于识别共享范围，不作为终止目标。

## 定位并核对进程

已知 PID 时直接查询；未知时用 `pgrep` 列出候选。例如：

```bash
pgrep -a -x 'worker'
pgrep -a -f -- '^/完整/程序路径( |$)'
ps -eo user,pid,ppid,pgid,sid,lstart,stat,args --forest
```

`pgrep` 的名称匹配长度有限，长名称或解释器脚本可按完整命令行查找。匹配模式是正则，`-x` 也可能选出多个副本；逐个核对 PID，不直接把结果传给 `kill`。

对候选进程检查所有者、命令、启动时间、父子关系和组信息：

```bash
ps -p "$target_pid,$$,$PPID" -o user,pid,ppid,pgid,sid,lstart,stat,args
pstree -sp "$target_pid"
pgrep -a -P "$target_pid"
```

以上 shell 变量示例使用 Bash。沿父链识别当前 shell、agent Host、终端和登录会话组件；普通任务清理排除这些进程。`pstree` 不可用时用 `ps --forest` 查看进程树。`pgrep -P` 只列直接子进程，需要处理后代时逐层核对。需要区分同名任务时，可直接查询以下绝对路径，不依赖当前工作目录：

```bash
readlink -- "/proc/$target_pid/exe" "/proc/$target_pid/cwd"
cat -- "/proc/$target_pid/cgroup"
```

共享 PGID 不妨碍单独处理已确认的任务 PID，但对整个组发信号会影响其他成员。目标归属无法确认时，保留进程并指出缺少的证据。

## 发送信号

应用有作用范围明确的退出或任务取消接口时优先使用。需要发信号时，执行前再次核对进程身份，再发送 TERM。以下 Bash 示例只检查 PID 格式，身份核对需在前一步完成：

```bash
if [[ ${target_pid:-} =~ ^[1-9][0-9]*$ && $target_pid != 1 ]]; then
  kill -TERM -- "$target_pid"
else
  printf '%s\n' '需要核对过的单个 PID，且 PID 大于 1' >&2
fi
```

等待目标完成正常退出，再检查状态与目标端口。只有同一进程仍存活且任务需要强制结束时，重新核对后使用 `kill -KILL -- "$target_pid"`。目标已退出、身份改变或查询失败时，停止升级信号。

启动时间与命令核对可减少 PID 复用误判，但 `ps` 后接 `kill` 仍有竞态。需要自动化绑定进程身份时，使用可用的 pidfd 接口。

## 常见错误与处理方法

| 问题用法或现象 | 原因与处理方法 |
|---|---|
| `kill -TERM -PGID`、`kill -TERM -- -PGID`、`pkill -g`、作业组 `%…` | 选择整个组。改为逐个核对任务 PID；`--` 不会改变负 PID 的含义。 |
| `kill 0`、以 `-1` 为目标 | 作用于当前组或大量有权限访问的进程。仅以大于 1 的正 PID 为目标。`kill -0 PID` 则是存在性和权限检查，不验证身份或健康状态。 |
| `pkill -f`、`pkill -x`、`killall` | 可能选中同名副本、包装器或其他项目。用 `pgrep` 查询后按 PID 处理。 |
| `kill $(pgrep …)` 或搜索结果直接接 `xargs kill` | 缺少逐项身份核对。将查询与信号操作分开。 |
| 使用旧 PID、pidfile 或 `$!` | 进程可能退出并被复用，或该 PID 对应包装器。发送前核对当前命令、启动身份和父子关系。 |
| 终止父进程后仍有子进程 | 后代可能继续运行。先记录并逐层核对任务后代，再逐个处理。 |
| 进程退出后出现新 PID | 检查父链和 supervisor；按已确认的管理方式停止任务，避免循环终止重生进程或直接停止共享服务。 |
| 直接使用 KILL，或失败后扩大匹配范围 | KILL 不提供清理机会。先 TERM，必要时对同一身份升级，保持原任务范围。 |

`Z` 状态需要父进程回收；`D` 状态需要排查 I/O，信号可能暂不生效。权限拒绝按当前客户端的授权机制处理。完成后确认目标任务退出，相关会话与其他任务仍正常。

命令参考：[kill 的 PID 语义](https://man7.org/linux/man-pages/man2/kill.2.html)、[pgrep/pkill](https://man7.org/linux/man-pages/man1/pgrep.1.html)。
