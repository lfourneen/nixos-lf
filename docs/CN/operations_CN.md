# 网络操作与诊断

[English](../operations.md) | 简体中文 | [文档目录](README_CN.md)

## GUI 控制权限

Clash Verge GUI 复用 Nixpkgs 的上游构建。独立编译的 Rust 后台服务带有本地补丁。
首页、设置页、托盘和快捷键中的 System Proxy、TUN 与核心模式仍可操作，并可写入 GUI 本地偏好。
这些操作在到达操作系统或受管核心前被静默丢弃：

- 启动包装用只丢弃写入的 shim 替换 System Proxy、PAC 和代理守护调用。
  私有挂载命名空间遮蔽绝对 helper 路径，GSettings 内存后端和 KDE 5 helper 名称作为后备。
  启动与退出时的 OS 代理清理同样不改变实际设置。
- 控制器对 `mode`、`tun` 的 `PATCH /configs` 和 `PUT /configs` 重载返回 204 但不应用。
  其它配置写入、禁用规则、升级和重启仍拒绝。
- helper 以内核 Unix peer 身份区分调用者，GUI 停止或重启请求不能中断受管核心，受控来源由 root 启动。
- 受管解析器在最终校验前把核心模式固定为 `rule`，并用 root 策略覆盖整个 `tun` 段。
  本地偏好不能改变实际状态，也不能让重启因策略校验失败。

GUI 可以显示本地偏好；实际状态以控制器、`proxy-status` 或受管核心为准。
受管组内选择节点、关闭连接、延迟测试、状态读取和日志仍然可用。

使用 `clash-rules` 或 `clash-proxy-only` 请求系统路由子模式。
两者均渲染核心 `mode: rule`，GUI 的 Rule 表示核心按规则列表处理流量，并不代表当前路由子模式。
使用 `proxy-status` 查看整体网络状态及 B 中的路由子模式。
策略缺失或损坏不会开放 GUI 控制写权限。

## 来源登记

来源清单保存在 Sops 加密的 [system/secrets/proxy.yaml](../../system/secrets/proxy.yaml)，激活时渲染为 root 私有 `/run/secrets/proxy_source`（0400）。

在 Sops 编辑器中，`proxy_source` 是包含 sources 数组的 JSON 字符串：

```yaml
proxy_source: |-
  {
    "sources": [
      {
        "id": "primary",
        "subscription": {"url": "https://subscription-a.example.invalid/config"},
        "providers": {}
      },
      {
        "id": "secondary",
        "subscription": {"url": "https://subscription-b.example.invalid/config"},
        "providers": {}
      }
    ]
  }
```

每个来源有一个主 `subscription` 和可选 HTTP provider。
新增订阅使用独立 ID，同一来源内的 provider 则须匹配订阅声明的名称和 URL。

端点可选设置包括最多四个精确 HTTPS `redirects`，以及默认为 `clash.meta` 的 `user-agent`。
来源的 `"gui-profile":"REMOTE_PROFILE_UID"` 映射 GUI 本地 `profiles.yaml` 中的精确 UID。
映射只授权选择来源，不导入 GUI 的 URL 或 YAML。

### 添加与切换来源

运行 `sops edit system/secrets/proxy.yaml`，在 `sources` 中追加一条配置并使用唯一 ID。清单支持 1–256 条。激活更新后的密钥，然后添加需要的来源：

```sh
sudo nixos-rebuild switch --flake .#lfour
sudo clash-source add SOURCE_ID
```

检查显示的 `digest` 和 `previous` 版本，确认要登记该条目后输入 `yes`。Broker 只登记这一条目，并复核确认期间清单条目和已登记版本是否变化。摘要覆盖规范化后的整条定义。命令只显示摘要及来源 ID，URL 和凭据仍保持私有。

`add` 通过非特权 worker 下载，选择下载完成的来源，并等待现有 verifier。处于 A 时，命令会通过受保护的启动事务请求代理。处于 B 时，沿用当前路由策略，无需另跑 `clash-on` 或 `clash-rules`。A 或 B 中的显式操作立即开始，自动更新仍要求 B 稳定 90 秒。处于 C 时，应先恢复 B 或显式进入 A 再下载。

终端结果包含 `requested_source`、`active_source`、`result` 和 `reason`。接受时显示 `accepted` 并退出零。拒绝、回退、回滚及验证超时退出非零。成功验证仍须经过正常的健康观察期。可用 `proxy-status` 查看活动来源和 `Dropped leaves` 数量。回退时，活动来源可能与请求的来源不同。

用 `sudo clash-source use SOURCE_ID` 立即下载、选择并验证已登记来源。附加 `--revision REVIEWED_DIGEST` 可要求登记版本完全匹配。下载失败保留原选择。在 `proxy-only` 下候选必须匹配固定出口。要更换固定出口，先在 `rules` 中接受更新，再选择新出口。

底层命令 `review FILE --id SOURCE_ID` 和 `register FILE --id SOURCE_ID --digest REVIEWED_DIGEST --previous PREVIOUS_DIGEST` 仍可使用。首次登记使用 `none` 作为上一修订。不带 `--id` 的 review 可列出清单摘要，多条清单进行 register 时须指定 ID。

更新登记会使该来源的旧下载及批准失效。已运行的核心保留原来源身份，已接受快照保留原修订供审计比较。重新启动或回滚须使用符合当前登记修订的快照。先下载并接受新修订，再离线启动。`sudo clash-source list` 显示选择、活动授权、登记修订、已接受摘要和本地验证结果。

