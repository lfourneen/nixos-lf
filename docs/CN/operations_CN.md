# 网络操作与诊断

[English](../operations.md) | 简体中文 | [文档目录](README_CN.md)

## 模式切换

使用 Nushell 的 `clash-on`、`clash-off`，它们记录特权意图，管理 GUI/helper 并等待一致状态。关闭 GUI 不会清除代理意图；核心停止后因此进入 C。

底层系统命令为 `sudo proxy-control on`、`sudo proxy-control off`。`on` 打开一次性配置导入窗口并启动 helper，GUI 仍需提供配置并启动核心；`off` 清除授权并停止 helper。桌面命令额外处理 GUI 进程。A 中仅启动 `clash-verge` 不会授权核心。

```sh
proxy-control status proxy
proxy-control wait proxy
proxy-control status direct
firefox --check
proxy-inspect
```

`status` 输出就绪 JSON，`wait` 等待目标模式满足共享就绪条件。Nushell 的 `proxy-status` 汇总模式、DNS、relay、验证、告警与实时探针，`egress-audit` 提供额外主动诊断。`proxy-inspect` 仅读取状态、服务资源及链路/队列/offload 统计，不发送探针。

## 状态与定位

| 文件 | 含义 |
|---|---|
| `/run/proxy-mode/state.json` | 原子模式、原因、策略代次、revision 和更新时间 |
| `/run/proxy-mode/health.json` | 探针结论与分层耗时 |
| `/run/proxy-mode/heartbeat.json` | 最近成功监督轮次 |
| `/run/netsec/state.json` | 验证结论、所验证代次与新鲜度 |
| `/run/dns-upstream/state.json` | DNS 模式/原因、会话、预算、截止和 override |
| `/run/dns-policy/state.json` | Broker 租约状态与应用代次 |
| `/run/gost-relay/state.json` | Relay 观测与应用代次 |
| `/run/netsec/alerts/*.json` | 各 owner 告警；`/run/netsec/failed` 汇总 owner |
| `/run/proxy-mode/intent`、`core-seen` | 单次开机意图和崩溃检测标记 |
| `/run/proxy-mode/authorized-config`、`config-digest`、`authorize-window` | root 私有快照授权与一次性导入凭据 |

消费者读取原子 JSON，独立 `status`、`reason`、`version`、`result`、`verified-version` 是兼容镜像。仅原因变化更新 `revision`；内核策略提交更新 `policy_version`，`version` 是其别名。跨新代次的检查结果丢弃。

就绪要求模式、DNS、broker、relay 同代次且新鲜，没有告警，并有新鲜验证。Broker 必须 `applied`，`suspended`/`revoked` 和 DNS `degraded-dot-only` 不算就绪。消费者默认有效期 120 秒，验证为 420 秒以容纳五分钟定时器及有界执行。Boot identity 和单调时间戳拒绝跨开机、未来与陈旧记录。

| 原因 | 定位方向 |
|---|---|
| `request`、`reload` | 转换中等待核心健康与验证 |
| `unauthorized-core`、`core-gone` | 意图、启动器/helper 与核心 cgroup |
| `no-tun`、`tun` | 设备、受管表/规则、实际选路与 fake-IP HTTP |
| `config` | 快照摘要、受管 API 和 TCP/UDP 监听身份 |
| `route` | 日志实际叶子为 DIRECT/REJECT 或与选择图不符 |
| `obs`、`obs:1/2`、`obs:2/2` | HTTP 完成、精确源端口日志和选择观测 |
| `resolver-error`、`split-error` | DNS 身份或批准 Wi-Fi profile/租约 |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

部分日志和授权文件需要 sudo。诊断输出中应去掉快照、订阅配置等敏感内容。

## 协调与防火墙恢复

Socket 目录事件、TUN link 监视、nft 事件、NetworkManager dispatcher 和 dhcpcd hook 唤醒监督器。自适应轮询后备为转换中两秒、执行模式最多五秒、直连最多 60 秒；模式监督器有 90 秒 watchdog。取消的探针不算失败观测。

