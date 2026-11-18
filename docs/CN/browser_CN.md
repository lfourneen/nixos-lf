# 受管 Firefox

[English](../browser.md) | 简体中文 | [文档目录](README_CN.md)

## 软件包与启动

Firefox 是原生 Nix 包。
[firefox.nix](../../home/programs/firefox/firefox.nix) 将二进制、enterprise policies、AutoConfig 和固定字体绑定在不可变 store closure 中，Home Manager 管理独立的 `~/.config/mozilla/firefox/lfour` profile。

更新跟随锁定的 Nixpkgs 和重建 generation，浏览器二进制自更新禁用。
旧 Flatpak profile 不自动导入或删除。

启动器先运行 [guard.py](../../home/programs/firefox/guard.py)，再打开受管 profile。
B 模式要求模式、DNS、relay 和验证状态新鲜且代次一致，随后核对监听身份及实时控制策略。
私有配置快照由 root 服务验证，浏览器读取其发布的结果。

C、未知状态和 `unenforced` 拒绝启动。
预检结束前再次读取状态，以识别期间的模式切换。
浏览器运行后的变化由 relay 和防火墙执行。

启动器在严格隐私下检查字体，然后在下述外层沙箱中运行浏览器。
宿主实例记录结合进程启动时间与包身份拒绝复用旧包。
陈旧 profile 锁中的命名空间 PID 不作为桌面进程身份。
桌面/MIME 入口均使用它，原 Zen/Chrome ID 转向 Firefox。
策略更新后需完整重启浏览器。
`firefox --check` 仅检查前提，不创建 profile 或开窗口。

## 外层沙箱

[sandbox.py](../../home/programs/firefox/sandbox.py) 保留桌面 UID。
严格浏览器另有独立网络命名空间，由 bubblewrap 创建独立的挂载、用户、PID、IPC 和 UTS 命名空间。
浏览器只看到合成主目录及私有 `/tmp`、`/dev`，清空 capabilities，以白名单重建环境，同时保留 Gecko 内容进程沙箱。

复用原生 Firefox 二进制，由 Nix 包装和小型 [scope 辅助程序](../../home/programs/firefox/scope.c) 提供外层策略，不修改 Gecko 源码。

| 资源 | 浏览器权限 |
|---|---|
| 持久文件 | 可读写受管 profile、`~/.cache/firefox-managed` 和 `~/Downloads`。其它个人文件需经文件选择器授权 |
| 运行文件与信任 | 只读 Nix store、选定的解析器/CA 文件及图形驱动。不挂载完整宿主根目录、个人主目录或 runtime 目录 |
| 显示与音频 | 指定 Wayland socket、可用的 Pulse socket 与 DRM render 节点。声明 NVIDIA 加速时另开放对应 primary 节点及渲染接口。不开放 X11、输入设备、摄像头或 NVIDIA modeset 接口 |
| 会话总线 | 过滤代理仅允许文件选择、只读设置、请求/会话关闭、协议支持查询及当前精确 profile 的链接复用 |
| 其它宿主接口 | 不开放系统总线、SSH/GPG agent、容器或核心控制 socket。Landlock 禁止连接宿主抽象 Unix socket 和向作用域外发送信号 |

### 证书与图形设备

系统证书信任使用 `/etc/ssl/certs/ca-certificates.crt` 下的 NixOS CA bundle，包含声明的系统 CA。
启动器解析其符号链接，将实际文件只读挂载到该路径，使 NSS/p11-kit 能读取它，同时不开放 `/etc/static` 或完整宿主证书目录。
CA bundle 缺失时拒绝启动，证书校验保持开启。

`my.firefox.nvidiaAcceleration` 默认跟随声明的 NVIDIA 驱动。
它开放渲染需要的 GPU 节点和只读 EGL 配置，隐藏无关设备及 modesetting 接口。
设为 `false` 可将权限限制为 render 节点。
Gecko 沙箱继续启用，锁定的隐私策略仍禁用 WebGL。

### 系统接口与文件访问

系统调用过滤还禁止 keyring、进程检查、文件句柄打开及部分无需使用的内核接口，保留 Gecko 沙箱需要的命名空间和 seccomp 操作。
要求 Landlock ABI 至少为 6，并允许非特权用户命名空间。
隔离不可用或总线代理失败时拒绝启动或终止浏览器，不回退到无隔离运行。

总线代理通过桌面门户的 Flatpak 身份协议，将浏览器识别为 `org.nixos.ManagedFirefox`，无须安装 Flatpak runtime。
浏览器只能看到自身获得的文档授权。
通过文件选择器选中文件时，只开放该文档，不开放整个父目录。
浏览器的可写目录不能是符号链接。

### 已有窗口与重启

正常启动器通过当前精确 profile 的 D-Bus 名称把新链接交给已有窗口。
代次记录和宿主启动锁不在浏览器挂载范围内。
重建包后需关闭全部旧窗口，再打开新包。
受管 profile 在重启后保留。
`firefox --check` 还检查私有 runtime 目录、桌面 socket 和 Landlock 支持，不打开窗口。