从 Sops 清单删除条目后，已登记副本仍然存在。用 `sudo clash-source revoke SOURCE_ID` 移除本地登记及其排队任务和快照。附加 `--revision REVIEWED_DIGEST` 可限定为指定修订。撤销活动来源会进入 C、清除 B 租约并停止核心。撤销非活动来源会保留现有活动授权。

启用运行时路由时，可在 C 用 `sudo clash-source use SOURCE_ID --select-only` 选择已登记的替代来源，再用 `sudo proxy-control recover` 通过 C 的恢复流程取得该来源。

`clash-source-select.path` 监听 GUI 的 `profiles.yaml`，切换到已映射 UID 会请求对应的登记来源。
未知 profile 不改变选择。
修改 GUI URL、YAML 或脚本不授予新权限。
GUI 选择与实际来源不同时，可查看请求、下载和应用日志。

```bash
journalctl -u clash-source-select -u clash-source-download -u clash-source-apply
```

A 中的 GUI 请求会下载并选择来源，供下次 `clash-on` 使用。

`clash-source refresh` 只准备候选。
直接请求自动下载和应用流程时，可启动 `clash-source-refresh.service`。
显式运行 `clash-on` 时，有已接受快照便先用它启动，并记录一次待处理的更新请求。
B 在同一次启动、快照、策略和路由选择下经验证并持续健康 90 秒后，协调器启动下载服务。
离开健康 B 后重新计时。
C 中请求继续排队，`proxy-status` 显示延期原因。`clash-off` 会取消尚未处理的下载和应用请求。
root timer 在开机约两分钟后排队一次请求，此后每 30 分钟排队一次，不依赖 GUI。
每轮下载任务最多尝试三次，间隔 15 秒，服务总时限为 210 秒。
导入或回滚尚未收尾时，已下载的候选按摘要等待应用。
GUI 的 `update_interval` 只影响展示副本：启动时将已存在且短于 1440 分钟的间隔提升至 1440，保留更长间隔和已禁用的计划。

当前快照仍获授权且完整，并通过新鲜的完整健康检查、就绪检查及所需 B 租约检查时，
被拒或未变化的候选保留正在运行的核心和已有连接。
当前授权失效或健康检查失败时，仍进入 C 恢复。
实际切换先进入 C，再通过 helper IPC 替换核心，重新执行导入检查，通过代理健康检查后才返回 B。

worker 失败区分 `fetch`、`parse`、`render`、`pin-addresses`。
`proxy-status` 的 `Subscription download` 另显示固定的 DNS、TLS、连接或 HTTP 失败分类，以及可用的 HTTP 状态码。
`Subscription download` 也显示候选是否正在等待应用。`Profile refresh` 显示审计与健康结果。
`Dropped leaves` 按原因显示导入时剔除的节点数。`Leaf session sample` 显示有限运行时抽查发现的认证拒绝和会话不可用数量。

按授权过滤不安全的 TLS 节点后，解析失败的节点和使用回环 IP 字面量的占位节点，最多丢弃 `max(1, remaining_node_count // 5)` 个。
DNS 答案含非公网地址，或节点使用其他非公网 IP 字面量时，拒绝候选。
下载、解析和审计失败保留有效的运行回退，没有回退则保持 C。

### 恢复已登记来源

启用运行时路由且在 C 中保留代理意图时，可以应用所选来源的最近一次下载。

```sh
sudo proxy-control recover --cached
```

候选须匹配当前登记修订和路由策略。命令使用本机下载内容，仍须通过常规内容审计和健康检查才会进入 B。
没有兼容下载时，获取新候选。

```sh
sudo proxy-control recover
```

两种操作期间，C 均持续阻断普通应用和访客的公网访问。
专用解析器通过已配置的认证 DoT 上游查询，并在本地验证 DNSSEC。下载进程使用登记的 HTTPS 端点的固定解析结果，验证 TLS 证书，只跟随登记的精确跳转。
防火墙按服务身份、物理接口、源地址、目标地址和端口限制恢复流量。内核租约在 55 秒内到期，并切断已有恢复连接。收到上行变更通知或系统进入休眠时会提前撤权。
命令先撤销恢复租约，再按请求的路由策略暂存候选。下载失败时保持 C 并保留已接受基线。进入 B 仍须通过常规内容审计和健康检查。
在 `proxy-only` 下，下载内容仍需包含所选固定出口。如需采用来源中的其他出口，参见下文的出口选择流程。
未启用运行时路由的配置可在 A 中按手动来源流程恢复。

### 只读查看订阅变化

本机自动导入正常节点轮换。
需要查看所选来源候选与已接受基线的差异时，运行：

```sh
clash-diff
```

命令通过 sudo 读取本机快照，不下载候选、不写入审阅或批准记录，在 A、B、C 中都不改变网络模式。
它显示节点地址、端口、TLS 名称、分组和规则，隐藏认证值与嵌套传输参数值。
`clash-diff --json` 提供结构化输出。
这是可选诊断命令，不是更新前置步骤。

`proxy-status` 的 `Profile refresh` 显示刷新结果和原因，并列出审计类别与计数。
`Last auto approval` 显示上次自动批准的类别、去重节点数和时间。
`fallback` 表示候选未导入，仍运行此前快照，`verifying` 表示导入后等待健康接受。
结构阻断需要修正来源内容或相应本地策略，不通过批准日常轮换来解决。
每五分钟运行的桌面通知仅经 Noctalia 提示下载失败、导入被拒、回滚和健康失败。
成功更新及非阻断的审计结果保留在 `proxy-status`，不弹窗。
通知使用固定分类，不包含节点名、订阅 URL 或凭据。

