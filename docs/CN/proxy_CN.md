# 代理核心与权限

[English](../proxy.md) | 简体中文 | [文档目录](README_CN.md)

## 组件与身份

| 组件 | 默认端点 / 状态 | 身份 |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`、DNS `:1053`、TUN `Mihomo` | `clash-core`，UID 982；`NET_ADMIN` 和 `NET_RAW` |
| Clash Verge helper | 按需启动的 `clash-verge.service` | root，能力限定为启动、文件及网络管理所需集合 |
| GUI 控制口前向器 | `/run/user/<桌面uid>/clash-verge-rev/verge-mihomo.sock` | 桌面 UID、core 组，无能力；socket 0600 |
| Gost | HTTP `127.0.0.1:33332`、透明 `127.0.0.1:33333` / `[::1]:33333` | UID 987，无能力 |
| dnsmasq / Unbound / dhcpcd | 本地 DNS / 有线 DHCP | UID 985 / 983 / 986 |

[identity.nix](../../system/programs/clash/identity.nix) 和 [runtime.nix](../../system/programs/clash/runtime.nix) 定义核心身份与服务约定。root 启动器经 `setpriv` 降权，核心的 permitted、effective、inheritable、ambient、bounding 集均只保留两项网络能力。真实控制口为 `/run/clash-verge-rev/core/mihomo.sock`，监督器直接访问；桌面 UID 的 `socat` 提供 GUI 前向路径。

Helper 保留身份切换、目录所有权和子进程管理所需权限，仅允许打包的 root 所有启动器作为可执行文件。受管日志固定在 `/var/lib/clash-verge-service/logs` 并固定轮转；客户端 IPC 路径不驱动 root chmod/unlink 或启动恢复。Systemd 管理子进程清理。Helper 私有状态与 `/var/lib/clash-core` 的可写缓存分开。

这些约束减少桌面可控制的 root 操作。核心仍有宿主 `NET_ADMIN`，不能视为与宿主网络隔离。同桌面 UID 程序可以调用允许的 GUI/API；helper 上游 IPC 协议不能代替执行路径和文件操作限制。

## 配置授权

`clash-on` 写入代理意图，进入 C 并开始新导入会话：清除旧授权，打开**一次性 300 秒**窗口。GUI 提供生效配置，打包启动器在共享授权锁下导入后再启动核心。失败也消费窗口，修正输入后需重新 `clash-on`。

[clash-verge-stage.py](../../system/programs/clash/clash-verge-stage.py) 只接受桌面用户所有、大小受限的普通输入文件，不跟随 symlink，复制 YAML/geodata 到 root 所有的快照并检测并发变化。它规范控制口位置，以无能力的 `clash-core` 执行核心 `-t` 验证。目录 0750、文件 0640，core 组可读；授权记录、验证/helper 日志仅 root 可读写。

摘要覆盖生效 YAML、geodata 字节及可选文件缺失状态。规范资源包括 `Country.mmdb`、`GeoIP.dat`、`GeoSite.dat`，以及可选 `ASN.mmdb`/`BundleMRS.7z`；支持的旧 country 数据库名字会规范化。`cache.db` 指向独立可写状态，不纳入摘要。

普通重启要求 GUI YAML 匹配且快照摘要完整，复用已授权数据库；GUI 更新数据库不会替换该授权。导入新 YAML/geodata 需要再次 `clash-on`。受管 geodata 下载禁用，缺失或损坏数据库也不能自动替换。快照跨 helper 重启保留，健康检查复核摘要；`clash-off` 清除单次开机授权并请求直连。

导入授权的是该会话选中的输入，不能使已失陷桌面账户变得可信。授权前应确认订阅和配置来源。

## 受管 profile 与控制 API

[proxy-profile.nix](../../system/config/proxy-profile.nix) 定义 root 策略，[cvr-merge.nix](../../home/config/cvr-merge.nix) 生成匹配的 GUI Merge。最终核心 parser 在 GUI/订阅处理后约束监听、mark、DNS、TUN 路由/排除项和私网目标设置。Rule providers 固定为空，geodata 自动更新关闭，无条件 `MATCH,DIRECT`/`FINAL,DIRECT` 被拒绝；其它订阅规则和组选择保留授权语义。

核心向健康消费者报告 `managed-policy` 摘要，描述系统不变量；独立快照摘要绑定已授权 YAML 和资源。

| 控制操作 | 受管行为 |
|---|---|
| GET / HEAD / OPTIONS | 允许，包括 GUI 延迟测试 |
| PUT / DELETE `/proxies/<name>` | 选择或释放代理选择 |
| DELETE `/connections` 或 `/connections/<id>` | 关闭连接 |
| PATCH `/configs` | 仅 `mode=rule`、`mode=global` 或不改变设置 |
| 其它写操作 | 默认拒绝 |

配置加载、禁用规则、provider/geodata/storage 更新、重启及升级 API 均拒绝。PATCH 在应用前检查规范字段名和重复键，验证与应用使用同一份已解码上游 schema。升级新增写端点时需明确决定权限。

## 路由策略

```nix
my.proxy.proxyOnly = false; # 默认保留订阅 Rule 分流。
# my.proxy.proxyOnly = true; # 使用订阅最终代理 MATCH。
```

默认保留国内 DIRECT，并前置已知探针/下载域代理规则。未列出的 IP 回显资源仍可能命中 DIRECT，看到真实出口。健康检查只证明其配置的样本路径。

`true` 将进入核心的应用流量统一交给订阅最终代理 MATCH，要求存在可用代理兜底，覆盖地理/进程分流及 profile REJECT。核心同时拒绝应用 DIRECT/COMPATIBLE TCP/UDP，包括组内 direct 叶子和改名适配器。按规则路由的 DNS 也受约束；显式引导/端点解析和 `#DIRECT` MagicDNS 仍豁免。关闭嗅探，避免 cover SNI 替换实际拨号目标。

