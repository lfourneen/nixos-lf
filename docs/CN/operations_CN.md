# 网络操作与诊断

[English](../operations.md) | 简体中文 | [文档目录](README_CN.md)

## GUI 控制权限

Clash Verge GUI 直接用 Nixpkgs 的上游构建，独立编译的 Rust 后台服务则打上了本地补丁。首页、设置页、托盘菜单和快捷键里的 System Proxy、TUN 和核心模式按钮都还能点、也会写入 GUI 本地偏好，但在碰到底层系统或受管核心前会被静默丢弃：

- **启动 shim**：System Proxy、PAC 和代理守护调用在启动时被换成只丢弃写入的 shim；私有挂载命名空间遮蔽绝对 helper 路径（以内存 GSettings 后端和 KDE 5 helper 名称作后备），确保启动和退出清理都不改系统代理设置。
- **控制器策略**：对 `mode`、`tun` 的 `PATCH /configs` 和 `PUT /configs` 重载都返回 `204` 但不应用，其余配置写操作、禁用规则、升级和重启一律返回 `403`。
- **调用者身份**：helper 按内核 Unix peer 凭据认人，GUI 的停止/重启请求动不了受管核心，受控来源只能由 root 启动。
- **受管解析器**：最终校验前把核心模式钉死为 `rule`，并用 root 策略覆盖整段 `tun`，所以本地偏好既改不了实际状态，也不会让重启因校验失败而卡住。

GUI 界面里显示的只是它自己的本地偏好，实际状态以控制器、`proxy-status` 或受管核心为准（策略缺失或损坏也不会开放 GUI 写权限）。受管组内选节点、关连接、测速、状态读取和看日志都照常可用。切换系统路由子模式请用 `clash-rules` 或 `clash-proxy-only`——两者渲染出来的核心设置都是 `mode: rule`，所以 GUI 上的 Rule 只代表核心在跑规则表，不代表当前是哪个路由子模式（具体看 `proxy-status`）。

## 来源登记

私有来源清单存在 Sops 加密的 [system/secrets/proxy.yaml](../../system/secrets/proxy.yaml) 里，激活时渲染成 root 私有的 `/run/secrets/proxy_source`（权限 `0400`）。在 Sops 编辑器中，`proxy_source` 是一个包含 `sources` 数组的 JSON 字符串：

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

每个来源有一条主 `subscription` 和可选的 HTTP `providers`（新增订阅要配独立 ID，同一来源内的 provider 须匹配订阅声明的名称和 URL）。端点可选字段包括最多四个精确 HTTPS `redirects` 和 `user-agent`（默认 `clash.meta`）；`"gui-profile":"REMOTE_PROFILE_UID"` 则把 Verge 本地 `profiles.yaml` 里的精确 UID 映射过来，用来授权选择来源而不导入 GUI 的 URL 或 YAML。

### 添加与切换来源

运行 `sops edit system/secrets/proxy.yaml`，在 `sources` 里追加一条带唯一 ID 的记录（清单支持 1–256 条），重建部署后再添加来源：

```sh
sudo nixos-rebuild switch --flake .#lfour
sudo clash-source add SOURCE_ID
```

核对终端打出来的 `digest` 和 `previous` 版本确认无误后输入 `yes`（broker 只登记这一条并复核期间定义和历史版本没变；URL 和凭据始终保密）。`add` 会用无特权 worker 下载、选中该来源并等待现有 verifier：处于 **A** 时通过受保护启动事务请求代理；处于 **B** 时沿用当前路由策略，不用另跑 `clash-on` 或 `clash-rules`（在 **A** 或 **B** 下的显式命令会跳过自动更新的 90 秒稳定期立刻执行；若当前在 **C**，先恢复 **B** 或切到 **A** 再下载）。

终端最后会输出 `requested_source`、`active_source`、`result` 和 `reason`（`accepted` 退出 `0`，拒绝、回退、回滚或验证超时退出非零）。即使验证过了也仍要走完正常健康观察期，可用 `proxy-status` 查看 `Active source` 和 `Dropped leaves`（发生回退时，活动来源可能与请求的来源不同）。

想对已登记来源直接拉取、选中并验证，跑 `sudo clash-source use SOURCE_ID`（加 `--revision REVIEWED_DIGEST` 可强制核对版本；下载失败保留原选择，在 `proxy-only` 下候选仍须包含已选固定出口，想换出口先在 `rules` 接受更新再选新出口）。底层命令 `review FILE --id SOURCE_ID` 和 `register FILE --id SOURCE_ID --digest REVIEWED_DIGEST --previous PREVIOUS_DIGEST` 也保留着（首次登记用 `none` 作上一修订；不带 `--id` 的 `review` 会列出清单，多条清单时 `register` 须指定 ID）。

