"""Attach the managed policy at the final parser and controller mutation point."""
from pathlib import Path
import sys


def replace(path, old, new):
    value = path.read_text()
    if value.count(old) != 1:
        raise RuntimeError("upstream managed-policy anchor changed: " + str(path))
    path.write_text(value.replace(old, new, 1))


root = Path(sys.argv[1])
replace(root / "config/config.go", "type RawConfig struct {",
        'type RawConfig struct {\n\tLFPrivateTargets map[string]any `yaml:"lf-private-targets" json:"lf-private-targets"`')
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
replace(root / "tunnel/connection.go", '\tmetadata := packet.Metadata()\n',
        '\tmetadata := packet.Metadata()\n'
        '\tguarded := metadata.Clone()\n'
        '\tif err := preHandleMetadata(guarded); err != nil { return }\n'
        '\tguardCtx, guardCancel := context.WithTimeout(s.ctx, resolver.DefaultDNSTimeout)\n'
        '\tprotected, guardErr := lfguard.Protect(guardCtx, guarded, guarded, resolver.LookupIP)\n'
        '\tguardCancel()\n'
        '\tif guardErr != nil { log.Warnln("[Managed UDP target] %s", guardErr); return }\n'
        '\tif protected != guarded {\n'
        '\t\ts.AddMapping(metadata, protected)\n'
        '\t\t_ = handleUDPToRemote(packet, pc, protected.UDPAddr())\n'
        '\t\treturn\n'
        '\t}\n')
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
source = Path(__file__).parent
(root / "component/lfguard").mkdir()
(root / "component/lfguard/guard.go").write_text((source / "target_guard.go").read_text())
(root / "component/lfguard/guard_test.go").write_text((source / "target_guard_test.go").read_text())
(root / "config/lf_managed.go").write_text((source / "managed_profile.go").read_text())
(root / "config/lf_managed_test.go").write_text((source / "managed_profile_test.go").read_text())
(root / "hub/route/lf_managed.go").write_text((source / "managed_route.go").read_text())
(root / "hub/route/lf_managed_test.go").write_text((source / "managed_route_test.go").read_text())
