"""Attach the managed policy at the final parser and controller mutation point."""
from pathlib import Path
import sys


def replace(path, old, new):
    value = path.read_text()
    if value.count(old) != 1:
        raise RuntimeError("upstream managed-policy anchor changed: " + str(path))
    path.write_text(value.replace(old, new, 1))


root = Path(sys.argv[1])
replace(root / "listener/sing_tun/prepare.go", '"context"',
        '"context"\n\t"fmt"\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "listener/sing_tun/prepare.go", 'case N.NetworkICMP: // our fork only send those type to PrepareConnection now',
        'case N.NetworkICMP: // our fork only send those type to PrepareConnection now\n'
        '\t\tif lfguard.SingleEgress() { return nil, fmt.Errorf("managed single egress refuses ICMP echo") }')
# Filter before hosts/fake-IP and before resolver cache/transport selection.
# The second hook also covers proxy-endpoint/bootstrap resolver queries.
replace(root / "dns/middleware.go", '\tvar middlewares []middleware',
        '\tvar middlewares []middleware\n'
        '\tmiddlewares = append(middlewares, func(next handler) handler { return lfDomainFilter(next, mapper.ipv6) })')
replace(root / "dns/middleware.go", '\t\t\tcase D.TypeSVCB, D.TypeHTTPS:\n\t\t\t\treturn handleMsgWithEmptyAnswer(r), nil',
        '\t\t\tcase D.TypeSVCB, D.TypeHTTPS:\n'
        '\t\t\t\tif lfServiceRecords() { return next(ctx, r) }\n'
        '\t\t\t\treturn handleMsgWithEmptyAnswer(r), nil')
replace(root / "dns/resolver.go",
        'func (r *Resolver) ExchangeContext(ctx context.Context, m *D.Msg) (msg *D.Msg, err error) {',
        'func (r *Resolver) ExchangeContext(ctx context.Context, m *D.Msg) (msg *D.Msg, err error) {\n'
        '\tif reply := lfBlockedReply(m); reply != nil { return reply, nil }\n'
        '\tif lfguard.DesktopDNS(ctx) && ctx.Err() != nil { return nil, ctx.Err() }\n'
        '\tif reply, matched, err := lfExchangeMagicDNS(ctx, m, r.ipv6); matched { return reply, err }')
replace(root / "dns/resolver.go", '"context"',
        '"context"\n\t"github.com/metacubex/mihomo/component/lfguard"')
# A desktop operation must not detach from its authorization on cancellation,
# expiry or singleflight retry. Keep ordinary DNS's existing singleflight path.
replace(root / "dns/resolver.go",
        'if continueFetch || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {',
        'if !lfguard.DesktopDNS(ctx) && (continueFetch || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled)) {')
replace(root / "dns/resolver.go", 'if expireTime.Before(now) {',
        'if expireTime.Before(now) {\n\t\t\tif lfguard.DesktopDNS(ctx) { return r.exchangeWithoutCache(ctx, m) }')
replace(root / "dns/resolver.go",
        'ctx, cancel := context.WithTimeout(context.Background(), resolver.DefaultDNSTimeout) // reset timeout in singleflight',
        'parent := context.Background()\n\t\tif lfguard.DesktopDNS(ctx) { parent = ctx }\n'
        '\t\tctx, cancel := context.WithTimeout(parent, resolver.DefaultDNSTimeout)')
replace(root / "dns/resolver.go", '\tch := r.group.DoChan(q.String(), fn)',
        '\tif lfguard.DesktopDNS(ctx) { return fn() }\n\tch := r.group.DoChan(q.String(), fn)')
# Managed geodata must come from the authorized snapshot. Even a missing or
# damaged database must not trigger an unaudited network replacement in -t.
replace(root / "component/geodata/init.go",
        'func downloadToPath(url string, path string) (err error) {',
        'func downloadToPath(url string, path string) (err error) {\n'
        '\tif os.Getenv("LF_MANAGED_PROXY_POLICY") != "" { return fmt.Errorf("managed geodata requires a new clash-on snapshot") }')
replace(root / "constant/metadata.go", "type Metadata struct {",
        'type Metadata struct {\n\t// Set only by the internal rule-routed DNS dialer, never decoded from wire JSON.\n'
        '\tLFRuleDNS bool `json:"-"`\n'
        '\tLFGuarded bool `json:"-"`\n'
        '\tLFCandidates []netip.Addr `json:"-"`\n'
        '\tLFLocalTarget bool `json:"-"`\n'
        '\tLFDelay bool `json:"-"`\n'
        '\tLFEgressDirect bool `json:"-"`\n'
        '\tLFEgressProxy string `json:"-"`')