### 门户登录

门户登录先显式切 A，再用实际字面地址运行 `firefox-portal http://192.168.1.1/`。
此入口使用全新数据、无扩展、单 IP 的 80/443 relay，15 分钟后终止。
跳转到另一地址会失败，审核后显式为新地址另开会话。
关闭后清理数据。
日常 Firefox 可留在 A 使用。

身份 Firefox 须恢复 B 后再启动。
应保留可离线启动的 generation 与登记密钥备份。
该命令不会创建恢复 generation 或更改 DNS 回退策略。

## 模式切换

使用 Nushell 的 `clash-on`、`clash-off`，它们记录特权意图，管理 GUI/helper 并等待一致状态。
启用运行时路由时，`clash-on` 在核心启动后最多等待 90 秒。该窗口覆盖 DNS 和 TUN 预热、使用当前路由事务的新健康证明进入 B，以及确认相关服务就绪。
关闭 GUI 保留代理意图和受管核心运行。停止受管代理访问使用 `clash-off`。

本机使用 `clash-rules`、`clash-proxy-only` 一键选择路由子模式并请求 B。
命令在终端返回包含模式的成功结果，或以非零状态返回失败阶段及可用原因。
从 C 重试、以保留的 `proxy-only` 选择启动、显式切换路由或恢复来源时，核心启动后最多等待 90 秒确认代理就绪。
它们不启动 GUI、不发送桌面通知，也不自动运行 `proxy-status`。
B 和 C 中，`proxy-status` 显示请求的路由及上次生效的路由、事务 epoch，以及观测到的核心模式、TCP 叶节点和 DNS、UDP、ICMP 覆盖。
底层命令为 `sudo proxy-control routing rules`、`sudo proxy-control routing proxy-only`。
`clash-off` 保留本次启动的选择，`clash-on` 复用它，重启恢复 rules。
Proxy-only 需要已接受的固定节点来源。若准备或完整健康检查失败，C 继续保护应用，重试时沿用请求的选择。

