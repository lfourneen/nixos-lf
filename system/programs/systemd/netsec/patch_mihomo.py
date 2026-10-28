"""Attach the managed policy at the final parser and controller mutation point."""
from pathlib import Path
import sys


def replace(path, old, new):
    value = path.read_text()
    if value.count(old) != 1:
        raise RuntimeError("upstream managed-policy anchor changed: " + str(path))
    path.write_text(value.replace(old, new, 1))


root = Path(sys.argv[1])
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
(root / "config/lf_managed.go").write_text((source / "managed_profile.go").read_text())
(root / "config/lf_managed_test.go").write_text((source / "managed_profile_test.go").read_text())
(root / "hub/route/lf_managed.go").write_text((source / "managed_route.go").read_text())
(root / "hub/route/lf_managed_test.go").write_text((source / "managed_route_test.go").read_text())