更新登记会让该来源的旧下载和批准失效：已跑着的核心保留原来源身份，已接受快照也保留原修订供审计比较，但重启或回滚必须用符合当前登记修订的快照（先下载并接受新修订再依赖离线启动；`sudo clash-source list` 可看选择、活动授权、修订、已接受摘要和本地验证结果）。从 Sops 清单删掉条目不会撤销本地登记，需要用 `sudo clash-source revoke SOURCE_ID`（可加 `--revision REVIEWED_DIGEST`）移除本地登记及排队任务和快照（撤销活动来源会进 **C**、清掉 **B** 租约并停核心；撤销非活动来源则不影响当前授权）。开启运行时路由且处于 **C** 时，可以先用 `sudo clash-source use SOURCE_ID --select-only` 选好替代来源，再用 `sudo proxy-control recover` 拉取恢复。

`clash-source-select.path` 盯着 GUI 的 `profiles.yaml`，切到已映射 UID 就会请求对应来源（未知 profile 不动选择，改 GUI URL/YAML/脚本也不授新权限）。如果 GUI 选择和实际活动来源对不上，查一下这几个日志：

```bash
journalctl -u clash-source-select -u clash-source-download -u clash-source-apply
```

在 **A** 下发起 GUI 请求会下载并选中来源，留给下次 `clash-on` 用。

`clash-source refresh` 只准备候选；想直接触发自动下载和应用流程可以启动 `clash-source-refresh.service`。显式跑 `clash-on` 时，有已接受快照就先用它启动并记一次待处理更新，等 **B** 在同一次启动、快照、策略和路由选择下持续健康 90 秒，协调器就会拉起下载（离开健康 **B** 重置计时，**C** 中继续排队，`clash-off` 则清空队列）。root timer 开机约两分钟后也会按期检查（哪怕关掉 GUI 照样跑；每轮最多试三次、间隔 15 秒、总超时 210 秒；若导入或回滚尚未收尾，候选按摘要排队等待应用）。启动 Verge 时，包装器会把已登记配置的 GUI URL 指向本机受控入口（已开着的旧窗口重开一次生效），点 Verge 的刷新按钮走同一队列，等候选生效或确认等价才返回成功；Verge 在 **B** 里用自己的定时器，关掉后由 root 读保存的间隔和自动更新开关（默认 30 分钟，设为 `0` 停用定时刷新）。

只要当前快照依然获授权且完整、通过新鲜完整健康与就绪检查及所需 **B** 租约，被拒或未变化的候选就会让现核心和已有连接继续跑（当前授权失效或健康失败才走 **C** 恢复）。真正要切换时会先入 **C**，经 helper IPC 换核、重跑导入检查并等代理健康后再回 **B**。worker 失败分 `fetch`、`parse`、`render` 和 `pin-addresses`（`proxy-status` 的 `Subscription download` 会附带 DNS/TLS/连接/HTTP 失败分类、HTTP 状态码和等待应用状态；`Profile refresh` 给审计和健康结论；`Dropped leaves` 统计剔除数；`Leaf session sample` 报抽查到的认证拒绝和会话不可用）。过滤掉不安全的 TLS 节点后，解析失败和回环 IP 占位节点最多丢 `max(1, remaining_node_count // 5)` 个，非公网答案或非公网字面端点则直接拒掉候选（留住有效回退，没有回退就停在 **C**）。

### 恢复已登记来源

启用运行时路由并在 **C** 中保留代理意图时，可以先尝试应用所选来源最近一次下载的候选：

```sh
sudo proxy-control recover --cached
```

命令会在停核心前先检查本机下载（须不同于已接受快照且匹配当前登记修订和路由策略，同样要过内容审计和健康检查才进 **B**；不可用时命令和 `proxy-status` 会写明原因）。没有兼容缓存就抓一份新的：

```sh
sudo proxy-control recover
```

两种操作期间，**C** 都继续阻断普通应用和访客的公网访问。专用解析器经认证 DoT 上游做本地 DNSSEC 验证，下载进程对登记的 HTTPS 端点用固定解析结果、校验 TLS 证书并只跟精确跳转，防火墙按服务身份、物理接口、源/目标地址和端口把恢复流量锁在 55 秒内核租约内（收到上行变更或休眠提前撤权）。下载失败会保持 **C** 并留住已接受基线；在 `proxy-only` 下下载内容还得包含已选固定出口（要换出口看下面流程；没开运行时路由的配置在 **A** 里走手动来源流程）。

### 只读查看订阅变化

本机平时会自动导入正常节点轮换，想看看当前候选和已接受基线差在哪，跑：

```sh
clash-diff
```

命令通过 `sudo` 读本地快照，不下载、不写批准记录，在 **A**、**B**、**C** 下都不改网络模式。输出节点地址、端口、TLS 名称、分组和规则，同时隐藏认证值和嵌套传输参数（`clash-diff --json` 输出 JSON），纯粹用来排查，不是更新前提。