部署 Nix generation 并导入/重启受管核心后生效，会增加包括国内流量在内的订阅消耗。`bootMode`、A 的门户访问、root UDP、组播及核心外宿主私网路径独立控制。

## 域名目标

[target_guard.go](../../system/programs/systemd/netsec/target_guard.go) 在路由/嗅探后、TCP/UDP 出站前校验域名目的地，拒绝混合公网/私网答案、空结果，以及重试一次后仍无法解析的目标。限制包括私网、CGNAT、loopback、链路本地、未指定、组播及选定保留/过渡范围，含 mapped IPv4 和知名 NAT64 前缀内嵌私网 IPv4。这是针对性清单，并非整个 IANA 特殊用途地址表。

DIRECT 与远程代理均固定已检查 IP，避免第二次未经检查的 DNS 解析。复用 UDP association 时，每个新目的映射重新验证，后续数据包使用该映射。

字面 IP、配置的私网后缀、已认证 MagicDNS 和核心基础设施是有意的豁免。发给远程代理的字面私网/loopback 地址可能指向节点网络，因此此守卫不提供通用内部目标隔离；`proxyOnly` 单独约束应用直拨。

## Gost 生命周期

[gost-relay.nix](../../system/programs/systemd/gost-relay.nix) 遵循模式原子状态，绑定前回收旧进程，不使用 `SO_REUSEPORT`，每端点要求唯一监听，并限制文件描述符、内存和任务数。B 信任监督器健康结论并复核 mixed 监听身份；以被动 socket 观测代替连接透明入口，自连接检测作为后备。

Relay 兼容带有效端口的裸数字 IPv6 CONNECT，保留 Go HTTP framing 和缓冲中的隧道数据。建议客户端使用标准 `[IPv6]:port`。Gost 通过 store 绝对路径运行，不放进系统 PATH 或 initrd。源码及打包自定义见[维护手册](maintenance_CN.md)。
