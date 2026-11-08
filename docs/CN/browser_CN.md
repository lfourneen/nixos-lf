# 受管 Firefox

[English](../browser.md) | 简体中文 | [文档目录](README_CN.md)

## 软件包与启动

Firefox 是原生 Nix 包。[firefox.nix](../../home/programs/firefox/firefox.nix) 将二进制、enterprise policies、AutoConfig 和固定字体绑定在不可变 store closure 中，Home Manager 管理独立的 `~/.config/mozilla/firefox/lfour` profile。更新跟随锁定的 Nixpkgs 和重建 generation，浏览器二进制自更新禁用；旧 Flatpak profile 不自动导入或删除。

公开启动器先运行 [guard.py](../../home/programs/firefox/guard.py)，再打开指定 profile。B 要求新鲜、同代次的模式/DNS/relay/验证状态、被动监听身份检查，并通过桌面 GUI 前向 socket 核对实时 API 不变量。私有配置快照及内部控制口由 root 监督器验证，浏览器使用其发布的就绪结果，不读取私有文件。A 允许在公共 DNS 尚不可用时打开字面 HTTP 门户。C、未知和 `unenforced` 拒绝启动；二次状态读取拒绝预检中途的切换，启动后的变化由 relay 和内核策略控制。

启动器在严格隐私下检查字体，强制 Wayland，去掉禁用沙箱的继承开关，并拒绝复用占有 profile 锁的旧包进程。桌面/MIME 入口均使用它，原 Zen/Chrome ID 转向 Firefox。策略更新后需完整重启浏览器。`firefox --check` 仅检查前提，不创建 profile 或开窗口。

## 网络策略

HTTP/HTTPS 代理锁定为 Gost `127.0.0.1:33332`，bypass/PAC/SOCKS 为空，localhost 也走代理，禁用直连 failover。A 直通，B 进入 Mihomo 路由，C 关闭应用路径。隐私设置不改变 `bootMode` 或 `proxyOnly`。

WebRTC 和 HTTP/3 禁用，使网页传输使用 TCP relay。DoH 禁用并锁定（`network.trr.mode=5`），浏览器自身 DNS 走系统解析器。显式 HTTP 代理可委托 Gost/Mihomo 解析目标，不会自动经过 dnsmasq 黑名单。门户检测与 NetworkManager 连通性检查保留，HTTPS-First 仍允许本地 HTTP。

## 隐私设置

模块中的 `strictPrivacy` 默认 true。AutoConfig `lockPref` 覆盖 enterprise policy 无法锁定的命名空间，对该包打开的所有 profile 生效，抵抗冲突 `user.js`。

| 层 | 行为与边界 |
|---|---|
| 指纹 | RFP 和 letterboxing 减少 Canvas、时区、硬件和窗口尺寸信号；定制桌面仍不同于 Tor Browser |
| 跟踪与状态 | Strict protection、FPP、Total Cookie Protection 减少脚本和跨站状态关联 |
| URL 与分析 | 查询参数清理、bounce 清理、跨源 referrer 限制和 GPC；禁用建议、预连接、遥测、studies、赞助内容、Sync 与内置 IP Protection |
| API 与字体 | 禁用 WebRTC、WebGPU、地理位置、电池和传感器；严格隐私另禁 WebGL，仅暴露固定 DejaVu/Noto/CJK/emoji 字体 |
| ECH / TLS | 启用 ECH 发现、原生 HTTPS-RR 和 GREASE；真实握手仍要求可用 ECHConfig 与兼容的站点/DNS/代理路径 |
| 出口地址 | 共用 A/B/C 与路由策略；默认 B Rule 仍可能 DIRECT |

严格扩展策略阻止未声明扩展，强制安装 uBlock Origin，KISS Translator 可移除。签名包及更新经 relay 从 Mozilla Add-ons 获取，需要联网；包策略和 Gecko 保护不依赖扩展下载完成。

GREASE 不证明 ECH。Relay/core 能看到 HTTP CONNECT 目标；ECH 不隐藏目的 IP、账户或时序。这些设置也不阻止账户关联、出口信誉标签、协议分类或流量分析。

Gecko 内容进程沙箱保留，原生父进程仍有普通桌面用户的文件/socket 权限。同 UID 程序或包内私有 unchecked 二进制可绕过启动预检，不构成外层文件系统/会话总线沙箱。`strictPrivacy=false` 放宽指纹、字体、WebGL 与扩展限制，受管 relay、系统 DNS 和启动前提保留。

软件包升级与检查见[维护手册](maintenance_CN.md)，解析行为见 [DNS](dns_CN.md)。