`proxy-status` 的 `Profile refresh` 和 `Last auto approval` 分别展示刷新结果、审计分类计数以及上次自动批准的去重节点数和时间（`fallback` 代表候选被拒、沿用旧快照；`verifying` 代表正在等健康接受）。遇到结构阻断得去改来源内容或本地策略，别靠批准日常轮换硬过。用户通知 timer 每五分钟读一次本地文件，只把下载失败、导入被拒、回滚和健康失败合并成每轮一条 Noctalia 通知（固定分类，不含节点名、URL 或凭据；成功更新、恢复、审计警告、订阅年龄和 GUI 副本差异不弹窗）。

### 门户登录

需要过门户登录时，先显式切到 **A**（`clash-off`），再用实际字面 IP 跑 `firefox-portal http://192.168.1.1/`。它会拉起一个无扩展临时会话，背后是限死单 IP 80/443 端口、最多活 15 分钟的 relay（跳转到别的 IP 会失败，审阅后为新地址另开一次即可），关掉就清数据。日常 Firefox 可以留在 **A** 用，`firefox-identity` 则等恢复 **B** 再开。平时留好可离线启动的 generation 和登记密钥备份——这个命令不建恢复 generation 也不改 DNS 回退策略。

## 模式切换

在 Nushell 里用 `clash-on` 和 `clash-off` 切换。开启运行时路由时，`clash-on` 每次起核心最多等 90 秒完成 DNS/TUN 预热并拿到就绪证明；要是恢复的受控快照连续在数据面翻车，`clash-on` 会在 **C** 里试一次兼容缓存更新（本机还开了 `my.proxy.startupRecoveryFetch`，缓存不可用时允许在 **C** 受限下载一次——共用从首个核心启动算起的 210 秒就绪截止）。关掉 GUI 不影响代理意图和核心运行，彻底停代理用 `clash-off`。

`clash-rules` 和 `clash-proxy-only` 一键选好路由子模式并请求 **B**（核心启动后最多等 90 秒确认就绪），只在终端返回模式或失败阶段，不开 GUI、不发通知、也不跑 `proxy-status`（底层是 `sudo proxy-control routing rules` 和 `sudo proxy-control routing proxy-only`）。`clash-off` 会为本次启动留住选择，`clash-on` 复用它，重启恢复 `rules`。`proxy-only` 要求已有已接受的固定节点来源，准备或健康检查挂了就停在 **C** 等重试。