dns_dialer = root / "tunnel/dns_dialer.go"
value = dns_dialer.read_text()
anchor = '\t\tType:    C.INNER,\n'
if value.count(anchor) != 2:
    raise RuntimeError("upstream DNS metadata anchors changed")
dns_dialer.write_text(value.replace(anchor, anchor + '\t\tLFRuleDNS: proxyName == DnsRespectRules,\n'))
replace(root / "adapter/outbound/direct.go", '"github.com/metacubex/mihomo/component/dialer"',
        '"github.com/metacubex/mihomo/component/dialer"\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "adapter/outbound/direct.go",
        'func (d *Direct) DialContext(ctx context.Context, metadata *C.Metadata) (C.Conn, error) {',
        'func (d *Direct) DialContext(ctx context.Context, metadata *C.Metadata) (C.Conn, error) {\n'
        '\tif err := lfguard.CheckDirect(metadata); err != nil { return nil, err }\n'
        '\tmetadata, err := lfguard.Protect(ctx, metadata, metadata, resolver.LookupIP)\n'
        '\tif err != nil { return nil, err }\n'
        '\treturn lfguard.DialTCP(ctx, metadata, func(attemptCtx context.Context, target *C.Metadata) (C.Conn, error) {\n'
        '\t\tif err := lfguard.CheckEgress(target, true, d.Name()); err != nil { return nil, err }\n'
        '\t\treturn d.lfDialContext(attemptCtx, target)\n\t})\n}\n\n'
        'func (d *Direct) lfDialContext(ctx context.Context, metadata *C.Metadata) (C.Conn, error) {')
replace(root / "adapter/outbound/direct.go",
        'func (d *Direct) ListenPacketContext(ctx context.Context, metadata *C.Metadata) (C.PacketConn, error) {',
        'func (d *Direct) ListenPacketContext(ctx context.Context, metadata *C.Metadata) (C.PacketConn, error) {\n'
        '\tif err := lfguard.CheckDirect(metadata); err != nil { return nil, err }\n'
        '\tvar guardErr error\n'
        '\tmetadata, guardErr = lfguard.Protect(ctx, metadata, metadata, resolver.LookupIP)\n'
        '\tif guardErr != nil { return nil, guardErr }\n'
        '\tif err := lfguard.CheckEgress(metadata, true, d.Name()); err != nil { return nil, err }')
# Check the actual child selected by a group, immediately before its dial.
# Inspecting Unwrap and then dialing the group would race a new selection.
replace(root / "adapter/adapter.go", '"github.com/metacubex/mihomo/component/ca"',
        '"github.com/metacubex/mihomo/component/ca"\n'
        '\t"github.com/metacubex/mihomo/component/lfguard"\n'
        '\t"github.com/metacubex/mihomo/component/resolver"')
for signature in [
        'func (p *Proxy) DialContext(ctx context.Context, metadata *C.Metadata) (C.Conn, error) {',
        'func (p *Proxy) ListenPacketContext(ctx context.Context, metadata *C.Metadata) (C.PacketConn, error) {']:
    replace(root / "adapter/adapter.go", signature, signature + '''
    switch p.Type() {
    case C.Selector, C.Fallback, C.URLTest, C.LoadBalance:
        // The selected child checks its actual egress.
    default:
        var err error
        metadata, err = lfguard.Protect(ctx, metadata, metadata, resolver.LookupIP)
        if err != nil { return nil, err }
        if len(metadata.LFCandidates) == 0 { if err = lfguard.CheckEgress(metadata, p.Type() == C.Direct || p.Type() == C.Compatible, p.Name()); err != nil { return nil, err } }
    }
''')
replace(root / "adapter/adapter.go", '\tconn, err := p.ProxyAdapter.DialContext(ctx, metadata)',
        '\tconn, err := lfguard.DialTCP(ctx, metadata, func(attemptCtx context.Context, target *C.Metadata) (C.Conn, error) {\n'
        '\t\tif err := lfguard.CheckEgress(target, p.Type() == C.Direct || p.Type() == C.Compatible, p.Name()); err != nil { return nil, err }\n'
        '\t\treturn p.ProxyAdapter.DialContext(attemptCtx, target)\n\t})')
# Groups must delegate selection exactly once; only the chosen leaf retries.
replace(root / "adapter/adapter.go", 'func (p *Proxy) DialContext(ctx context.Context, metadata *C.Metadata) (C.Conn, error) {',
        'func (p *Proxy) DialContext(ctx context.Context, metadata *C.Metadata) (C.Conn, error) {\n'
        '\tswitch p.Type() { case C.Selector, C.Fallback, C.URLTest, C.LoadBalance: return p.ProxyAdapter.DialContext(ctx, metadata) }')
