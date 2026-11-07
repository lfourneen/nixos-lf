# 本机删除保护

以下路径中的 `~` 表示当前用户的 home，与工作目录无关。路径表描述本机 DSH 启动器的默认范围；普通文件权限、immutable 标志及客户端自己的沙箱还可能进一步限制操作。

## DSH 默认可写路径

| 用途 | 路径 |
|---|---|
| 项目和下载 | `~/Projects`、`~/Downloads` |
| 应用状态和缓存 | `~/.cache`、`~/.config`、`~/.local`、`~/.dsh` |
| JavaScript | `~/.npm`、`~/.bun`、`~/.yarn` |
| Go | `~/go` |
| Rust | `~/.cargo`、`~/.rustup` |
| JVM | `~/.gradle`、`~/.m2` |
| .NET | `~/.nuget`、`~/.dotnet` |
| Haskell | `~/.cabal`、`~/.stack`、`~/.ghcup` |
| OCaml | `~/.opam` |
| Elixir | `~/.mix`、`~/.hex` |
| Ruby | `~/.gem`、`~/.bundle` |
| PHP | `~/.composer` |
| Conda | `~/.conda` |
| 开发服务和部署工具 | `~/.kube`、`~/.docker`、`~/.terraform.d` |
| Android | `~/Android`、`~/.android` |
| 临时文件和运行接口 | `/tmp`、`/dev`、`/proc`、`/run/user/<当前用户 UID>` |

目录须在启动器应用规则时存在。进入其他工作目录不会自动扩大此表；符号链接也不会给表外目标增加写权限。

## DSH 默认限制写入的路径

上述范围之外默认只允许读取和执行，不允许创建、修改、重命名或删除。常见目标如下：

| 路径 | 默认行为 |
|---|---|
| `~/Documents`、`~/Videos`、`~/Music`、`~/Pictures`、`~/Desktop` | 不在 DSH 可写范围内。 |
| `~/.ssh`、`~/.aws`、`~/.codex`、`~/.agents` | 不在 DSH 可写范围内。 |
| `~/work`、`~/src`、`~/tmp` 等自行创建的目录 | 不因是工作目录或临时目录就获得写权限。 |
| `/mnt`、`/media` 下的项目 | 默认没有写权限，需单独核对启动时是否已授予。 |
| `/etc`、`/var`、`/nix/store` | 不在 DSH 可写范围内，且还受各自的系统权限约束。 |

例如，`~/Projects/demo` 属于默认可写范围，`~/work/demo` 不属于。表内的 `.config` 默认可写，但下节列出的凭据文件仍受 immutable 限制。将表外文件移入回收站也需要改动原目录，不会绕过写入限制。

此 Landlock 范围适用于经本机启动器运行的 DSH 及其后代。启动时设置 `DSH_NO_LANDLOCK=1` 会跳过该层，`DSH_EXTRA_RW` 可添加可写路径；两者都属于启动前的配置，排障时核对已有设置，不为完成删除自行调整。进程内后来改变同名环境变量不能解除已施加的规则。

单独启动的 Codex、OpenCode 或其他程序不自动受到这份 DSH 路径表约束；按当前客户端给出的可写目录和授权范围操作。反过来，经 DSH 启动并继承规则的子进程，即使程序名不同也仍受该层限制。

## immutable 文件保护

本机 home-guard 默认给已有的以下文件设置 immutable 标志：

- `~/.ssh/id_*`
- `~/.ssh/config`
- `~/.ssh/authorized_keys`
- `~/.config/sops/age/keys.txt`

这项文件标志不依赖 DSH；Codex、OpenCode 和普通 shell 也会受其影响。文件带 `i` 时，修改、重命名和删除受限。它不递归锁定 home；`Documents`、`Videos`、`Music` 不在默认 immutable 列表。后来创建的匹配文件要等下一次应用规则。

用 `lsattr -d -- 目标路径` 检查当前是否带 `i`。目录带 `i` 只说明目录项增删改受限，不能据此认定子文件内容也全部不可修改。

`home-guard lock` 和 `unlock` 作用于全部配置目标，没有按路径操作的接口。以普通用户运行时，所有子命令均会调用 sudo，包括 `status`。仅查看标志时使用 `lsattr`；解除保护需有对应授权。

## 回收站和删除命令

本机 Nushell 中的 `rm` 调用 `trash-put`。其他 shell 和程序内部的删除接口保持原有行为；需要回收站时显式调用 `trash-put`。

home 回收站通常位于 `${XDG_DATA_HOME:-$HOME/.local/share}/Trash`，其中 `files` 保存文件，`info` 保存恢复信息。其他挂载点可能使用自己的回收站，统一用 `trash-list` 核对记录。

## 只读诊断

将 `target` 设置为已核对的绝对目标路径，再检查实际权限：

```bash
ls -ld -- "$target"
id
lsattr -d -- "$target"
findmnt -T "$target"
```

`Permission denied` 与 `Operation not permitted` 都不能单独确定原因，必要时再检查 ACL 及客户端报告的沙箱范围。沙箱内的挂载视图可能不同于宿主机；查询失败时记录尚未确认的事实，再按客户端权限机制处理。
