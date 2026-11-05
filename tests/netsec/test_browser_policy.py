"""Validate rendered policy semantics, including the enterprise pref boundary."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

manifest = json.loads(Path(sys.argv[1]).read_text())
files = manifest["generated"]
zen = json.loads(Path(files["zen"]).read_text())["policies"]
chrome = json.loads(Path(files["chrome"]).read_text())
assert "privacy.resistFingerprinting" not in zen["Preferences"], "unsupported enterprise preference"
assert "security.tls.ech.grease_probability" not in zen["Preferences"]
assert all(p["Status"] == "locked" for p in zen["Preferences"].values())
assert zen["DisableTelemetry"] and not zen["DisableEncryptedClientHello"]
proxy = "127.0.0.1:" + str(manifest["relayPort"])
assert manifest["defaults"] == {"bootMode": "direct", "proxyOnly": False}
assert manifest["connectivityEnabled"], "portal discovery must remain enabled"
assert zen["Proxy"]["Locked"] and zen["Proxy"]["HTTPProxy"] == proxy
assert zen["Proxy"]["SSLProxy"] == proxy and zen["Proxy"]["UseHTTPProxyForAllProtocols"]
assert zen["Preferences"]["network.proxy.socks"]["Value"] == ""
assert zen["Preferences"]["network.proxy.socks5_remote_dns"]["Value"] is False
assert zen["Proxy"]["Passthrough"] == ""
assert zen["CaptivePortal"]
assert zen["DNSOverHTTPS"]["Locked"]
assert zen["DNSOverHTTPS"]["Fallback"] == (not manifest["dohOnly"])
assert zen["Preferences"]["network.proxy.failover_direct"]["Value"] is False
assert not zen["Preferences"]["dom.security.https_only_mode"]["Value"]
assert not zen["Preferences"]["dom.security.https_only_mode.upgrade_local"]["Value"]
assert not zen["Preferences"]["dom.security.https_first_for_local_addresses"]["Value"]
assert chrome["ProxySettings"]["ProxyMode"] == "fixed_servers"
assert chrome["ProxySettings"]["ProxyServer"] == "http://" + proxy
assert chrome["ProxySettings"]["ProxyBypassList"] == "<-loopback>"
assert chrome["DnsOverHttpsMode"] == "automatic"
if manifest["strictPrivacy"]:
    assert manifest["fontEtcMode"] == "0644", "Flatpak host-etc needs a real fontconfig file"
    if manifest["zenEnabled"]:
        assert manifest["policyRepairTimer"] == "5min"
    assert zen["ExtensionSettings"]["*"]["installation_mode"] == "blocked"
    assert zen["ExtensionSettings"]["uBlock0@raymondhill.net"]["installation_mode"] == "force_installed"
    prefs = Path(files["preferences"]).read_text()
    assert 'user_pref("privacy.resistFingerprinting", true);' in prefs
    assert 'user_pref("privacy.resistFingerprinting.letterboxing", true);' in prefs
    tree = ET.parse(files["fonts"]).getroot()
    assert tree.find("reset-dirs") is not None
    assert {p.text for p in tree.findall("dir")} == {
        "/usr/share/fonts", "/app/share/fonts", "/app/zen/fonts", manifest["cjkFonts"]}
print("rendered browser privacy policy checks passed")