# Keep the adapter's configured TLS identity and per-protocol handshakes. Only
# its underlying TCP socket dial selects among the worker-pinned endpoints.
replace(root / "adapter/parser.go", '\tvar (\n\t\tproxy outbound.ProxyAdapter',
        '\taddresses, alternateErr := lfguard.ParseNodeAlternates(mapping)\n'
        '\tif alternateErr != nil { return nil, alternateErr }\n'
        '\tif len(addresses) > 0 {\n'
        '\t\tport, err := strconv.Atoi(fmt.Sprint(mapping["port"]))\n'
        '\t\tif err != nil || port < 1 || port > 65535 { return nil, fmt.Errorf("invalid managed node port") }\n'
        '\t\tbasicOption.LFNodeAddresses, basicOption.LFNodePort = addresses, port\n\t}\n'
        '\tvar (\n\t\tproxy outbound.ProxyAdapter')
replace(root / "adapter/outbound/base.go", '"net"', '"net"\n\t"net/netip"\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "adapter/outbound/base.go", '\tProviderName string   `proxy:"-"`',
        '\tProviderName string   `proxy:"-"`\n\tLFNodeAddresses []netip.Addr `proxy:"-"`\n\tLFNodePort int `proxy:"-"`')
replace(root / "adapter/outbound/base.go", '\treturn cDialer',
        '\treturn lfguard.WithNodeAlternates(cDialer, b.LFNodeAddresses, b.LFNodePort, b.IPVersion)')
replace(root / "adapter/adapter.go",
        'func (p *Proxy) URLTest(ctx context.Context, url string, expectedStatus utils.IntRanges[uint16]) (t uint16, err error) {',
        'func (p *Proxy) URLTest(ctx context.Context, url string, expectedStatus utils.IntRanges[uint16]) (t uint16, err error) {\n'
        '\tctx, done, err := lfguard.BeginDelay(ctx, url)\n'
        '\tif err != nil { return 0, err }\n'
        '\tdefer done()')
replace(root / "adapter/adapter.go", '\tinstance, err := p.DialContext(ctx, &addr)',
        '\taddr.LFDelay = lfguard.ManagedDelay()\n\tinstance, err := p.DialContext(ctx, &addr)')
replace(root / "adapter/adapter.go", '\tvar satisfied bool',
        '\tsessionCheck := lfguard.SessionCheck(ctx, url)\n\tvar satisfied bool')
replace(root / "adapter/adapter.go", '\t\talive := err == nil',
        '\t\tif sessionCheck { return }\n\t\talive := err == nil')
# Recovery probes need the first successful response within their short deadline.
replace(root / "adapter/adapter.go", '\tif unifiedDelay {', '\tif unifiedDelay && !lfguard.HealthDelay(ctx) {')
# Unified delay may issue a second HEAD on the same connection.
for indent in ('\t', '\t\t\t'):
    close = '\n' + indent + '_ = resp.Body.Close()'
    replace(root / "adapter/adapter.go", close, close + '\n' + indent +
            'if addr.LFDelay && !sessionCheck && (resp.StatusCode < 200 || resp.StatusCode >= 300) {\n' + indent +
            '\treturn 0, fmt.Errorf("managed delay test requires a successful response")\n' + indent + '}')
