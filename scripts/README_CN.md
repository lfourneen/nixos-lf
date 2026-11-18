# scripts

[English](README.md) | 简体中文

本目录收录仓库自用的工具脚本，用于部署 flake、刷新 DNS 名单、验证网络策略、运行基准测试、检查维护台账、迁移虚拟机所有权以及创建系统 Age 密钥。

请在仓库根目录运行这些脚本。它们依赖 Bash、coreutils、findutils、rsync、util-linux（flock）、Nix 和 Python 3，其中 Python 工具只使用标准库。部署到 `/etc/nixos/scripts` 的是权限为 0600 的构建输入快照，因此请使用仓库中的脚本，而不是部署副本。

| 脚本 | 作用 |
| --- | --- |
| `rebuild-deployed.sh` | 持有部署锁重建部署副本 |
| `push-to-dir.sh` | 经暂存、校验、加锁和备份后，把构建载荷复制到 `/etc/nixos` |
| `sync-to-git.sh` | 把 `/etc/nixos` 复制到私有 Git 工作目录，不覆盖已有仓库 |
| `refresh-dns-blocklist.py` | 获取、校验并发布 DNS 黑名单快照与 manifest |
| `verify-network.py` | 运行组件检查并记录准确的输入与输出 |
| `network-benchmark.py` | 测量受控 HTTP 吞吐与网络各层时延 |
| `check-maintenance.py` | 用文件、`flake.lock` 和网络来源校验维护台账 |
| `libvirt-qemu-migrate.sh` | 把 VM 镜像和状态的属主交给非特权 QEMU 用户 |
| `system-age-key.py` | 创建或接管用于系统秘密的 root 专属 Age 身份 |
| `usbguard-policy-migrate.py` | 把已安装的 USBGuard 策略复制到私有持久目录 |

## push-to-dir.sh

把构建载荷目录（`home/`、`overlays/`、`system/`、`tests/`、`scripts/`、`maintenance/`、`local-data/`、flake 文件和 `.sops.yaml`）复制进 `/etc/nixos`。测试临时 `DEST_DIR` 时须传入 `--test-mode`。脚本先取得独占锁，建立私有暂存副本，检查权限与符号链接，用 `nix eval --offline` 求值暂存 flake，然后才同步目录树。旧目录树会连同恢复日志完整备份。安装失败时自动恢复，下一次部署会先恢复尚未结束的事务。最终的目录树替换不是原子操作。重建部署副本时使用 `sudo bash scripts/rebuild-deployed.sh switch`，在求值与重建期间持有同一把锁。

目标目录非空且没有 `.nixos-lf-deploy` 标记时，脚本会拒绝执行。已有的 `/etc/nixos` 需要先显式接管一次：

```sh
sudo ./scripts/push-to-dir.sh --adopt-existing
sudo ./scripts/push-to-dir.sh
```

目标目录为空时不需要该参数。第一次带标记的部署成功后，后续运行可以省略 `--adopt-existing`。备份和恢复日志位于 `/var/lib/nixos-lf-deploy`，该目录会跨重启持久化。

`--skip-eval` 会跳过暂存的 `nix eval`。离线复制可信 checkout，或本机缺少 flake 输入时可以使用。文件权限、manifest、lock 和备份仍会执行，`nixos-rebuild` 在切换前仍会求值部署后的目录树。

| 变量 | 含义 |
| --- | --- |
| `DEST_DIR` | 目标目录，默认 `/etc/nixos` |
| `SUDO` | 提权前缀；留空时以普通用户身份操作临时目标 |
| `DEPLOY_STATE_DIR` | 暂存与备份根目录，默认 `/var/lib/nixos-lf-deploy`；非 root 时使用按用户隔离的临时目录 |
| `DEPLOY_LOCK_FILE` | 锁文件，默认 `/run/lock/nixos-lf-deploy.lock`；非 root 时位于状态目录内 |
| `DEPLOY_SKIP_EVAL` | 设为 `1` 时跳过暂存求值，等价于 `--skip-eval` |