用 `clash-egress` 列出已接受来源的出口 ID，用 `clash-egress APPROVED_NODE_ID` 选择其中一个并请求严格 B。
将 `APPROVED_NODE_ID` 替换为列表中的完整 ID。底层入口为 `sudo proxy-control egress list` 和 `sudo proxy-control egress APPROVED_NODE_ID`。
出口变更经 C 关闭旧流和 association。Rules 保留 GUI 选组，严格模式通过此 root 事务更换出口。
所选出口在本次启动的 off/on 后保留。来源更改该节点后，须先在 rules 接受更新，再选择新 ID。
策略边界见[路由策略](proxy_CN.md#路由策略)。

底层系统命令为 `sudo proxy-control on`、`sudo proxy-control off`。
`on` 打开一次性配置导入窗口并启动 helper，受控来源本机由登记候选启动核心，兼容模式仍由 GUI 提供配置并启动核心。
`off` 清除授权并停止 helper。
桌面命令额外处理 GUI 进程。
A 中仅启动 `clash-verge` 不会授权核心。

| 命令 | 用途 |
|---|---|
| `sudo proxy-control preflight` | 检查受控来源的配置和资源，不改变网络意图 |
| `proxy-policy` | 查看当前网络权限和策略 |
| `proxy-control status auto --check` | 检查请求的模式是否就绪 |
| `proxy-control status proxy` | 查看代理状态 |
| `proxy-control wait proxy` | 最多等待 30 秒，检查代理是否就绪 |
| `proxy-control status direct` | 查看直连状态 |
| `firefox --check` | 检查浏览器启动条件，不开窗口 |

`status auto` 跟随请求的模式。
系统未就绪时，`--check` 返回非零状态，`wait` 每十秒显示一次进度。

| 工具 | 输出 |
|---|---|
| `proxy-status` | 模式、DNS、就绪、探针失败层、刷新结果和审计计数 |
| `clash-diff` | 比较候选与所选来源已接受基线的差异 |
| `proxy-status --plain` | 适合日志的纯文本 |
| `proxy-status --json` | 结构化状态 |
| `proxy-inspect` | 服务、链路、队列和资源的被动诊断 |
| `proxy-inspect --posture` | 监听绑定、声明的入站策略和本机信任状态 |
| `egress-audit` | 额外的主动网络探测 |

`--no-color` 或 `NO_COLOR` 可关闭状态面板的颜色。

`proxy-policy` 同时显示原生 UID/接口/CIDR 授权、核心 DIRECT 范围、远程目标授权和订阅信任。
这些权限相互独立：原生 restricted 与宽泛核心 DIRECT 范围并用时，连接本地代理入口的客户端仍可使用核心授权。
限制宿主应用访问内网时，应同时审阅 `directEgress.mode="restricted"` 和 `localTargetCidrs=[]`，再声明必需授权。
`proxy-inspect` 报告命令失败分类及退出码，不输出任意服务 stderr。

`proxy-inspect --posture` 查看调用者所在网络命名空间的 IPv4 和 IPv6 监听。
它分别显示绑定和身份，以及 LAN、tailnet 和路由访客的入站声明。
入站列描述策略，实际可达性还取决于网络状态、对端授权和服务配置。
代理端口提示用于人工复核，命令不会连接监听端口。
用 `--json` 获取结构化结果，并查看声明的控制器、浏览器和容器 Unix socket 元数据。

用 `proxy-inspect --posture --pid PID` 查看特定进程的启动环境，或用 `sudo proxy-inspect --posture --privileged` 检查指定系统单元。
报告只列跟踪的变量名及比对状态，还提供系统 CA bundle 摘要、SSH 状态和可读取的 Tailscale 设置摘要。
无法读取的字段显示未知。需查看已保存的桌面代理模式时，从桌面会话运行。
桌面配置通过断言保持 OpenSSH 关闭，并检查声明的宿主入站端口不含 TCP/22。

## 远程桌面

Sunshine 已启用，随 `graphical-session.target` 启动，将当前图形会话提供为 **Desktop**。
部署后先登录电脑桌面，在电脑本机打开 `https://localhost:47990` 设置凭据并批准 Moonlight 配对 PIN。
手机与电脑需登录同一获准的 tailnet，在 Moonlight 中手动添加电脑的 Tailscale IP，不依赖自动发现。

同一 overlay 地址可用于局域网和公网，仍受 tailnet 权限策略约束。
默认 `trustedLanCidrs` 为空，不直接开放物理 LAN IP 的远控访问。

管理界面仅接受本机客户端，默认 tailnet 防火墙也不放行该端口。
UPnP 关闭。
串流使用 [options.nix](../../system/config/network/options.nix) 的明确 TCP/UDP 清单。
手机到电脑的 grants 需另在 Tailscale 管理后台配置，仓库不包含该权限策略。

A 与健康 B 允许远控。
C 会切断会话，包括已建立的 UDP 流及已解密 tailnet 入站。
需要直连 A 时在本地使用 `clash-off`。

[sunshine.nix](../../system/programs/sunshine.nix) 选择 CUDA 构建变体。
配置的 `https://cache.nixos-cuda.org` 提供与 lock 和软件包选项所选 derivation 匹配的二进制缓存。
可用 `nix build --max-jobs 0 --no-link .#nixosConfigurations.lfour.config.services.sunshine.package` 检查缓存可用性，该命令禁用本地编译。

凭据和配对状态保存在用户的 Sunshine 目录，Desktop 入口和服务设置由 Nix 声明。
硬件捕获/编码与手机串流在部署后的图形会话中检查。

```sh
systemctl --user status sunshine.service
journalctl --user -b -u sunshine.service
sudo tailscale netcheck
sudo tailscale ping <手机的-tailnet-名称或-IP>
```

可选受限 B、Unbound DoT 开关和时间目标限制见[直连例外](egress_CN.md)。
受限 B 保留协商后的 Sunshine 媒体。
C 始终切断远控。

Tailscale 打标失败时，B 仍会阻断未打标的 root UDP。
应检查服务权限和 socket mark，需要直连时在本地运行 `clash-off`。
升级检查见[维护手册](maintenance_CN.md#验证)。
专用访客账户保留独立边界。

## 状态与定位

| 文件 | 含义 |
|---|---|
| `/run/proxy-mode/state.json` | 原子模式、原因、策略代次、revision 和更新时间 |
| `/run/proxy-mode/routing.json`、`routing-active` | 绑定本次启动的请求/实际子模式、epoch、不可变策略摘要及阶段；选定的策略/健康/审计文件 |
| `/run/proxy-mode/routing-firewall.json` | 仅 root 可读的外层防火墙预期模型及选定传输脚本摘要 |
| `/run/proxy-mode/health.json` | 探针覆盖范围、完整结论、HTTP/路由/DNS/TUN 独立诊断、证明来源与分层耗时 |
| `/run/proxy-mode/heartbeat.json` | 最近成功监督轮次 |
| `/run/netsec/state.json` | 验证结论、所验证代次与新鲜度 |
| `/run/dns-upstream/state.json` | DNS 模式/原因、加密策略、会话、预算、截止和 override |
| `/run/dns-policy/state.json` | Broker 租约状态与应用代次 |
| `/run/gost-relay/state.json` | Relay 观测与应用代次 |
| `/run/netsec/alerts/*.json` | 各 owner 告警。`/run/netsec/failed` 汇总 owner |
| `/run/proxy-mode/intent`、`core-seen` | 单次开机意图和崩溃检测标记 |
| `/run/proxy-mode/authorized-config`、`config-digest`、`authorize-window` | root 私有快照授权与一次性导入凭据 |
| `/run/proxy-mode/profile-refresh.json` | 最近刷新结果、digest、固定原因、审计计数、上次自动批准和 20 条历史，权限 0644 |
| `/run/proxy-mode/profile-refresh-pin.json` | 被拒候选 digest、健康失败标记与一小时 TTL，权限 0644 |
| `/run/proxy-mode/source-fetch.json` | 绑定本次启动的下载结果、固定失败分类与可用的 HTTP 状态码，权限 0644 |
| `/run/proxy-mode/profile-refresh-verifying.json` | root 私有待验证事务及上一快照路径 |
| `/run/proxy-mode/profile-refresh-settled.json`、`profile-refresh-commit.json`、`profile-refresh-restart.json` | root 私有 settled 授权、提交中断恢复和待完成内核恢复 |
| `/run/proxy-mode/effective-config.json`、`boot-id` | 绑定共享 boot identity 的 effective YAML 摘要。`effective-config-sha256` 是兼容镜像 |
| `/run/proxy-mode/health-observations.json` | 本次启动最近 20 次完整探测结果，包含脱敏阶段、分层耗时和快照摘要 |
| `/run/proxy-mode/health-failure.json` | 最近失败探针的固定原因、可用的脱敏 `failure_stage`、分层耗时、boot identity 与快照摘要，恢复后仍保留 |

消费者读取原子 JSON，独立 `status`、`reason`、`version`、`result`、`verified-version` 是兼容镜像。
仅原因变化更新 `revision`。
内核策略提交更新 `policy_version`，`version` 是其别名。
跨新代次的检查结果丢弃。

就绪要求模式、DNS、broker、relay 同代次且新鲜，没有告警，并有新鲜验证。
验证器的 `ok` 和 `degraded` 都表示硬性检查已通过。
`degraded` 另有观察性警告，显示为 `Passed · warnings`。
失败或未知结果不算就绪。
Broker 必须为 `applied`。B 要求 DNS 状态为 `proxy`，严格 DNS 下的 A 接受 `dot` 或 `dot-priming`。
消费者默认有效期 120 秒，验证为 420 秒以容纳五分钟定时器及有界执行。
Boot identity 和单调时间戳拒绝跨开机、未来与陈旧记录。

| 原因 | 定位方向 |
|---|---|
| `request`、`reload` | 转换中等待核心健康与验证 |
| `unauthorized-core`、`core-gone` | 意图、启动器/helper 与核心 cgroup |
| `no-tun`、`tun` | 设备、受管表/规则、实际选路与 fake-IP HTTP |
| `config` | 快照摘要、受管 API 和 TCP/UDP 监听身份 |
| `route` | 日志实际叶子为 DIRECT/REJECT 或与选择图不符 |
| `obs`、`obs:1/3`、`obs:2/3`、`obs:3/3` | HTTP 完成、精确源端口日志和选择观测 |
| `resolver-error`、`split-error` | DNS 身份或批准 Wi-Fi profile/租约 |
| DNS `proxy-priming`、`degraded-proxy` | B 正在确认选定的核心解析器或探测已失败。检查核心 DNS 监听及选定的 DoH 路径 |
| DNS `degraded-dot-only` | 检查 A 的已认证 DoT 上游及 DNSSEC 探测结果 |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

部分日志和授权文件需要 sudo。
诊断输出中应去掉快照、订阅配置等敏感内容。

### 拒绝计数器

运行 `sudo proxy-inspect --drops`，读取 `inet lf_filter` 中已有拒绝计数器的精简 JSON 视图。
普通 `proxy-inspect` 也在 `drops` 字段提供同一视图。
命令只读本地状态和 nftables，不改规则、不清零计数、不发送探测。

```sh
sudo proxy-inspect --drops | jq '{available,error,policy,state_consistent,groups:[.groups[] | select((.packets // 0) > 0)]}'
```

每组给出稳定的 `reason`、`role`、所在 `chain` 及已计数的包数/字节数。
路由访客角色使用配置的接口名。
policy 记录包含所观测模式、转换原因和代次。
`state_consistent=false` 表示状态不可读或采集期间发生变化。
此视图不证明就绪。

| 原因 | 计数的拒绝 |
|---|---|
| `mode-c-output`、`mode-c-input` | C 的输出或回复/入站 guard |
| `guest-source-policy` | 访客源地址超出声明范围，包括未声明的地址族 |
| `guest-target-policy` | 路由访客的受保护或不支持目标 |
| `dns-client-policy` | 应用访问内部解析器入口 |
| `dns-listener-identity`、`proxy-listener-identity` | 未授权本地 socket UID 的回复 |
| `proxy-inlet-access` | 对受保护本地代理端口的显式访问 |

计数仅覆盖列出链中带 counter 的 drop。
`uncounted_rules` 给出选中但没有计数器的 drop 数。
`installed=false` 且计数为 null 表示链不存在。
零计数不证明入口已启用或健康。
读取/解析失败返回 `available=false`、固定错误类别和空 groups，`--drops` 退出 1。
缺少 nftables 所需权限时为 `permission-denied`。

计数从各条规则最近一次安装或 flush 开始，重载、修复和模式变化可能重置部分计数。
比较快照时须结合该生命周期和策略代次。
历史总量不能把某次连接及其原始/NAT 目标归因到当前模式。
输出组数由配置角色固定，不含逐包日志、目标地址或原始规则。
流量增大只改变数字总量。

### 刷新状态

`proxy-status` 的 `Auto update` 显示更新调度，`Profile refresh` 和 `Update result` 显示最近刷新，`Profile vs runtime` 显示 GUI 副本信息。
受控来源将 GUI 副本视为展示用途，兼容导入则比较 GUI 与已导入快照的摘要。
`proxy-policy` 包含 `profile_refresh`，`proxy-inspect` 提供最多 20 条脱敏刷新历史。

`Subscription download` 将下载进度和错误与导入结果分开显示。
`deferred` 表示网络保护使本次尝试延期，不是下载失败，也不触发弹窗。
`Content audit` 显示内容自动批准，不表示健康检查已经接受。
`restored` 表示启动时复用了符合当前策略的已接受快照。
`verifying` 表示已导入快照正在等待健康结果。
最近更新失败与订阅年龄分别显示，原有接受时间予以保留。
受控来源以 root 记录为依据，GUI 更新时间不代表 root 更新成功。
`fallback` 表示候选被拒绝，旧授权快照保留。
`rolled_back` 表示已恢复快照授权并请求替换核心。
核心是否恢复须查看网络就绪字段。
摘要不同只表示内容不同，不能据此判断哪个文件更新。

切换订阅仍使用同一份 root 策略。
新的 hosts 覆盖须匹配 `subscriptionAuditAllowedHosts`，切换动作本身不会扩大该权限。
探测目标和预期路由均可配置。
受保护路由仍无法确定时，`enforce` 保留旧快照，应先根据审计分类定位约束，再决定策略。

用户通知 timer 每五分钟读取本地文件，将更新和健康失败合并为每轮一条消息。
刷新失败按结果与完整摘要去重，下载失败按尝试时间去重，健康失败按状态迁移提醒。
成功更新、健康恢复、审计警告、订阅年龄及 GUI 副本差异不触发弹窗。
去重状态私有存于 `$XDG_STATE_HOME/clash-verge/refresh-notified.json`，发送成功后才推进。
timer 不下载订阅。

### 订阅连通性

显式运行 `proxy-subscription --check --json` 检查当前订阅。
命令从 GUI 索引读取地址，报告 HTTP 状态、传输路径、耗时、字节数、HTTPS 和重定向次数。
提供 `--etag` 时发送条件 GET，没有 validator 时发送普通 GET。

检查先使用当前网络策略允许的直连路径，传输失败后尝试配置的 mixed 端口。
403 等完整 HTTP 响应直接报告，不再通过代理重试。
命令不写 profile、不导入配置，也不推进更新时间。

总超时默认 15 秒，覆盖域名解析、两条传输路径、重定向和正文读取。
`--timeout` 支持 1–120 秒。
响应上限为 8 MiB，最多允许三次同源重定向，跨源跳转和 HTTPS 降级均拒绝。
输出不包含 URL、响应正文和 validator 原文。
`proxy-inspect --deep` 另提供服务重启次数和固定 journal 查询命令，供有权限的操作者排障。

## 协调与防火墙恢复

Socket 目录事件、TUN link 监视、nft 事件、NetworkManager dispatcher 和 dhcpcd hook 唤醒监督器。
自适应轮询后备为转换中两秒、执行模式最多五秒、直连最多 60 秒。
模式监督器有 90 秒 watchdog。
取消的探针不算失败观测。

构建期的[预期规则](../../system/config/network/proxy/coordinator.nix) 在 LKL 用户态内核中用同版 nft 生成。
Verifier 比较所属表、hook、priority、policy 和完整有序表达式，仅规范化 handle、counter、集合元素顺序和受严格约束的 DNS 租约。
读取/解析错误是失败，不当作空成功。
没有核心但正确安装 C 也能通过。

`nftables-verify` 报告失败和告警后仍退出零，以便修复/重建继续。
应看 JSON 结论，不能只看 unit 退出码。
各组件只清自己的告警，忙碌轮次保留排队验证请求。

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload 原子重装所属静态策略和安全模式。
代理意图先回 C，再由健康门恢复 B。
主表丢失后，独立守卫继续阻断代理意图下的流量。若要热回滚到引入 `inet lf_policy_guard` 之前的代次，先用 `clash-off` 明确进入 A，清除独立守卫的逐包要求。
Stop 保留已加载规则。
`nftables-verify-repair.service` 显式修复并复验，`nftables-recover` 重试失败的 nftables unit。
不要清空全局 ruleset。

## 防火墙表迁移

`proxy-control migrate-firewall` 显式删除 `inet filter`、`ip proxymode_nat` 和 `ip6 proxymode_nat`，并安装[保留表](network_CN.md#防火墙生命周期)。
普通启动、重载和修复保留这三个名称。
执行迁移前须确认这些表归本配置所有。

查看迁移目标并确认归属：

```sh
sudo nft list table inet filter
sudo nft list table ip proxymode_nat
sudo nft list table ip6 proxymode_nat
```

确认归属后执行迁移，并检查安装的策略：

```sh
sudo proxy-control migrate-firewall
sudo systemctl start nftables-verify.service
```

该 root 命令持有 operator 和 policy 锁，以一次 nft 事务提交三个目标表的删除、静态规则和安全模式。
事务被拒绝时保留已安装的规则和目标表。
目标表允许缺失，因此在名称仍归本配置所有时支持重复执行。

代理意图或核心仍在运行时先安装 C，监督器在健康检查通过后开放 B。
直连意图且没有核心时安装 A。
迁移保留意图、授权和运行中的核心。
使用上文诊断命令检查模式和验证 JSON。
清理仅在本命令执行期间发生，其它服务的表由各自所有者管理。

## 核心命名空间恢复

宿主的 `Mihomo` 接口是受管 veth。
用 `sudo ip -n lf-clash-core link` 和 `sudo ip netns exec lf-clash-core nft list table inet mihomo` 查看实际核心 TUN 和 auto-redirect 表。
完整就绪使用 `proxy-control status proxy --check`，仅有宿主接口不能证明已就绪。

`clash-on` 停止旧 helper 后重建私有命名空间，保持配置的来源授权流程，并断开已有核心连接。
物理上行变化和恢复钩子自动重启已经授权的核心。
如果 LAN/VPN 与内部地址冲突，通过 `my.proxy.coreLink` 选择未占用的私有 /30 与 /64，各子网及其 host/core 端点应一起调整，不删除重叠检查来恢复连接。
内部 MAC 为不同的本地单播地址，与物理 MAC 随机化独立。
激活前保留上一系统 generation。

返回使用宿主 TUN 的 generation 时：

1. 执行 `sudo proxy-control off`，等待 A。
2. 停止 `clash-core-network.service`。清理只移除其拥有的 namespace、veth 和路由优先级。即使存在残留所有权标记，也拒绝删除其它接口或旧版 TUN。
3. 激活选定 generation，按正常 `clash-on` 流程启动。

## 配置入口

| 来源 | 设置 |
|---|---|
| [machine.nix](../../system/config/machine.nix) | 用户、接口及匹配键、UID、端口、mark、网段和上游 |
| [options.nix](../../system/config/network/proxy/options.nix) | TUN、表 2022、fake-IP、探针、开机意图、proxyOnly、dnsEncryption、共享域名过滤、访客、split DNS 和新鲜度 |
| [profile.nix](../../system/config/network/proxy/profile.nix) | root 受管 DNS/TUN/profile 约定 |
| [routing.nix](../../system/config/network/proxy/routing.nix) | `runtimeRouting`、不可变 rules/proxy-only 策略及 `proxyOnlyDnsUpstreams` |
| [options.nix](../../system/config/network/options.nix) | 宿主/访客私网、逐接口访客信任/源地址、独立 socket 访客账户、宿主端口、热点、tailnet 和 Wi-Fi MAC |
| [cvr-merge.nix](../../home/config/clash/cvr-merge.nix) | 匹配的 GUI Merge 及路由增强 |
| [firefox.nix](../../home/programs/firefox/firefox.nix)、[options.nix](../../home/programs/firefox/options.nix) | 原生浏览器、networkPath、nvidiaAcceleration、隐私、策略、字体和外层沙箱 |

TUN 默认 `Mihomo`，表 2022、规则优先级 9000、内部 mark 8227/8228、fallback priority 32768、file descriptor 0，由 parser、模板和健康检查共用。
`tunMode=false` 选择 HTTP-only/redir-host，不再要求 TUN。
Fake-IP 网络由 `fakeIpRange` 推导，避免单独修改消费者。

`runtimeRouting` 在模块中默认为 false，本机配置已启用。
它要求受控来源、本地 DNSSEC、严格加密 DNS、隔离 TUN、内核授权租约，以及作为 rules 开机默认的 `proxyOnly=false`。
旧 `proxyOnly` 仍用于不启用运行时切换的构建期策略。
`proxyOnlyDnsUpstreams` 接受认证的字面地址 HTTPS 解析器，始终绑定远程组。
已接受快照分别保存在 `approved/rules`、`approved/proxy-only`，包含本地重渲染所需的原始 Rule 数据和固定节点。
探测参数变化时，运行时路由用经过摘要校验的原始订阅规则重新渲染，再按当前策略审计结果。
旧格式快照的原始规则无法可靠恢复时，需要刷新已登记来源并导入新的下载代。

需要不同访客信任级别时，应显式选择开发网桥，并只为其它访客授权必要的私网服务。
宿主 socket 后端另需独立固定账户。

选择空闲 UID 与互不重叠的 subordinate 范围：

```nix
my.hardening = {
  guestNetworks.virbr0.trust = "trusted";
  guestPrivateExceptions = [{
    interface = "waydroid0";
    cidr = "192.168.1.20/32";
    tcpPorts = [ 443 ];
  }];
  socketGuestUsers = [ "guestnet" ];
  socketGuestPrivateExceptions = [{
    user = "guestnet";
    interface = "ens1";
    cidr = "192.168.1.20/32";
    tcpPorts = [ 443 ];
  }];
};
users.users.guestnet = {
  isNormalUser = true;
  uid = 1100;
};
```

示例地址需要替换。
内建网桥源地址/网关默认来自 `my.machine`。
额外 `guestInterfaces` 必须有规范且互不重叠的 `guestNetworks` 源 CIDR。
仅在需要开放宿主服务时，才为相应角色添加 `hostTcpPorts` / `hostUdpPorts`，目标限定于声明网关。
IPv6 需显式 ULA 源地址/网关，访客 IPv6 公网仍拒绝。
热点只有明确调整自身 trust 后才变为可信。

先安装/协调完整 nft 策略，再以 `guestnet` 运行 pasta 或 rootless Docker。
Docker 用户单元会为受限账户清除继承的显式代理环境变量，并使用声明的 rootless 包与设置。
新增/更改身份或委派 UID 映射前停止旧后端。
为运行中的后端撤销私网访问时，保持身份声明、移除授权并重载防火墙，已跟踪连接的双向流量都会切断。

仅设置 `socketGuestUsers` 不会迁移桌面容器。
这些账户使用单独的真实地址 DNS 路径，没有明文/split DNS 后备。
见[访客边界](network_CN.md#访客与容器)。

Wi-Fi profile 显式的克隆 MAC 优先于 `my.hardening.wifi.clonedMacAddress`。
否则默认 `stable-ssid` 为相同 SSID 保留相同 MAC。
`permanent` 选择硬件地址，`preserve` 保持当前地址。
需要协调身份变化时，声明 `my.hardening.wifi.identities`，查看 `network-identity plan UUID`，关闭自动连接并断开后再执行 `apply` 或 `restore`。
完整步骤、有线策略和 DHCP 状态准备见[链路身份](identity_CN.md)。

核心内部服务访问由[目标授权](proxy_CN.md#域名目标)单独管理：`localTargetCidrs` 限定本地 DIRECT，`remoteTargetGrants` 按实际叶节点、核心入口、协议和端口授权远程内部地址，`nat64Prefixes` 声明已知翻译前缀。
内部后缀只允许本地解析私网答案，不授权节点代解析或拨号。

Gost CONNECT 在核心中是 `HTTPS`。
修改后需重建并有意导入/重启核心。
A 字面门户、C 阻断与原生直连授权各自保持独立。

## Hermes 容器

[`system/programs/hermes/`](../../system/programs/hermes) 集中存放 agent 配置、技能、邮件 CLI 与容器后端。
后端在专用账户的 user manager 中运行 `hermes-docker.service`，由 `hermes-containers.socket` 和 `.service` 向 Hermes 提供固定 Unix 字节流。
专用账户的私有 runtime 和数据目录保留独立权限。

| 资源 | 默认位置 |
|---|---|
| Hermes agent 数据与工作区 | `/var/lib/hermes` |
| 受限后端 HOME / Docker 数据 | `/var/lib/hermes-containers` / `/var/lib/hermes-containers/docker` |
| Hermes Docker API | `unix:///run/hermes-containers/docker.sock` |

Gateway、dashboard 和 Hermes 登录 shell 将 `DOCKER_HOST` 指向该 API。
Socket 模式为 0660，仅允许后端账户及 `hermes-container-clients` 成员访问。
桌面继续使用自己的 Docker 入口。
直接以 Hermes 运行的本地 terminal、MCP 和浏览器工具仍使用宿主代理策略。

后端清除继承的代理环境变量，使用过滤后的真实地址 DNS。
A 允许公网 IPv4 直连，B 按宿主公网策略经过受管核心，C 切断应用流量。
私网、元数据、fake-IP 目标和宿主代理入口默认没有授权。
默认 Docker 网桥没有外部 IPv6 路径。
其它已支持的原生后端路径仍受 UID 守卫的双栈检查约束。

```sh
sudo systemctl status hermes-containers.socket hermes-containers.service
sudo systemctl --user --machine=hermes-containers@.host status hermes-docker.service
sudo -u hermes env DOCKER_HOST=unix:///run/hermes-containers/docker.sock docker info --format '{{.DockerRootDir}}'
```

专用账户 user manager 启动前，retirement unit 先停止 Hermes 旧 user Docker。
仍有旧 dockerd 时拒绝启动。
全局 Docker user unit 排除 Hermes 和专用后端账户，后者使用独立 unit。
新后端继续受 `user.slice` 继承的保留端口 bind 限制约束。

`/var/lib/hermes` 中的 agent 数据，以及旧账户的 Docker 镜像和卷都保留原位。
镜像和卷不会自动导入新后端。

迁移需要保留的容器数据时：

1. 激活前停止业务写入，使用旧账户的 UID 映射导出镜像和卷。
2. 通过新 Docker 入口加载镜像并恢复卷。
3. 检查恢复结果后，再处理旧数据和 UID 映射。

递归修改所有权不能代替迁移。
返回旧后端配置前，先停止专用 daemon 和两个 API unit。

## Hermes time MCP

`time` MCP 保留 stdio 接口，使用 NixOS 配置的时区。桥接进程清空环境后连接 `/run/hermes-time/mcp.sock`，该 socket 仅允许 root 和 `hermes-time-clients` 组访问。
每条连接由 `untrusted.slice` 中以 `hermes-time` 账户启动的 `hermes-time@.service` 实例处理。服务可访问只读 Nix store 和临时 `/work`，私有网络命名空间内仅允许 Unix socket。

Gateway 和 dashboard 在 systemd 启动时清除继承的代理与 CA 覆盖键，并使用声明的系统 CA bundle。
兼容模式为 gateway 恢复受管的本地 Gost 代理。联网 MCP 和浏览器工具使用各自声明的选项。
`proxy-inspect --posture` 报告进程启动环境。Hermes 在进程内加载 profile `.env`，其中的覆盖值可在启动后生效。

```sh
systemctl status hermes-time.socket
systemctl list-units 'hermes-time@*.service'
```

## 离线恢复

需要直连时用 `clash-off`。

默认 `dnsEncryption="opportunistic"` 下，A 中门户/DNS 恢复先读 DNS 状态和历史，必要时显式开启有界续租 override：

```sh
sudo touch /run/dns-upstream/force-plaintext
# 恢复后删除，重启也会清除。
sudo rm /run/dns-upstream/force-plaintext
```

它允许明文 DNS 并继续计入预算，不能越过 C 的出站 guard，范围见 [DNS](dns_CN.md)。

`my.proxy.dnsEncryption="strict"` 忽略 override，不授予明文后备。
`clash-off` 选择的 A 也保留加密 DNS。
可尝试字面 HTTP 门户地址、更换上行或修复认证 DoT 配置。
门户若依赖不可用的公网 DNS，需要主动改变策略，例如离线选择已构建的 `opportunistic` generation。
在出行依赖严格模式前先准备可用 generation。
私网 split DNS 和 MagicDNS 仍受各自批准及模式约束。

系统 generation 故障时，按以下顺序恢复：

1. 从 systemd-boot 菜单选择旧条目。菜单保留八个 generation。
2. 如果菜单中没有需要的条目，检查 `/nix/var/nix/profiles/system-*-link` 中保留的系统。以 root 选择对应 store 路径并安装启动项：

   ```sh
   nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-path>
   /nix/var/nix/profiles/system/bin/switch-to-configuration boot
   ```

3. 如果所有已安装系统都无法启动，使用 NixOS ISO 解锁并挂载根目录和 ESP，再通过 `nixos-enter --root /mnt` 进入系统恢复。

## 配置检查

[维护手册](maintenance_CN.md#验证)列出了配置和依赖变更所需的检查。