replace(root / "adapter/outboundgroup/groupbase.go", '\t"context"',
        '\t"context"\n\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "adapter/outboundgroup/groupbase.go", '''	var wg sync.WaitGroup
	var lock sync.Mutex
	mp := map[string]uint16{}
	proxies := gb.GetProxies(false)
	for _, proxy := range proxies {
		proxy := proxy
		wg.Add(1)
		go func() {
			delay, err := proxy.URLTest(ctx, url, expectedStatus)
			if err == nil {
				lock.Lock()
				mp[proxy.Name()] = delay
				lock.Unlock()
			}

			wg.Done()
		}()
	}
	wg.Wait()''', '\tmp := lfguard.TestDelayBatch(ctx, gb.GetProxies(false), url, expectedStatus)')
replace(root / "adapter/provider/healthcheck.go", '\t"context"',
        '\t"context"\n\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "adapter/provider/healthcheck.go", '''	if len(url) == 0 || url == hc.url {
		log.Debugln("ignore invalid health check url: %s", url)
		return
	}

	hc.mu.Lock()
	defer hc.mu.Unlock()''', '''	if len(url) == 0 {
		log.Debugln("ignore invalid health check url: %s", url)
		return
	}

	hc.mu.Lock()
	defer hc.mu.Unlock()
	if url == hc.url {
		// Managed providers and groups use the same URL. A disabled provider
		// can inherit its group's schedule without registering a second test.
		if lfguard.ManagedDelay() && hc.interval == 0 {
			hc.interval = time.Duration(interval) * time.Second
		}
		return
	}''')
replace(root / "adapter/provider/healthcheck.go", 'context.WithTimeout(hc.ctx, hc.timeout)',
        'lfguard.BackgroundDelay(hc.ctx, hc.timeout)')
replace(root / "adapter/parser.go", '"fmt"', '"fmt"\n\t"strconv"\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "adapter/parser.go", 'func ParseProxy(mapping map[string]any, options ...ProxyOption) (C.Proxy, error) {',
        'func ParseProxy(mapping map[string]any, options ...ProxyOption) (C.Proxy, error) {\n'
        '\tif err := lfguard.CheckProxy(mapping); err != nil { return nil, err }')
replace(root / "config/config.go", "type RawConfig struct {",
        'type RawConfig struct {\n\tLFPrivateTargets map[string]any `yaml:"lf-private-targets" json:"lf-private-targets"`\n'
        '\tLFDNSTransport map[string]any `yaml:"lf-dns-transport" json:"lf-dns-transport"`')
replace(root / "config/config.go", "type Config struct {",
        "type Config struct {\n\tLFDNSTransport *LFDNSTransport")
replace(root / "config/config.go", "\tconfig.DNS = dnsCfg",
        "\tconfig.DNS = dnsCfg\n\tconfig.LFDNSTransport, err = lfParseDNSTransport(rawCfg, ruleProviders)\n"
        "\tif err != nil { return nil, err }")
replace(root / "hub/executor/executor.go", "\tupdateDNS(cfg.DNS, cfg.General.IPv6)",
        "\tupdateDNS(cfg.DNS, cfg.General.IPv6)\n\tupdateManagedDNSTransport(cfg.LFDNSTransport, cfg.General.IPv6)")
# allow-lan=false keeps the ordinary inbounds on loopback. The TPROXY inbound
# is different: it must accept packets addressed to the original destination,
# so a 127.0.0.1 socket can never receive them. Honor an explicit "*" bind for
# this listener only; mixed/DNS/controller stay loopback and the firewall still
# decides which guests may reach the bridge address.
replace(root / "listener/listener.go",
        '\taddr := genAddr(bindAddress, port, allowLan)\n\n\tif tproxyListener != nil {',
        '\taddr := genTProxyAddr(bindAddress, port, allowLan)\n\n\tif tproxyListener != nil {')
replace(root / "listener/listener.go",
        "func genAddr(host string, port int, allowLan bool) string {",
        "func genTProxyAddr(host string, port int, allowLan bool) string {\n"
        "\tif host == \"*\" {\n"
        "\t\t// An IPv6 wildcard socket is not a reliable TPROXY target for\n"
        "\t\t// IPv4 transit; bind the IPv4 wildcard explicitly.\n"
        "\t\treturn fmt.Sprintf(\"0.0.0.0:%d\", port)\n"
        "\t}\n"
        "\treturn genAddr(host, port, allowLan)\n"
        "}\n\n"
        "func genAddr(host string, port int, allowLan bool) string {")
# Bind a real IPv4 TPROXY socket for the IPv4 inlet; Go's generic "tcp"/
# "udp" networks may otherwise turn 0.0.0.0 into an IPv6 dual-stack socket.
replace(root / "listener/tproxy/tproxy.go",
        "func New(addr string, tunnel C.Tunnel, additions ...inbound.Addition) (*Listener, error) {",
        "func lfNetwork(network, addr string) string {\n"
        "\thost, _, err := net.SplitHostPort(addr)\n"
        "\tif err == nil { if ip := net.ParseIP(host); ip != nil && ip.To4() != nil { return network + \"4\" } }\n"
        "\treturn network\n}\n\n"
        "func New(addr string, tunnel C.Tunnel, additions ...inbound.Addition) (*Listener, error) {")
replace(root / "listener/tproxy/tproxy.go",
        '\tl, err := lc.Listen(context.Background(), "tcp", addr)',
        '\tl, err := lc.Listen(context.Background(), lfNetwork("tcp", addr), addr)')
replace(root / "listener/tproxy/udp.go",
        '\tl, err := net.ListenPacket("udp", addr)',
        '\tl, err := net.ListenPacket(lfNetwork("udp", addr), addr)')
replace(root / "tunnel/tunnel.go", '"github.com/metacubex/mihomo/component/resolver"',
        '"github.com/metacubex/mihomo/component/resolver"\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "tunnel/tunnel.go", '\t\t\trawPc, err := retry(ctx,',
        '\t\t\tdialMetadata, err = lfguard.Protect(ctx, metadata, dialMetadata, resolver.LookupIP)\n'
        '\t\t\tif err != nil { return nil, nil, err }\n'
        '\t\t\trawPc, err := retry(ctx,')
replace(root / "tunnel/tunnel.go", '\tremoteConn, err := retry(ctx,',
        '\tdialMetadata, err = lfguard.Protect(ctx, metadata, dialMetadata, resolver.LookupIP)\n'
        '\tif err != nil { log.Warnln("[Managed target] %s", err); return }\n'
        '\tremoteConn, err := retry(ctx,')
replace(root / "tunnel/connection.go", 'C "github.com/metacubex/mihomo/constant"',
        'C "github.com/metacubex/mihomo/constant"\n'
        '\t"github.com/metacubex/mihomo/component/lfguard"\n'
        '\t"github.com/metacubex/mihomo/component/resolver"')
# The initial dial already validates and seeds a pinned mapping. Validate only
# an unmapped destination in a reused UDP association, then let AddMapping pin
# it. Re-resolving every packet can disagree with the original dial/mapping.
replace(root / "tunnel/connection.go", 'originToTarget map[string]netip.Addr',
        'originToTarget map[string]*C.Metadata\n\tegressDirect bool\n\tegressProxy string\n\tegressSet bool')
replace(root / "tunnel/connection.go", 'make(map[string]netip.Addr)', 'make(map[string]*C.Metadata)')
replace(root / "tunnel/connection.go", '''if addr := s.originToTarget[originKey]; !addr.IsValid() { // overwrite only if the record is illegal
		s.originToTarget[originKey] = targetAddr
	}''', '''if target := s.originToTarget[originKey]; target == nil || !target.DstIP.IsValid() {
        s.originToTarget[originKey] = metadata.Clone()
    }
    if !s.egressSet {
        s.egressDirect, s.egressProxy, s.egressSet = metadata.LFEgressDirect, metadata.LFEgressProxy, true
    }''')
replace(root / "tunnel/connection.go", '''if targetAddr.IsValid() {
		addr = net.UDPAddrFromAddrPort(netip.AddrPortFrom(targetAddr, metadata.DstPort))
	}''', '''if targetAddr != nil && targetAddr.DstIP.IsValid() {
        checked := targetAddr.Clone()
        checked.DstPort, checked.Type, checked.NetWork = metadata.DstPort, metadata.Type, metadata.NetWork
        if err := lfguard.CheckEgress(checked, s.egressDirect, s.egressProxy); err != nil { return }
        addr = net.UDPAddrFromAddrPort(netip.AddrPortFrom(checked.DstIP, checked.DstPort))
    }''')
replace(root / "tunnel/connection.go", '\t\tmetadata = metadata.Pure()\n',
        '\t\tguardOrigin := metadata\n'
        '\t\tmetadata = metadata.Pure()\n'
        '\t\tguardCtx, guardCancel := context.WithTimeout(s.ctx, resolver.DefaultDNSTimeout)\n'
        '\t\tprotected, guardErr := lfguard.Protect(guardCtx, guardOrigin, metadata, resolver.LookupIP)\n'
        '\t\tguardCancel()\n'
        '\t\tif guardErr != nil { log.Warnln("[Managed UDP target] %s", guardErr); return }\n'
        '\t\tmetadata = protected\n'
        '\t\tif err := lfguard.CheckEgress(metadata, s.egressDirect, s.egressProxy); err != nil { return }\n')
# Global-mode logs previously omitted the actual connection leaf. The health
# gate must observe the same group[leaf] chain as rule-mode connections.
replace(root / "tunnel/tunnel.go",
        'log.Infoln("[%s] %s --> %s using GLOBAL", strings.ToUpper(metadata.NetWork.String()), metadata.SourceDetail(), metadata.RemoteAddress())',
        'log.Infoln("[%s] %s --> %s using %s", strings.ToUpper(metadata.NetWork.String()), metadata.SourceDetail(), metadata.RemoteAddress(), remoteConn.Chains().String())')
replace(root / "config/config.go", "type General struct {", 'type General struct {\n\tManagedPolicy string `json:"managed-policy"`')
replace(root / "config/config.go", "func ParseRawConfig(rawCfg *RawConfig) (*Config, error) {",
        "func ParseRawConfig(rawCfg *RawConfig) (*Config, error) {\n"
        "\tmanagedID, managedErr := lfValidateRaw(rawCfg)\n"
        "\tif managedErr != nil { return nil, managedErr }")
replace(root / "config/config.go", "config.General = general", "general.ManagedPolicy = managedID\n\tconfig.General = general")
replace(root / "hub/route/configs.go", '''func patchConfigs(w http.ResponseWriter, r *http.Request) {
	general := &configSchema{}
	if err := render.DecodeJSON(r.Body, &general); err != nil {
		render.Status(r, http.StatusBadRequest)
		render.JSON(w, r, ErrBadRequest)
		return
	}''', '''func patchConfigs(w http.ResponseWriter, r *http.Request) {
	general, managedOK := lfDecodeManagedPatch(w, r)
	if !managedOK { return }''')
replace(root / "hub/route/configs.go", "general := executor.GetGeneral()",
        "general := executor.GetGeneral()\n\tgeneral.ManagedPolicy = config.ManagedPolicyID()")
# Restrict the controller API to the GUI operations that do not change the
# privileged core's configuration or binaries. Installed only for managed
# builds; controller writes remain gated independently of policy state.
replace(root / "hub/route/server.go", "\tr := chi.NewRouter()\n\tcors.Apply(r)",
        "\tr := chi.NewRouter()\n\tlfPolicyUse(r)\n\tcors.Apply(r)")
replace(root / "hub/route/server.go", '\t\tunixServer = server',
        '\t\tserver.ConnContext = lfControllerPeer\n\t\tunixServer = server')
replace(root / "hub/route/proxies.go", '\n\t"strconv"\n\t"time"\n', '\n')
replace(root / "hub/route/proxies.go", '\n\t"github.com/metacubex/mihomo/common/utils"', '')
replace(root / "hub/route/proxies.go", '\t\tr.Get("/delay", getProxyDelay)',
        '\t\tr.Get("/delay", lfDesktopRequest(getProxyDelay, false))\n\t\tr.Get("/session", lfSessionCheck)\n'
        '\t\tr.Get("/selection", lfSelection)\n\t\tr.Put("/selection", lfSelection)\n'
        '\t\tr.Get("/health-delay", lfHealthDelay)')
replace(root / "hub/route/proxies.go", '\tif err := selector.Set(req.Name); err != nil {',
        '\tunlock := lfLockSelection(proxy)\n\tdefer unlock()\n'
        '\tif err := selector.Set(req.Name); err != nil {')
replace(root / "hub/route/proxies.go", '''	cachefile.Cache().SetSelected(proxy.Name(), req.Name)
	if SwitchProxiesCallback != nil {''', '''	lfChangedSelection(proxy)
	lfSaveSelection(proxy, req.Name)
	render.NoContent(w, r)
}

func lfSaveSelection(proxy C.Proxy, name string) {
	cachefile.Cache().SetSelected(proxy.Name(), name)
	if SwitchProxiesCallback != nil {''')
replace(root / "hub/route/proxies.go", '''		go SwitchProxiesCallback(proxy.Name(), req.Name)
	}
	render.NoContent(w, r)
}''', '''		go SwitchProxiesCallback(proxy.Name(), name)
	}
}''')
# Controller selector trials race data-plane readers and must preserve a GUI
# update even if both selected the very same name.
selector = root / "adapter/outboundgroup/selector.go"
replace(selector, '\t"errors"', '\t"errors"\n\t"sync/atomic"')
replace(selector, '\tselected   string', '\tselected   atomic.Pointer[string]')
replace(selector, '\t\t\ts.selected = name', '\t\t\ts.selected.Store(&name)')
replace(selector, '\ts.selected = name', '\ts.selected.Store(&name)')
replace(selector, '\tproxies := s.GetProxies(touch)\n\tfor _, proxy := range proxies {\n\t\tif proxy.Name() == s.selected {',
        '\tproxies := s.GetProxies(touch)\n\tselected := s.selected.Load()\n'
        '\tfor _, proxy := range proxies {\n\t\tif selected != nil && proxy.Name() == *selected {')
replace(selector, '\treturn &Selector{',
        '\ts := &Selector{')
replace(selector, '\t\tselected:   selectorOption.DefaultSelected,\n', '')
replace(selector, '\t}, nil\n}', '\t}\n\ts.ForceSet(selectorOption.DefaultSelected)\n\treturn s, nil\n}')
replace(root / "hub/route/proxies.go", '''	query := r.URL.Query()
	url := query.Get("url")
	timeout, err := strconv.ParseInt(query.Get("timeout"), 10, 16)
	if err != nil {
		render.Status(r, http.StatusBadRequest)
		render.JSON(w, r, ErrBadRequest)
		return
	}

	expectedStatus, err := utils.NewUnsignedRanges[uint16](query.Get("expected"))
	if err != nil {
		render.Status(r, http.StatusBadRequest)
		render.JSON(w, r, ErrBadRequest)
		return
	}

	proxy := r.Context().Value(CtxKeyProxy).(C.Proxy)

	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond*time.Duration(timeout))
	defer cancel()

	delay, err := proxy.URLTest(ctx, url, expectedStatus)
	if ctx.Err() != nil {''', '''	call := lfStartDelay(w, r, "")
	if call == nil { return }
	defer call.stop()
	proxy := r.Context().Value(CtxKeyProxy).(C.Proxy)
	delay, err := proxy.URLTest(call.ctx, call.target, call.expected)
	if call.ctx.Err() != nil {''')
replace(root / "hub/route/groups.go", '\n\t"context"\n\t"strconv"\n\t"time"\n', '')
replace(root / "hub/route/groups.go", '\n\t"github.com/metacubex/mihomo/common/utils"', '')
replace(root / "hub/route/groups.go", 'r.Get("/delay", getGroupDelay)',
        'r.Get("/delay", lfDesktopRequest(getGroupDelay, false))')
replace(root / "hub/route/groups.go", '''	if selectAble, ok := proxy.Adapter().(outboundgroup.SelectAble); ok && proxy.Type() != C.Selector {
		selectAble.ForceSet("")
		cachefile.Cache().SetSelected(proxy.Name(), "")
	}

	query := r.URL.Query()
	url := query.Get("url")
	timeout, err := strconv.ParseInt(query.Get("timeout"), 10, 32)
	if err != nil {
		render.Status(r, http.StatusBadRequest)
		render.JSON(w, r, ErrBadRequest)
		return
	}

	expectedStatus, err := utils.NewUnsignedRanges[uint16](query.Get("expected"))
	if err != nil {
		render.Status(r, http.StatusBadRequest)
		render.JSON(w, r, ErrBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), time.Millisecond*time.Duration(timeout))
	defer cancel()

	dm, err := group.URLTest(ctx, url, expectedStatus)''', '''	call := lfStartDelay(w, r, proxy.Name())
	if call == nil { return }
	defer call.stop()
	if selectAble, ok := proxy.Adapter().(outboundgroup.SelectAble); ok && proxy.Type() != C.Selector {
		selectAble.ForceSet("")
		cachefile.Cache().SetSelected(proxy.Name(), "")
	}
	dm, err := group.URLTest(call.ctx, call.target, call.expected)''')
replace(root / "hub/route/provider.go", '\t"context"',
        '\t"context"\n\n\t"github.com/metacubex/mihomo/component/lfguard"')
replace(root / "hub/route/provider.go", 'r.Get("/healthcheck", healthCheckProvider)',
        'r.Get("/healthcheck", lfDesktopRequest(healthCheckProvider, false))')
replace(root / "hub/route/provider.go", 'r.Get("/healthcheck", getProxyDelay)',
        'r.Get("/healthcheck", lfDesktopRequest(getProxyDelay, false))')
replace(root / "hub/route/provider.go", '\tprovider.HealthCheck()\n\trender.NoContent(w, r)', '''	if lfguard.ManagedDelay() {
		call := lfProviderDelay(w, r, provider.Name())
		if call == nil { return }
		defer call.stop()
		lfguard.TestDelayBatch(call.ctx, provider.Proxies(), call.target, nil)
		if call.ctx.Err() != nil {
			lfRefuseDelay(w, r, http.StatusGatewayTimeout, "provider delay tests timed out")
			return
		}
	} else {
		provider.HealthCheck()
	}
	render.NoContent(w, r)''')
replace(root / "hub/route/dns.go", 'r.Get("/query", queryDNS)',
        'r.Get("/query", lfDesktopRequest(queryDNS, true))')
replace(root / "hub/route/dns.go", 'context.WithTimeout(context.Background(), resolver.DefaultDNSTimeout)',
        'context.WithTimeout(r.Context(), resolver.DefaultDNSTimeout)')
# Weak decoding renders a float as a string with 'E' notation, so a JSON
# round-tripped port such as 7898 becomes "7.898E+03" and the listener range
# parser rejects the whole profile. Format like upstream mapstructure instead.
replace(root / "common/structure/structure.go",
        "val.SetString(strconv.FormatFloat(dataVal.Float(), 'E', -1, dataVal.Type().Bits()))",
        "val.SetString(strconv.FormatFloat(dataVal.Float(), 'f', -1, dataVal.Type().Bits()))")
# Decimal float ports now reach the unsigned range parser. Reject values that
# do not fit its target type instead of wrapping (for example 65536 to port 0).
replace(root / "common/utils/range.go",
        "func parseUnsigned[T constraints.Unsigned](s string) (T, error) {\n"
        "\tif val, err := strconv.ParseUint(s, 10, 64); err == nil {\n"
        "\t\treturn T(val), nil",
        "func parseUnsigned[T constraints.Unsigned](s string) (T, error) {\n"
        "\tif val, err := strconv.ParseUint(s, 10, 64); err == nil {\n"
        "\t\tif uint64(T(val)) != val { return 0, strconv.ErrRange }\n"
        "\t\treturn T(val), nil")
source = Path(__file__).parent.parent
(root / "dns/lf_filter.go").write_text((source / "guard/dns_filter.go").read_text())
(root / "dns/lf_filter_test.go").write_text((source / "guard/dns_filter_test.go").read_text())
(root / "dns/lf_transport.go").write_text((source / "guard/dns_transport.go").read_text())
(root / "dns/lf_transport_test.go").write_text((source / "guard/dns_transport_test.go").read_text())
(root / "config/lf_dns_transport.go").write_text((source / "config/dns_transport.go").read_text())
(root / "hub/executor/lf_dns_transport.go").write_text((source / "controller/dns_transport.go").read_text())
(root / "component/lfguard").mkdir()
(root / "component/lfguard/lf_tcp_candidates.go").write_text((source / "guard/tcp_candidates.go").read_text())
(root / "component/lfguard/lf_tcp_candidates_test.go").write_text((source / "guard/tcp_candidates_test.go").read_text())
(root / "component/lfguard/guard.go").write_text((source / "guard/target_guard.go").read_text())
(root / "component/lfguard/delay.go").write_text((source / "guard/delay.go").read_text())
(root / "component/lfguard/validated_answers.go").write_text((source / "guard/validated_answers.go").read_text())
(root / "component/lfguard/validated_answers_test.go").write_text((source / "guard/validated_answers_test.go").read_text())
(root / "component/lfguard/guard_test.go").write_text((source / "guard/target_guard_test.go").read_text())
(root / "adapter/lf_managed_target_test.go").write_text((source / "tests/managed_target_test.go").read_text())
(root / "adapter/inbound/lf_socks_addr_test.go").write_text((source / "tests/socks_addr_test.go").read_text())
(root / "listener/sing_tun/lf_icmp_test.go").write_text((source / "tests/icmp_test.go").read_text())
(root / "adapter/outbound/lf_managed_direct_test.go").write_text((source / "tests/managed_direct_test.go").read_text())
(root / "tunnel/lf_managed_udp_test.go").write_text((source / "tests/managed_udp_test.go").read_text())
(root / "tunnel/lf_managed_dns_test.go").write_text((source / "tests/managed_dns_test.go").read_text())
(root / "config/lf_managed.go").write_text((source / "config/managed_profile.go").read_text())
(root / "config/lf_delay.go").write_text((source / "config/delay.go").read_text())
(root / "config/lf_managed_test.go").write_text((source / "config/managed_profile_test.go").read_text())
(root / "hub/route/lf_managed.go").write_text((source / "controller/managed_route.go").read_text())
(root / "hub/route/lf_managed_test.go").write_text((source / "controller/managed_route_test.go").read_text())
(root / "hub/route/lf_policy.go").write_text((source / "controller/policy_middleware.go").read_text())
(root / "hub/route/lf_delay.go").write_text((source / "controller/delay.go").read_text())
(root / "hub/route/lf_selection.go").write_text((source / "controller/selection.go").read_text())
(root / "hub/route/lf_desktop_lease.go").write_text((source / "controller/desktop_lease.go").read_text())
(root / "hub/route/lf_desktop_lease_test.go").write_text((source / "controller/desktop_lease_test.go").read_text())
(root / "hub/route/lf_selection_test.go").write_text((source / "controller/selection_test.go").read_text())
(root / "hub/route/lf_policy_test.go").write_text((source / "controller/policy_middleware_test.go").read_text())
(root / "config/lf_managed_rules_test.go").write_text((source / "config/managed_rules_test.go").read_text())

(root / "adapter/provider/lf_managed_provider_test.go").write_text((source / "tests/managed_provider_test.go").read_text())