构建期的[预期规则](../../system/programs/systemd/proxy-mode.nix) 在 LKL 用户态内核中用同版 nft 生成。Verifier 比较所属表、hook、priority、policy 和完整有序表达式，仅规范化 handle、counter、集合元素顺序和受严格约束的 DNS 租约。读取/解析错误是失败，不当作空成功；没有核心但正确安装 C 也能通过。

`nftables-verify` 报告失败和告警后仍退出零，以便修复/重建继续；应看 JSON 结论，不能只看 unit 退出码。各组件只清自己的告警，忙碌轮次保留排队验证请求。

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload 原子重装所属静态策略和安全模式；代理意图先回 C，再由健康门恢复 B。Stop 保留已加载规则。`nftables-verify-repair.service` 显式修复并复验，`nftables-recover` 重试失败的 nftables unit。不要清空全局 ruleset。

## 配置入口

| 来源 | 设置 |
|---|---|
| [machine.nix](../../system/config/machine.nix) | 用户、接口及匹配键、UID、端口、mark、网段和上游 |
| [proxy-options.nix](../../system/config/proxy-options.nix) | TUN、表 2022、fake-IP、探针、开机意图、proxyOnly、访客、split DNS 和新鲜度 |
| [proxy-profile.nix](../../system/config/proxy-profile.nix) | root 受管 DNS/TUN/profile 约定 |
| [hardening-options.nix](../../system/config/hardening-options.nix) | 宿主/访客私网、显式宿主端口、热点、tailnet 和 Wi-Fi MAC |
| [cvr-merge.nix](../../home/config/cvr-merge.nix) | 匹配的 GUI Merge 及路由增强 |
| [firefox.nix](../../home/programs/firefox/firefox.nix) | 原生浏览器、隐私、策略、字体和启动器 |

TUN 默认 `Mihomo`，表 2022、规则优先级 9000、内部 mark 8227/8228、fallback priority 32768、file descriptor 0，由 parser、模板和健康检查共用。`tunMode=false` 选择 HTTP-only/redir-host，不再要求 TUN。Fake-IP 网络由 `fakeIpRange` 推导，避免单独修改消费者。

需要硬件 MAC 的 Wi-Fi profile 可设置：

```sh
nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address permanent
# 下次激活时恢复声明默认：
nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address ""
```

`preserve` 保持当前 MAC，不恢复硬件地址。Profile 显式值优先于 `my.hardening.wifi.clonedMacAddress`；相同 SSID 的网络共用默认稳定身份。

## 离线恢复

需要直连时用 `clash-off`。A 中门户/DNS 恢复先读 DNS 状态和历史，必要时显式开启有界续租 override：

```sh
sudo touch /run/dns-upstream/force-plaintext
# 恢复后删除，重启也会清除。
sudo rm /run/dns-upstream/force-plaintext
```

它允许明文 DNS 并继续计入预算，不能越过 C 的出站 guard，范围见 [DNS](dns_CN.md)。

Generation 故障时从 systemd-boot 选择旧条目；菜单保留八个以适应 ESP 容量。GC 尚未删除的 store 路径也能离线激活：查 `/nix/var/nix/profiles/system-*-link`，以 root 运行 `nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-…>`，再运行 `/nix/var/nix/profiles/system/bin/switch-to-configuration boot`。全部无法启动时用 NixOS ISO 解锁并挂载根及 ESP，在 `nixos-enter --root /mnt` 中恢复。

## 配置检查

[维护手册](maintenance_CN.md#验证)按改动列出检查，区分临时文件测试、包构建、隔离内核 VM 和宿主激活。修改完成后统一执行相关集合。`scripts/push-to-dir.sh` 仅部署构建输入和测试源码，不运行检查，也不复制 `docs/`。