日常透明路径使用桌面 UID、系统 DNS 和 A/B/C 规则。
它保留对其它已授权本机服务的访问。
Wayland、音频、Downloads、profile 和用户选择的文档是明确开放的权限。
已在桌面 UID 下无隔离运行的其它程序仍属于信任基础，可以自行调用包内私有二进制。

## 网络策略与身份

| 入口 | 可用模式 | Profile 与网络 |
|---|---|---|
| `firefox` | 默认允许 A 和健康 B | 持久化日常 profile，本机使用原生 socket |
| `firefox-identity` | 仅健康 B | 独立 profile、缓存和下载目录，无扩展，不复制已有账号数据 |
| `firefox-portal URL` | 仅显式 A | 临时 profile，只能通过 relay 连接一个字面 IP 的 80/443 端口，最多运行 15 分钟 |

`my.firefox.dailyRequireProxy=false` 允许日常 Firefox 在 A 中直连，在 B 中按订阅 Rule 分流，后者可能包含 DIRECT。
设为 `true` 后，日常浏览器也使用身份浏览器的严格入口。
旧选项 `my.firefox.requireProxy` 是日常设置的兼容别名。

严格入口要求 `my.proxy.strictBrowser=true`。
浏览器只有 loopback 网络，通过 `/run/clash-strict/proxy.sock` 访问宿主。
宿主 relay 只在健康 B 中接受流量，核对已连接核心 socket 的 UID，并在失去就绪状态后关闭连接。
它每 100 ms 读取一次已发布状态，故障发现本身还取决于监督器。
A/C 同时通过 nftables 阻断该入口。

核心的固定入口 `lf-strict` 使用 `lf-remote`，拒绝 DIRECT、COMPATIBLE 和私网目标。
Rules 中该组独立选择节点。运行时 proxy-only 将它固定到与普通应用和 DNS 相同的叶子，只有 root 出口事务可以更换。
GUI 选组和 Global 模式不能放宽这些规则。
浏览器不能直接访问宿主 TCP、LAN、本机 DNS 或公网。
已排队或发出的字节无法撤回，同桌面 UID 下的其它未隔离程序仍受信任。