## USBGuard 策略

USBGuard 从 `/persist/usbguard/rules.conf` 读取设备规则，要求 root 属主和 0600 权限，父目录要求 root 属主和 0700 权限。
Nix 声明服务参数和策略路径，设备标识保存在这个私有文件中。
请单独私密备份，不要提交到 Git。

### 迁移已有策略

如果旧 NixOS 配置已经提供 store 中的 USBGuard 规则，首次切换前先复制：

```sh
sudo python3 scripts/usbguard-policy-migrate.py
```

脚本从 `usbguard.service` 的 `ExecStart` 参数 `-c` 定位 daemon 配置，再复制其中 `RuleFile` 指向的 store 规则，不打印规则内容。
服务可以处于停止状态，但 unit 必须已经安装。
已有私有策略会保留。
activation 也会在替换 `/etc` 前执行这项接管。

迁移脚本需要已有规则。从未配置 USBGuard 时，按下一节初始化。

### 首次启用 USBGuard

1. 接好首次启用后必须可用的可信键盘、鼠标、集线器和开发设备，拔掉不应进入初始允许名单的设备。`generate-policy` 会纳入当前连接的所有设备，启用前必须审阅生成结果。
2. 创建私有目录并生成初始策略。下面使用本 flake 的 USBGuard 软件包，不需要 daemon 已运行，也不要求全局安装 CLI。命令会拒绝覆盖已有规则文件。

   ```sh
   sudo install -d -m 0700 -o root -g root /persist/usbguard
   sudo nix shell .#nixosConfigurations.lfour.pkgs.usbguard --command sh -c 'set -euC; umask 077; usbguard generate-policy > /persist/usbguard/rules.conf'
   sudoedit /persist/usbguard/rules.conf
   ```

   只保留预期设备。
   生成的规则包含描述符 hash 和接口约束，避免简化为只匹配厂商或无条件的 `allow`。
   描述符匹配不能认证物理设备身份。
   修改属性时可参考[上游规则语法](https://usbguard.github.io/documentation/rule-language)。

3. 按 [push-to-dir.sh](#push-to-dirsh) 中的流程部署、构建并切换。
   仓库配置已经导入 USBGuard 模块。
   首次切换时应能使用本机控制台，输入设备须已包含在审阅后的策略里。
4. 确认服务运行，检查预期设备已被允许：

   ```sh
   sudo systemctl status usbguard.service
   sudo usbguard list-devices
   ```

模块会对 daemon 启动时已经连接的设备和之后新插入的设备应用策略。没有匹配允许规则的设备会被阻断。后续添加设备使用下一节的命令，重新生成初始策略会替换此前的决定。

### 添加设备或自定义规则

CLI 使用 raw IPC，本配置只向管理员开放，因此下面的命令需要 `sudo`。桌面 D-Bus 查询允许活跃本地用户使用，D-Bus 修改操作需要管理员认证。

1. 插入新设备，查询它的设备 ID：

   ```sh
   sudo usbguard list-devices --blocked
   ```

   核对 vendor/product、序列号、hash 和接口是否属于预期硬件。下文的设备 ID `42` 只是示例，应使用本次查询的结果。
2. 临时允许设备，测试需要的功能：

   ```sh
   sudo usbguard allow-device 42
   ```

3. 确认后永久保存该设备的授权：

   ```sh
   sudo usbguard allow-device --permanent 42
   sudo usbguard list-rules
   ```

   这会更新运行中的策略和 `/persist/usbguard/rules.conf`，无需 rebuild 或重启 daemon。
   重新插拔设备，确认规则仍能匹配。
   重新插拔或重启 daemon 后，设备 ID 可能变化。

需要自定义规则时，把示例属性替换为实际设备的值。`equals` 要求完整接口集合匹配：

```sh
sudo usbguard append-rule 'allow id 1234:5678 serial "DEVICE_SERIAL" with-interface equals { ff:00:00 }'
```

`append-rule` 默认保存规则，传入 `--temporary` 时仅保留在内存中。
规则按顺序匹配，第一条匹配规则决定结果。
如果前面已有匹配的 `block`、`reject` 或更宽泛的 `allow`，后追加的规则不会覆盖它。
先用 `list-rules` 检查顺序，再调整已有规则。

### 撤销授权

`list-devices` 的设备 ID 和 `list-rules` 的规则 ID 是两种编号。
先找到并删除对应规则，再阻断当前连接的设备。
下面的 `17` 是规则 ID 示例，`42` 是设备 ID 示例：

```sh
sudo usbguard list-rules
sudo usbguard remove-rule 17
sudo usbguard block-device 42
```

删除规则会更新保存的策略，阻断设备会同时撤销当前访问。
移除例外时，还要检查是否存在其它匹配的允许规则。
设备标识应保留在私有策略中，不要写入 `services.usbguard.rules`，否则会进入 Nix store。

## sync-to-git.sh

把 `SRC_DIR`（通常是 `/etc/nixos`）复制到私有工作目录。默认目标是 `~/Projects/Nix/nixos-<时间戳>`，权限为 0700。复制会保留文件权限、符号链接和时间戳，因此秘密文件不会变得可读，可执行文件也保留执行位。

目标目录已存在且非空时脚本会拒绝执行。`--force` 会先把原目录移动到 `<目标>.backup-<时间戳>`，再复制到新目录。脚本不会删除已有 Git 仓库。

```sh
./scripts/sync-to-git.sh
./scripts/sync-to-git.sh --force
```

| 变量 | 含义 |
| --- | --- |
| `SRC_DIR` | 来源目录，默认 `/etc/nixos` |
| `DEST_BASE` | 带时间戳目标的父目录，默认 `$HOME/Projects/Nix` |
| `DEST_DIR` | 显式指定目标，替代带时间戳的名称 |
| `SUDO` | 提权前缀；留空时以普通用户身份复制 |

## refresh-dns-blocklist.py

获取配置的上游名单，校验每个域名，合并去重，然后写入压缩快照、manifest 以及 `system/config/network/dns/blocklist.nix` 中生成的头部。构建阶段会用 manifest 校验快照，格式错误或彼此不一致的数据会让配置构建失败，而不会生成不完整的过滤名单。

```sh
./scripts/refresh-dns-blocklist.py
./scripts/refresh-dns-blocklist.py --check
./scripts/refresh-dns-blocklist.py --check --fetch
./scripts/refresh-dns-blocklist.py --offline
```

| 选项 | 行为 |
| --- | --- |
| 不带选项 | 获取、校验并发布新的数据、manifest 和头部 |
| `--check` | 校验已提交的数据、manifest 和头部，不写入文件 |
| `--check --fetch` | 另外把已提交的数据与上游来源比较 |
| `--offline` | 从已提交的快照重建 manifest 和头部 |
| `--accept-large-change` | 发布超过审核阈值的变更 |
| `--json` | 以 JSON 输出结果 |

退出码 0 表示数据一致或发布成功，1 表示漂移或拒绝大幅变更，2 表示输入无效。下载在每次重定向时都要求 HTTPS，并限制允许的主机、实际耗时和响应大小。发布持有写锁并保留持久回退日志，后续刷新会恢复中断的写入，验证会拒绝未完成的发布。受保护的基础设施域名不能进入后缀阻断快照。

## verify-network.py

对选中的网络组件运行其检查列表，并归档准确的输入与结果。组件来自 `system/config/network/maintenance/sources.json`：`browser-isolation`、`clash`、`controlled-source`、`dns`、`firefox`、`firewall`、`gost`、`link-identity`、`mihomo`、`sing-tun`、`socks` 和 `tailscale`。

```sh
./scripts/verify-network.py --plan --component dns
./scripts/verify-network.py --component dns --component firewall
./scripts/verify-network.py --all
./scripts/verify-network.py --component dns --frozen
```

证据默认写入 `audit/validation/<时间戳>`，包含 `results.json`、每项检查的日志和 lock 文件，目录权限为 0700。清单记录提交、工作区差异、包括未跟踪文件在内的构建输入、lock 摘要、软件包版本，以及每项检查的 derivation 和输出。默认情况下，存在未跟踪构建输入时运行会失败；`--allow-untracked` 会将其记录为已接受。

| 选项 | 含义 |
| --- | --- |
| `--component NAME` | 选择组件，可重复指定 |
| `--all` | 选择全部组件 |
| `--plan` | 只打印选中的组件与检查，不运行 |
| `--archive DIR` | 把证据写入指定目录 |
| `--online` | 允许需要网络的检查 |
| `--timeout SECONDS` | 单项检查超时 |
| `--allow-untracked` | 纳入未跟踪构建输入并标记为已接受 |
| `--runtime-verification` | 要求 nftables JSON 结果成功，且启动 ID、策略代次和新鲜度均匹配 |
| `--frozen` | 把准确输入复制到私有临时目录，并从该副本运行 |

## network-benchmark.py

使用当前网络策略测量受控负载。

```sh
./scripts/network-benchmark.py serve --bind 127.0.0.1 --port 18094
./scripts/network-benchmark.py measure --url http://127.0.0.1:18094 --samples 20
./scripts/network-benchmark.py layers --name origin.example.test --address 192.0.2.10 --samples 20
./scripts/network-benchmark.py snapshot --tool system/programs/clash/service/clash-verge-stage.py
```

`serve` 启动回环 HTTP 夹具。`measure` 对原生或显式代理路径输出 P50 和 P95 的首字节时间与总时间、每秒连接数、批量吞吐，以及客户端 CPU 和 RSS。

`layers` 需要 `dig`、`ping` 和受控的 HTTPS 源站。把示例名称及地址换成源站的 DNS 名称和 IPv4 或 IPv6 地址。私有 CA 可用 `--ca-file` 指定。
命令查询对应地址族的 DNS 记录，记录结果码、地址记录和 DNS TTL。
ICMP echo 和 HTTPS 独立使用指定地址，TLS 和 HTTP 使用指定名称。
调用者有权访问受控 HTTP CONNECT 入口时，可使用 `--proxy`。
JSON 包含每次观测，并按阶段和结果给出 P50、P95 及最大值。TCP 耗时衡量客户端 socket 握手，握手端可能是 TUN 或 relay。
HTTPS 成功要求 TLS 校验通过，且 HTTP 200 响应包含正文首字节。`--timeout` 限制整个 HTTPS 样本，同时用于 DNS 查询和 echo 的截止时间。

`snapshot` 在合成的 32 MiB 快照上测量完整与快速校验路径的耗时。`blocklist` 测量已提交域名快照的解压和 Python 集合构建。

## check-maintenance.py

校验 `maintenance/inventory.json`。检查器确认 schema、条目 id 唯一性、仓库文件中的字面 pin、`flake.lock` 中的根 flake 输入版本，以及指向 `system/config/network/maintenance/sources.json` 的 `network_ref`。默认运行不发起网络请求。

```sh
python3 scripts/check-maintenance.py
python3 scripts/check-maintenance.py --json
python3 scripts/check-maintenance.py --online --json
```

`--online` 从 Mozilla 官方扩展 API 记录版本、权限和文件摘要，不安装更新，也不验证下载的 XPI 签名。`--snapshot FILE` 以私有权限记录 Firefox、Flatpak、Docker 和插件的已安装元数据。`--baseline FILE` 在观测结果变化时失败，不可访问的运行时会明确标记。Docker 观测记录 digest，不验证发布者签名。台账还通过 `lock_graph_sha256` 校验整个 flake 锁图。

```sh
python3 scripts/check-maintenance.py --snapshot /private/path/runtime.json
python3 scripts/check-maintenance.py --baseline /private/path/runtime.json --json
```

## libvirt-qemu-migrate.sh

把 libvirt 磁盘镜像和持久 QEMU 状态的属主交给非特权用户 `qemu-libvirtd`。脚本只修改 `<root>/images` 和 `<root>/qemu`，默认根目录为 `/var/lib/libvirt`。

先停止 libvirtd 及其虚拟机，预览变更，再执行：

```sh
sudo ./scripts/libvirt-qemu-migrate.sh
sudo ./scripts/libvirt-qemu-migrate.sh --apply
```

预览不会改动任何文件，并会打印回退命令。`--apply` 需要 root。脚本拒绝符号链接或不安全的根路径。`LIBVIRT_QEMU_MIGRATE_*` 变量只用于测试中覆盖根目录、属主和属组。

## system-age-key.py

创建或接管用于系统秘密的 root 专属 Age 身份。默认密钥路径为 `/persist/secrets/age/system.key`，属主为 root，权限 0600。脚本会以 0700 创建 `/persist` 下缺失的目录，但不会创建 `/persist` 本身，也不会在 `/persist` 未挂载时写入。私钥不会打印，不会进入 Nix store，也不会出现在命令行上，脚本只报告公钥 recipient。

### 迁移现有密钥

要把现有桌面身份变成 root 专属身份，直接接管即可：

```sh
sudo ./scripts/system-age-key.py --adopt-desktop
```

`--adopt PATH` 可以选择其他来源文件，例如：

```sh
sudo ./scripts/system-age-key.py --adopt /path/to/age.txt
```

请传入绝对路径。如果因为加了引号等原因，shell 把字面量 `~` 或 `$HOME`
传给了脚本，脚本会停止并要求展开后的路径。来源必须是普通文件，且不能有
group/other 权限；如果复制时权限变宽，先执行 `chmod 600`。recipient 没有
变化，因此不需要修改 `.sops.yaml`，不需要改密文，也不需要运行
`sops updatekeys`。选项默认开启，所以应在下一次重建前完成接管。
`push-to-dir.sh` 会在改动 `/etc/nixos` 前检查配置中的密钥，缺失或属主不对时
拒绝部署；密钥缺失、属主或权限不对时，激活脚本也会给出明确错误并停止。
重新构建并确认服务正常，然后删除桌面的旧副本。保留旧副本意味着桌面身份仍能
读取该密钥。

也可以直接复制，但脚本会检查源文件权限、拒绝符号链接和已存在的目标，并打印 recipient：

```sh
sudo install -D -m 600 -o root -g root \
  "$HOME/.config/sops/age/keys.txt" /persist/secrets/age/system.key
```

### 创建新身份

不带 `--adopt` 时脚本生成新身份。全新安装可以在同一步把 recipient 写入覆盖系统秘密的 creation rule：

```sh
sudo ./scripts/system-age-key.py --generate --write-sops-config
```

`--write-sops-config` 只在匹配的 `creation_rules` 中插入新的 `- "age1..."` 条目，保留文件其余内容、属主和权限，并写一份仅 root 可读的 `.sops.yaml.bak-<时间戳>` 备份。`--sops-config PATH` 可以选择其他配置文件。没有规则匹配 `system/secrets/*.yaml` 时脚本会停止并要求手工修改。

选项默认开启，因此这些操作必须在下一次重建前完成。如果已有密文使用另一把身份加密，要在旧身份仍能解密时重新加密：

```sh
for f in system/secrets/{secrets,soul,skill,proxy}.yaml; do
  sudo SOPS_AGE_KEY_FILE=<old-key> sops updatekeys "$f"
done
```

全新安装没有这类密文。不加 `--write-sops-config` 时，需要手工加入脚本打印的 recipient。`--print-recipient` 只读取已有密钥的公钥。

`--directory` 和 `--key-name` 可以选择其他密钥位置。`--allow-unprivileged` 仅用于临时目录中的测试，不要用它创建真实密钥。移动身份不能撤销此前已经获得的访问，暴露过的凭据需要另外轮换。
