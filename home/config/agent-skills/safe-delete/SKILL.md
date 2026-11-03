---
name: safe-delete
description: 删除任何文件或目录之前先读。本机 /home 上的 rm 语义被改成了回收站（trash），DSH 桌面跑在 Landlock 只读边界内，关键目录还带 root 设的 immutable 位；用于判断该用 trash-put 还是真 rm、被内核拒绝时该怎么做、以及哪些路径永远不许碰。触发词：删除、删掉、清理、清空、rm、rm -rf、回收站、trash、delete、remove、purge、清理缓存。
---

# 安全删除（safe-delete）

这台机器的删除语义被刻意改过。动手删任何东西之前，按下面四步走。

## 1. 默认进回收站，不真删

- 删除用户数据 → `trash-put <path>`。`-r` / `-f` 会被接受并忽略（GNU rm 兼容），所以 `trash-put -rf dir` 是合法写法。
- 回收站是 `~/.local/share/Trash`。`/home` 是**单个 btrfs 子卷**，所以"进回收站"是 `rename(2)`：瞬时、零拷贝、不占额外空间（不存在跨子卷 `EXDEV` 的问题）。
- 找回：`trash-list`（看） / `trash-restore`（交互式还原）。
- `trash-empty` 才是真删除，**属于破坏性操作**，不要顺手执行。

只有这几类可以直接 `rm`：构建产物（`target/`、`node_modules/`、`dist/`、`result*`）、`/tmp` 下的临时文件、`~/.cache` 内的缓存。

## 2. `rm` 只在交互式 shell 里被改写

nushell 里 `rm` 被 `def --wrapped rm` 覆盖成 `trash-put`，但**你（agent）执行的命令跑在 `bash -c` 里，不读 nushell 配置，你的 `rm` 仍然真删**。

所以：命令里显式写 `trash-put`，不要指望 alias，也不要把"用户自己敲 rm 是安全的"当成"我敲 rm 是安全的"。

## 3. 删除前自检（很便宜，必须做）

1. `realpath <目标>`，确认落在预期目录；变量未展开时要先展开再看。
2. 任何递归删除之前先 `ls` 一遍目标，确认没有意外展开（glob 与带空格路径是主要事故源）。
3. 批量删除时逐个路径来，别写 `rm -r $DIR/*`。
4. 手上有 `trash-list | head` 可以确认刚删的东西确实躺在回收站。

## 4. 被内核拒绝 = 停下来，不许绕过

出现下面任一信号，**立即停止并向用户报告**：

- `Permission denied` / `Operation not permitted` 出现在 `~` 下的删除或写入；
- `chattr: Operation not permitted`；
- DSH 桌面里写 `~/Documents`、`~/.ssh`、`~/.config/sops` 等被拒。

禁止的"绕过"动作：`sudo`、`chattr -i`、`mount -o remount`、`unshare` / `nsenter` / `bwrap`、`dd`、改用 `find -delete`、写个脚本让用户自己跑。

被拒绝意味着**这是受保护路径**，或**当前进程不在可写白名单内** —— 那正是要保护的东西，不是待修的 bug。

## 5. 本机的保护层（现状）

| 层 | 覆盖范围 | 被拦时的表现 | 谁设的 |
|---|---|---|---|
| Landlock（DSH 桌面 launcher） | 全盘只读 + 白名单可写：`~/Projects`、`~/Downloads`、`~/.cache`、`~/.config`、`~/.local`、`~/.dsh`、`/tmp`、`/run/user/$UID`、`/dev`、`/proc`（仅为了 Chromium 建 renderer 的 user namespace，不含持久数据） | `Permission denied`，进程内**无法解除**（内核态，只能收窄，`sudo` 也不行） | `overlays/dsh-desktop-appimage.nix` |
| immutable 位 | `~/Documents`、`~/Videos`、`~/Music`、`~/.ssh/id_*`、`~/.ssh/config`、`~/.ssh/authorized_keys`、`~/.config/sops/age/keys.txt` | `Operation not permitted`，需 root 才能解开 | `system/programs/systemd/home-guard.nix` |
| 回收站 | 所有用户数据 | 可 `trash-restore` 找回 | trash-cli |

查看/临时调整（需要用户密码，agent 不应自己调用）：

```
sudo home-guard status           # 哪些路径被锁着
sudo home-guard unlock ~/.ssh    # 临时解锁（例如接受新主机指纹）
sudo home-guard lock             # 重新上锁
```

注意：被 immutable 锁住的**目录**连新建文件都会失败，所以往 `~/Documents` 存东西前要先 unlock。

## 6. 永远不要

- 删除 `~`、`/home/*`、`/nix/store`、`/etc`、`/var/lib` 下不是你创建的东西；
- 对 `~` 整体做 `chmod` / `chown` / `find -delete` / `rsync --delete`；
- 在没有被明确要求时"顺手清理"用户目录；
- 把 `trash-empty`、`btrfs subvolume delete`、`rm -rf /` 当成可自主决定的操作；
- 因为"删不掉"就去改动保护机制本身。