日常和身份 profile 锁定 HTTPS-only，以及反钓鱼、恶意软件和下载保护，禁止绕过证书警告。
Safe Browsing 可能披露查询或下载元信息，详见 [Mozilla 说明](https://support.mozilla.org/en-US/kb/how-does-phishing-and-malware-protection-work)。
需要 HTTP 登录的门户使用独立的临时入口。

独立 profile 将 Cookie 和账号状态分开，但不会让已登录账号匿名。
WebRTC、HTTP/3 和浏览器 DoH 均关闭，代理域名解析遵循共享的 [DNS 与目标策略](dns_CN.md)。

## ECH 发现与代理兼容

ECH 加密 TLS ClientHello 的内部内容，包括站点 SNI。
本配置由浏览器从 HTTPS DNS 记录取得 ECHConfig。
GREASE 添加形似 ECH 的合成扩展。
真实 ECH 握手使用服务器的 ECHConfig 加密 ClientHelloInner。

### 浏览器路径

| `my.firefox.networkPath` | 名称发现与传输 | ECH 行为 |
|---|---|---|
| `"transparent"`，本机默认 | 系统 DNS 与原生 HTTPS-RR 查询。A 使用直连 socket，B 使用宿主 TUN/防火墙路径 | NSS 可使用返回的 ECHConfig，与支持的站点进行真实 ECH 握手 |
| `"http-proxy"` | 严格本地 relay 或兼容 Gost HTTP 代理，目标解析委托核心 | 锁定的 Firefox 160.0 将 HTTPS CONNECT 排除在 HTTPS-RR 发现之外，保留 GREASE |

Firefox 的[连接门](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp)同时检查代理 DNS 策略和 `UsingConnect()`，单改 DNS 传输不会改变这个门。

本包禁用仅供测试的 `force_use_https_rr` override，透明选项复用打包浏览器，不修改 Gecko 源码。

在 `my.proxy.transparentApplications = true` 且 `dailyRequireProxy = false` 时，日常 Firefox 默认使用 `"transparent"`。兼容配置默认使用 `"http-proxy"`。
也可以单独选择浏览器的透明路径：

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

重建 generation 并完整重启 Firefox。
模块拒绝 `tunMode=false` 下的透明路径。
网络路径在构建软件包时确定。
启动器继续检查网络就绪。
C 在内核阻断流量并拒绝新启动，A 保留字面 HTTP 门户。
`bootMode`、`proxyOnly`、浏览器指纹防护和 `dnsEncryption` 均独立设置。

### 发现过程

下图展示正常加密公网 DNS 路径，DNS 恢复和私网解析器例外遵循 [DNS 策略](dns_CN.md)。

```text
Firefox 原生 HTTPS 查询
  -> libc resolver -> systemd-resolved -> dnsmasq 后缀过滤
       A: Unbound -> 认证 DoT + 本地 DNSSEC 验证
       B: 核心后缀过滤 -> localDnssec=true 时使用本地验证器
                         否则使用声明的加密解析器
  <- 携带 ECHConfig 的 HTTPS RR

Firefox/NSS 构造加密 ClientHello
  -> A: 直连 TCP
  -> B: 受管 TUN/核心 -> Rule DIRECT 或选定代理 -> 站点
```

Unix 浏览器通过[原生解析 API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/dns/PlatformDNSUnix.cpp)查询 type 65。
浏览器 DoH 保持关闭并锁定。
现有解析链路已能传递 HTTPS DNS 消息，无须另开本地 DoH 入口。

核心保留地址记录的 fake-IP 行为，同时让 HTTPS 和 SVCB 记录经过正常解析器。
共享过滤先执行，阻断域返回本地 NXDOMAIN，不交付 ECHConfig。
记录参数连同 ECH public name 返回浏览器，核心不使用浏览器的 ECHConfig，也不终止站点 TLS。
A 在本地验证 DNSSEC，B 在 `localDnssec=true` 时也在本地验证。
加密传输、未签名区域、已批准 split DNS 和 MagicDNS 保留[各自边界](dns_CN.md#各路径的责任)。

NSS 选择配置、构造 ClientHelloInner/Outer、验证证书并处理已认证的重试配置。
GREASE 与 `ECHAccepted` 是不同观测：普通 TLS 可以携带 GREASE，而服务端仍报告未接受 ECH。

### 路由与失败行为

受管核心在 Rule 和统一代理模式均关闭嗅探。
ECH 外层 SNI 是掩护名，不能替代实际目标。
显式代理目标和 fake-IP 映射提供域名规则需要的名称，字面 IP 按 IP 规则处理。
Root parser 也会将此策略应用到旧 GUI 配置。

没有可用 HTTPS/ECH 记录时，仍允许正常验证证书的普通 TLS，ECH 按站点提供的配置启用。
`network.dns.echconfig.fallback_to_origin_when_all_failed=false` 禁止该偏好控制的 origin 回退，但不禁止 NSS 独立的“认证后不带 ECH 重试”：受信任服务器拒绝 ECH 且不提供可用重试密钥时，可以触发携带站点 SNI 的普通 TLS 新连接。

允许已认证密钥重试，不可信拒绝或无效证书不授权这种重试。
记录缺失/被剥离、DNS 故障与已认证拒绝的结果因此不同。
`dnsEncryption="strict"` 约束 DNS 传输，不要求所有站点支持 ECH。

显式 HTTP 代理能看到 CONNECT 目标，其它客户端即使能通过代理使用 ECH 也不改变这一点。
透明路径的本机核心可见 DNS/fake-IP 映射与路由目标。
ECH 不隐藏目的 IP、时序、账户或站点可见的内容。
协议接受和重试规则见 [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html)。

### 集成维护

Firefox、NSS 和解析器升级可能改变 ECH 发现与重试行为。
相关浏览器和核心检查见[维护手册](maintenance_CN.md#dns-与浏览器集成)。

## 隐私设置

模块固定启用 `strictPrivacy`。
AutoConfig `lockPref` 覆盖 enterprise policy 无法锁定的命名空间，对该包打开的所有 profile 生效，抵抗冲突 `user.js`。

| 层 | 行为与边界 |
|---|---|
| 指纹 | RFP 和 letterboxing 减少 Canvas、时区、硬件和窗口尺寸信号。定制桌面仍不同于 Tor Browser |
| 跟踪与状态 | Strict protection、FPP、Total Cookie Protection 减少脚本和跨站状态关联 |
| URL 与分析 | 查询参数清理、bounce 清理、跨源 referrer 限制和 GPC。禁用建议、预连接、遥测、studies、赞助内容、Sync 与内置 IP Protection |
| API 与字体 | 禁用 WebRTC、WebGPU、地理位置、电池和传感器。严格隐私另禁 WebGL，仅暴露固定 DejaVu/Noto/CJK/emoji 字体 |
| ECH / TLS | 启用 ECH 和 GREASE。日常透明路径通过原生 HTTPS 记录发现 ECH |
| 出口地址 | 共用 A/B/C 与路由策略。默认 B 按 Rule 选择 DIRECT 或代理节点 |

严格扩展策略阻止未声明扩展，强制安装 uBlock Origin，KISS Translator 可移除。
签名包及更新经选定网络路径从 Mozilla Add-ons 获取，需要联网。
包策略和 Gecko 保护不依赖扩展下载完成。

Gecko 内容进程沙箱与外层策略约束不同层次：外层策略限制原生父进程及其后代，隐私偏好约束浏览器行为，两者都不改变前述路由和解析器的信任边界。
软件包升级与检查见[维护手册](maintenance_CN.md)，解析行为见 [DNS](dns_CN.md)。