用 `clash-egress` 列出已接受来源的出口 ID，再把完整 ID 填进 `clash-egress APPROVED_NODE_ID` 切到严格 **B**（底层是 `sudo proxy-control egress list` 和 `sudo proxy-control egress APPROVED_NODE_ID`）。换出口会经 **C** 关掉旧流和 association，本次启动 off/on 后保留，来源改了节点定义则得先在 `rules` 接受更新再选新 ID（见[路由策略](proxy_CN.md#路由策略)）。

底层系统命令是 `sudo proxy-control on`（打开一次性导入窗口并起 helper，受控来源用登记候选起核心，兼容模式由 GUI 供配置）和 `sudo proxy-control off`（清授权、停 helper 并确认直连防火墙与路由生效；`clash-off` 还会后台关掉 GUI，直连 DNS 就绪用 `proxy-status` 单独看）。在 **A** 里光起 `clash-verge` 不会授权核心。

| 命令 | 用途 |
|---|---|
| `sudo proxy-control preflight` | 检查受控来源的配置和资源，不改变网络意图 |
| `proxy-policy` | 查看当前网络权限和策略 |
| `proxy-control status auto --check` | 检查请求的模式是否就绪 |
| `proxy-control status proxy` | 查看代理状态 |
| `proxy-control wait proxy` | 最多等待 30 秒，检查代理是否就绪 |
| `proxy-control status direct` | 查看直连状态 |
| `firefox --check` | 检查浏览器启动条件，不开窗口 |

`status auto` 跟随请求的模式，未就绪时 `--check` 返回非零，`wait` 每十秒报一次进度。

| 工具 | 输出 |
|---|---|
| `proxy-status` | 模式、DNS、就绪、探针失败层、刷新结果和审计计数 |
| `clash-diff` | 比较候选与所选来源已接受基线的差异 |
| `proxy-status --plain` | 适合日志的纯文本 |
| `proxy-status --json` | 结构化状态 |
| `proxy-inspect` | 服务、链路、队列和资源的被动诊断 |
| `proxy-inspect --posture` | 监听绑定、声明的入站策略和本机信任状态 |
| `egress-audit` | 额外的主动网络探测 |

用 `--no-color` 或 `NO_COLOR` 可以关掉状态面板的颜色。

`proxy-policy` 把原生 UID/接口/CIDR 授权、核心 `DIRECT` 范围、远程目标授权和订阅信任列在一起。它们相互独立：如果原生设为 `restricted` 但核心 `DIRECT` 范围很宽，连本地代理入口的客户端照样能用核心授权——真想锁死宿主应用访问内网，得同时配好 `directEgress.mode="restricted"` 和 `localTargetCidrs=[]`，再声明必需授权。`proxy-inspect` 只报失败分类和退出码，不吐任意服务 stderr。

`proxy-inspect --posture` 按调用者命名空间列出 IPv4/IPv6 监听，并把绑定/身份与 LAN、tailnet、路由访客的入站声明分开显示（代理端口提示仅供人工复核，不主动连端口；`--json` 补充结构化结果和声明的控制/浏览器/容器 Unix socket 元数据）。查特定进程启动环境用 `proxy-inspect --posture --pid PID`，查系统单元用 `sudo proxy-inspect --posture --privileged`（只列跟踪的变量名及比对状态、CA bundle 摘要、SSH 状态和可读 Tailscale 设置摘要；桌面会话内运行才会带出已保存的桌面代理模式）。桌面配置通过断言保持 OpenSSH 关闭，且声明的宿主入站端口不含 TCP/22。

## 远程桌面

Sunshine 随 `graphical-session.target` 启动，把当前会话暴露为 **Desktop**。部署后先在电脑桌面本机打开 `https://localhost:47990` 设好凭据并批准 Moonlight 配对 PIN，手机和电脑登录同一获准 tailnet，然后在 Moonlight 里手动填电脑的 Tailscale IP（不依赖自动发现，默认 `trustedLanCidrs` 为空所以不开放物理 LAN IP 直连）。

管理界面只接本机客户端、关闭 UPnP，串流走 [options.nix](../../system/config/network/options.nix) 里明写的 tailnet TCP/UDP 端口列表（清空某协议列表就关掉该协议全部入站，删掉端口会切断对端已有连接而不影响其他服务和主动出站），手机到电脑的 grants 另在 Tailscale 后台配。**A** 和健康 **B** 允许远控，**C** 会立刻切断会话（包括既有 UDP 流和已解密 tailnet 入站），需要直连就在本机跑 `clash-off`。

[sunshine.nix](../../system/programs/sunshine.nix) 选了 CUDA 构建变体，`https://cache.nixos-cuda.org` 提供与 lock 匹配的缓存（可用 `nix build --max-jobs 0 --no-link .#nixosConfigurations.lfour.config.services.sunshine.package` 验证缓存命中而不本地编译）。凭据和配对状态留在用户的 Sunshine 目录，Desktop 入口和服务设置由 Nix 声明，部署后在图形会话里检查硬件捕获/编码和手机串流：

```sh
systemctl --user status sunshine.service
journalctl --user -b -u sunshine.service
sudo tailscale netcheck
sudo tailscale ping <手机的-tailnet-名称或-IP>
```

可选受限 **B**（保留协商后的 Sunshine 媒体）、Unbound DoT 开关和时间目标限制见[直连例外](egress_CN.md)（**C** 始终切断远控）。如果 Tailscale 打标失败，**B** 会阻断未打标的 root UDP——核对服务权限和 socket mark，或本机跑 `clash-off` 切直连（升级检查见[维护手册](maintenance_CN.md#验证)，专用访客账户保留独立边界）。

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
| `/run/netsec/alerts/*.json` | 各 owner 告警（`/run/netsec/failed` 汇总 owner） |
| `/run/proxy-mode/intent`、`core-seen` | 单次开机意图和崩溃检测标记 |
| `/run/proxy-mode/authorized-config`、`config-digest`、`authorize-window` | root 私有快照授权与一次性导入凭据 |
| `/run/proxy-mode/profile-refresh.json` | 最近刷新结果、digest、固定原因、审计计数、上次自动批准和 20 条历史，权限 `0644` |
| `/run/proxy-mode/profile-refresh-pin.json` | 被拒候选 digest、健康失败标记与一小时 TTL，权限 `0644` |
| `/run/proxy-mode/source-fetch.json` | 绑定本次启动的下载结果、固定失败分类与可用的 HTTP 状态码，权限 `0644` |
| `/run/proxy-mode/profile-refresh-verifying.json` | root 私有待验证事务及上一快照路径 |
| `/run/proxy-mode/profile-refresh-settled.json`、`profile-refresh-commit.json`、`profile-refresh-restart.json` | root 私有 settled 授权、提交中断恢复和待完成内核恢复 |
| `/run/proxy-mode/effective-config.json`、`boot-id` | 绑定共享 boot identity 的 effective YAML 摘要（`effective-config-sha256` 是兼容镜像） |
| `/run/proxy-mode/health-observations.json` | 本次启动最近 20 次完整探测结果，包含脱敏阶段、分层耗时和快照摘要 |
| `/run/proxy-mode/health-failure.json` | 最近失败探针的固定原因、可用的脱敏 `failure_stage`、分层耗时、boot identity 与快照摘要，恢复后仍保留 |

消费者读取原子 JSON（独立的 `status`、`reason`、`version`、`result`、`verified-version` 是兼容镜像）；仅原因变化更新 `revision`，内核策略提交更新 `policy_version`（`version`），跨新代次的检查结果直接丢弃。

就绪要求模式、DNS、broker、relay 同代次且新鲜、无活动告警并有新鲜验证（`ok` 和 `degraded` 都表示硬性检查通过，`degraded` 另有观察性警告显示为 `Passed · warnings`，失败或未知不算就绪）。Broker 须为 `applied`，**B** 要求 DNS 状态 `proxy`，严格 DNS 下的 **A** 接受 `dot` 或 `dot-priming`；消费者默认有效期 120 秒、验证 420 秒，boot identity 和单调时间戳会挡掉跨开机、未来和陈旧记录。

| 原因 | 定位方向 |
|---|---|
| `request`、`reload` | 转换中等待核心健康与验证 |
| `unauthorized-core`、`core-gone` | 意图、启动器/helper 与核心 cgroup |
| `no-tun`、`tun` | 设备、受管表/规则、实际选路与 fake-IP HTTP |
| `config` | 快照摘要、受管 API 和 TCP/UDP 监听身份 |
| `route` | 日志实际叶子为 `DIRECT`/`REJECT` 或与选择图不符 |
| `obs`、`obs:1/3`、`obs:2/3`、`obs:3/3` | HTTP 完成、精确源端口日志和选择观测 |
| `resolver-error`、`split-error` | DNS 身份或批准 Wi-Fi profile/租约 |
| DNS `proxy-priming`、`degraded-proxy` | **B** 正在确认选定的核心解析器或探测已失败；检查核心 DNS 监听及选定的 DoH 路径 |
| DNS `degraded-dot-only` | 检查 **A** 的已认证 DoT 上游及 DNSSEC 探测结果 |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

部分日志和授权文件需要 `sudo`，贴诊断输出时别把快照或带订阅的配置发出去。

### 拒绝计数器

`sudo proxy-inspect --drops`（普通 `proxy-inspect` 也会在 `drops` 字段带上）只读本地状态和 `inet lf_filter` 里现有的拒绝计数器，不改规则、不清零计数也不发探测：

```sh
sudo proxy-inspect --drops | jq '{available,error,policy,state_consistent,groups:[.groups[] | select((.packets // 0) > 0)]}'
```

每组给出稳定的 `reason`、`role`（路由访客用配置的接口名）、`chain`、包/字节计数以及观测模式、原因和代次（`state_consistent=false` 代表状态不可读或采集中变了，这个视图不证明就绪）。

| 原因 | 计数的拒绝 |
|---|---|
| `mode-c-output`、`mode-c-input` | **C** 的输出或回复/入站 guard |
| `guest-source-policy` | 访客源地址超出声明范围，包括未声明的地址族 |
| `guest-target-policy` | 路由访客的受保护或不支持目标 |
| `dns-client-policy` | 应用访问内部解析器入口 |
| `dns-listener-identity`、`proxy-listener-identity` | 未授权本地 socket UID 的回复 |
| `proxy-inlet-access` | 对受保护本地代理端口的显式访问 |

计数只涵盖列出链中带 counter 的 drop（`uncounted_rules` 报没计数的 drop，`installed=false` 且计数为 `null` 代表链不存在，零计数不证明入口启用或健康；读取/解析失败返回 `available=false`、固定错误类别如 `permission-denied`、空 groups 并退出 `1`）。计数从每条规则最近一次安装或 flush 算起（重载、修复和模式切换可能重置部分计数），历史总量也不能把某次连接或原始/NAT 目标归因到当前模式。输出组数按角色固定，不含逐包日志、目标地址或原始规则。

### 刷新状态

`proxy-status` 把更新信息拆到 `Auto update`（调度）、`Profile refresh`/`Update result`（最近刷新）和 `Profile vs runtime`（GUI 副本——受控来源只作展示，兼容导入比对摘要），`proxy-policy` 带 `profile_refresh`，`proxy-inspect` 则提供最多 20 条脱敏历史。

`Subscription download` 把拉取和下载错误与导入分开显示：`deferred` 表示网络保护顺延了尝试（不算失败、不弹窗），`Content audit` 报自动内容批准（不等同健康接受），`restored` 表示启动复用了已接受快照，`verifying` 表示正等健康结果，`fallback` 表示候选被拒、留住旧快照，`rolled_back` 表示恢复授权并请求换核（核心是否恢复看就绪字段）。最近更新失败和订阅年龄分开显示以保留上次接受时间（受控来源以 root 记录为准，GUI 更新时间不代表 root 成功），摘要不同也只说明内容不同而非谁更新。切换订阅共用同一 root 策略、不会扩大 `subscriptionAuditAllowedHosts`；受保护路由仍无法确定时，`enforce` 保留旧快照方便你先看审计分类再调策略。

用户通知 timer 每五分钟读一次本地文件，把更新和健康失败合并成每轮一条消息（刷新失败按结果和完整摘要去重，下载失败按尝试时间去重，健康失败按状态迁移提醒），状态私有存在 `$XDG_STATE_HOME/clash-verge/refresh-notified.json` 且发送成功后才推进，不下载订阅，也不因成功更新、恢复、审计警告、订阅年龄或 GUI 副本差异弹窗。

### 订阅连通性

显式跑 `proxy-subscription --check --json` 测试当前订阅：它从 GUI 索引读地址，报告 HTTP 状态、传输路径、耗时、字节数、HTTPS 和重定向次数（带 `--etag` 发条件 GET）。命令先走当前策略允许的直连路径，传输失败才试配置的 mixed 端口（拿到 `403` 这类完整 HTTP 响应直接报告，不再经代理重试），不写 profile、不导入配置也不推进更新时间。总超时默认 15 秒（`--timeout` 支持 1–120 秒），上限 8 MiB、三次同源重定向（跨源跳转和 HTTPS 降级均拒绝），输出不含 URL、响应正文和 validator 原文。`proxy-inspect --deep` 另附服务重启次数和固定 journal 查询命令。

## 协调与防火墙恢复

Socket 目录事件、TUN link 监视、`nft` 事件、NetworkManager dispatcher 和 `dhcpcd` hook 都会唤醒监督器，辅以自适应轮询（转换中 2 秒、执行模式最多 5 秒、直连最多 60 秒）和 90 秒模式监督器 watchdog（取消的探针不算失败观测）。

构建期[预期规则](../../system/config/network/proxy/coordinator.nix)在 LKL 用户态内核里用同版 `nft` 生成。Verifier 比对所属表、hook、priority、policy 和完整有序表达式（仅规范化 handle、counter、集合元素顺序和受严控的 DNS 租约；读取/解析错误算失败，正确安装的 **C** 哪怕没有核心也能过）。`nftables-verify` 报出失败和告警后仍退出 `0` 让修复/重建继续，所以要看 JSON 结论别只看 unit 退出码（各组件只清自己的告警，忙碌轮次保留排队验证请求）。

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload 原子重装所属静态策略和安全模式（代理意图先过 **C** 再由健康门开 **B**，主表丢失时 `inet lf_policy_guard` 继续阻断代理意图下的流量——若要热回滚到引入 `inet lf_policy_guard` 之前的代次，先 `clash-off` 切进 **A** 清掉逐包要求）。停掉 `nftables` 会保留已装规则，`nftables-verify-repair.service` 显式修复并复验，`nftables-recover` 重试失败的 `nftables` unit 并拉起首启时因防火墙依赖被取消的网络和时间服务。别清空全局 ruleset。

## 防火墙表迁移

`proxy-control migrate-firewall` 会显式删掉 `inet filter`、`ip proxymode_nat` 和 `ip6 proxymode_nat` 并装上[保留表](network_CN.md#防火墙生命周期)（普通启动、重载和修复不动这三个名字）。动手前先确认它们归本配置所有：

```sh
sudo nft list table inet filter
sudo nft list table ip proxymode_nat
sudo nft list table ip6 proxymode_nat
```

确认后执行迁移并复验：

```sh
sudo proxy-control migrate-firewall
sudo systemctl start nftables-verify.service
```

这条 root 命令持有 operator 和 policy 锁，用一次 `nft` 事务提交三个删表、静态规则和安全模式（事务被拒就保留原规则和目标表，也允许目标表缺失以支持重复执行）。代理意图或核心仍在跑时先装 **C** 再等健康开 **B**，直连意图且无核心时直接装 **A**，整个过程保留意图、授权和运行中的核心，也不碰其他服务的表。

## 核心命名空间恢复

宿主的 `Mihomo` 接口是受管 `veth`，看实际核心 TUN 和 `auto-redirect` 表用 `sudo ip -n lf-clash-core link` 和 `sudo ip netns exec lf-clash-core nft list table inet mihomo`，完整就绪看 `proxy-control status proxy --check`（光有宿主接口不算就绪）。

`clash-on` 停掉旧 helper 后重建私有命名空间、保持来源授权流程并断开已有核心连接，物理上行变化和休眠唤醒也会自动重启已授权核心。万一 LAN/VPN 和内部地址冲突，通过 `my.proxy.coreLink` 换一组空闲私有 `/30` 和 `/64`（子网和 host/core 端点一起改，别删重叠检查）。内部 MAC 是独立的本地单播地址，与物理 MAC 随机化无关，激活前记得留好上一代系统。

要退回使用宿主 TUN 的旧代次：

1. 执行 `sudo proxy-control off` 并等 **A** 就绪。
2. 停掉 `clash-core-network.service`（清理只删它自己的 namespace、`veth` 和路由优先级，拒绝删外来接口或旧 TUN）。
3. 激活选定代次，按正常 `clash-on` 流程启动。

## 配置入口

| 来源 | 设置 |
|---|---|
| [machine.nix](../../system/config/machine.nix) | 用户、接口及匹配键、UID、端口、mark、网段和上游 |
| [options.nix](../../system/config/network/proxy/options.nix) | TUN、表 2022、fake-IP、探针、开机意图、`proxyOnly`、`dnsEncryption`、共享域名过滤、访客、split DNS 和新鲜度 |
| [profile.nix](../../system/config/network/proxy/profile.nix) | root 受管 DNS/TUN/profile 约定 |
| [routing.nix](../../system/config/network/proxy/routing.nix) | `runtimeRouting`、不可变 rules/proxy-only 策略及 `proxyOnlyDnsUpstreams` |
| [options.nix](../../system/config/network/options.nix) | 宿主/访客私网、逐接口访客信任/源地址、独立 socket 访客账户、宿主端口、热点、tailnet 和 Wi-Fi MAC |
| [cvr-merge.nix](../../home/config/clash/cvr-merge.nix) | 匹配的 GUI Merge 及路由增强 |
| [firefox.nix](../../home/programs/firefox/firefox.nix)、[options.nix](../../home/programs/firefox/options.nix) | 原生浏览器、`networkPath`、`nvidiaAcceleration`、隐私、策略、字体和外层沙箱 |

TUN 默认值（`Mihomo`、表 2022、规则优先级 9000、内部 mark 8227/8228、fallback 优先级 32768、fd 0）由 parser、模板和健康检查共用；`tunMode=false` 切到 HTTP-only/`redir-host` 不再要 TUN，fake-IP 网络从 `fakeIpRange` 推导，别单独改消费者。

`runtimeRouting` 模块默认 `false`、本机已开，要求受控来源、本地 DNSSEC、严格加密 DNS、隔离 TUN、内核授权租约和作为 `rules` 开机默认的 `proxyOnly=false`（旧 `proxyOnly` 留给不开运行时切换的配置）。`proxyOnlyDnsUpstreams` 接认证字面地址 HTTPS 解析器且始终走远程组。已接受快照分存在 `approved/rules` 和 `approved/proxy-only` 下，带着探测参数变化时本地重渲染所需的原始 `Rule` 数据和固定节点（旧格式快照恢复不了原始规则时，刷新来源并导入新下载代）。

核心内部服务访问由[目标授权](proxy_CN.md#域名目标)独立管理（`localTargetCidrs` 管本地 `DIRECT`，`remoteTargetGrants` 按叶节点、核心入口、协议和端口授权远程内部地址，`nat64Prefixes` 声明已知翻译前缀；内部后缀只放行本地解析私网答案，不授节点代解析或拨号；Gost CONNECT 在核心里是 `HTTPS`，改完策略需重建并重启核心；**A** 留字面门户，**C** 阻断，原生[直连授权](egress_CN.md)各自独立）。

需要不同访客信任级别时，显式把开发网桥设为可信、只给其他访客授权必需私网服务，宿主 socket 后端则另配空闲 UID 和不重叠 subordinate 范围的独立固定账户：

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

替换示例地址：内建网桥源地址/网关来自 `my.machine`，额外 `guestInterfaces` 须有规范且互不重叠的 `guestNetworks` 源 CIDR，仅需开放宿主服务时才加 `hostTcpPorts`/`hostUdpPorts`（限定于声明网关），IPv6 需显式 ULA 源/网关（访客公网 IPv6 仍拒），热点只有显式改 trust 才可信。先装好完整 `nft` 策略再以 `guestnet` 起 `pasta` 或 rootless Docker（Docker 用户单元会清掉受限账户继承的代理环境变量），改动身份或 UID 映射前停旧后端，撤销运行中后端的私网访问时保持身份声明、删授权并重载防火墙以切断双向跟踪流量。光改 `socketGuestUsers` 不会迁移桌面容器，这些账户走独立真实地址 DNS 路径、没有明文/split DNS 后备（见[访客边界](network_CN.md#访客与容器)）。

Wi-Fi profile 显式设的克隆 MAC 优先于 `my.hardening.wifi.clonedMacAddress`（默认 `stable-ssid` 同 SSID 保同 MAC，`permanent` 用硬件地址，`preserve` 保持当前地址）；需要协同变更时声明 `my.hardening.wifi.identities`，用 `network-identity plan UUID` 检查，关自动连接并断开后再 `apply` 或 `restore`（完整步骤、有线策略和 DHCP 状态准备见[链路身份](identity_CN.md)）。

## VirtualBox

VirtualBox 使用原生驱动和界面，可与 QEMU/KVM、libvirt 共存——激活后重新登录即可取得 `vboxusers` 组权限。虚拟机网卡接到 **仅主机网络（Host-only Adapter）** 选 `vboxnet0` 并用 DHCP（如果 VirtualBox 网络管理器给 `vboxnet0` 开了内置 DHCP，关掉它用系统的）。访客以 `192.168.56.1` 作网关和受管 DNS：公网 IPv4 在 **A** 直连、健康 **B** 经 TPROXY、**C** 阻断，私网和宿主服务按 `guestNetworks.vboxnet0` 受限访客策略开放（接其他虚拟网卡会改变联网路径）。

```sh
systemctl status vboxnet0.service vboxnet-dnsmasq.service
VBoxManage list hostonlyifs
```

## Hermes 容器

[`system/programs/hermes/`](../../system/programs/hermes) 集中放着 agent 配置、技能、邮件 CLI 和容器后端。后端在专用账户 user manager 里跑 `hermes-docker.service`，通过 `hermes-containers.socket`/`.service` 向 Hermes 暴露固定 Unix 字节流，私有 runtime 和数据目录保持独立权限。

| 资源 | 默认位置 |
|---|---|
| Hermes agent 数据与工作区 | `/var/lib/hermes` |
| 受限后端 HOME / Docker 数据 | `/var/lib/hermes-containers` / `/var/lib/hermes-containers/docker` |
| Hermes Docker API | `unix:///run/hermes-containers/docker.sock` |

Gateway、dashboard 和 Hermes 登录 shell 把 `DOCKER_HOST` 指到这个 `0660` socket（仅后端账户和 `hermes-container-clients` 成员可访），桌面留用自己的 Docker 入口，直接以 Hermes 跑的本地 terminal、MCP 和浏览器工具仍用宿主代理策略。后端会清掉继承代理变量并走过滤后的真实地址 DNS：**A** 允许公网 IPv4 直连，**B** 按宿主公网策略过受管核心，**C** 切断应用流量，私网/元数据/fake-IP 和宿主代理入口默认无授权，默认 Docker 网桥没有外部 IPv6 路径（其他受支持原生后端保留 UID 守卫的双栈检查）。

```sh
sudo systemctl status hermes-containers.socket hermes-containers.service
sudo systemctl --user --machine=hermes-containers@.host status hermes-docker.service
sudo -u hermes env DOCKER_HOST=unix:///run/hermes-containers/docker.sock docker info --format '{{.DockerRootDir}}'
```

专用账户 user manager 启动前，retirement unit 会先停 Hermes 旧 user Docker（仍有旧 `dockerd` 就拒绝启动；全局 Docker user unit 排除 Hermes 和专用后端，后者用独立 unit 并继承 `user.slice` 的保留端口 bind 限制）。`/var/lib/hermes` 的 agent 数据和旧账户的 Docker 镜像卷都留在原位、不会自动导入。迁移容器数据按这三步走：

1. 激活前停止写入，按旧账户 UID 映射导出镜像和卷。
2. 通过新 Docker 入口加载镜像并恢复卷。
3. 确认恢复无误后再清理旧数据和 UID 映射。

递归改属主代替不了导出恢复；退回旧后端配置前先停掉专用 daemon 和两个 API unit。

## Hermes time MCP

`time` MCP 保留 stdio 接口并用 NixOS 配置的时区：stdio 桥接清掉环境后连 `/run/hermes-time/mcp.sock`（仅 root 和 `hermes-time-clients` 可访），每条连接在 `untrusted.slice` 下以 `hermes-time` 账户拉起一个 `hermes-time@.service` 实例，只能看到只读 Nix store、临时 `/work` 和仅限 Unix socket 的私有网络命名空间。

Gateway 和 dashboard 在 systemd 启动时清掉继承的代理与 CA 覆盖键并用系统 CA bundle（兼容模式为 gateway 恢复受管 Gost 代理，联网 MCP 和浏览器工具各自用声明选项；`proxy-inspect --posture` 报启动环境，Hermes 进程内加载的 `.env` 可在启动后生效）。

```sh
systemctl status hermes-time.socket
systemctl list-units 'hermes-time@*.service'
```

## 离线恢复

想走直连就跑 `clash-off`。默认 `dnsEncryption="opportunistic"` 下，**A** 中门户/DNS 恢复先看 DNS 状态和历史，确实需要时再开有界续租 override：

```sh
sudo touch /run/dns-upstream/force-plaintext
# 恢复后删除，重启也会清除。
sudo rm /run/dns-upstream/force-plaintext
```

这会放行明文 DNS 并继续扣会话预算，但越不过 **C** 的出站 guard（范围见 [DNS](dns_CN.md)）。在 `my.proxy.dnsEncryption="strict"` 下，override 会被忽略且不给明文后备（`clash-off` 仍切到带加密 DNS 的 **A**）——试试字面 HTTP 门户地址、换个上行，或修好 DoT 配置；若门户非得靠不可用的公网 DNS，就得事先留一个预构建好的 `opportunistic` generation 离线切过去（出差依赖严格模式前先备好，私网 split DNS 和 MagicDNS 仍按各自批准和模式运行）。

遇到系统 generation 故障起不来时按顺序恢复：

1. 从 `systemd-boot` 菜单挑旧条目（菜单保留 8 个 generation）。
2. 菜单里没有就去 `/nix/var/nix/profiles/system-*-link` 找，以 root 设回路径并装回启动项：

   ```sh
   nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-path>
   /nix/var/nix/profiles/system/bin/switch-to-configuration boot
   ```

3. 全都起不来时用 NixOS ISO 解锁挂载根目录和 ESP，再 `nixos-enter --root /mnt` 进去修。

## 配置检查

配置和依赖变更需要跑的检查都在[维护手册](maintenance_CN.md#验证)里。
