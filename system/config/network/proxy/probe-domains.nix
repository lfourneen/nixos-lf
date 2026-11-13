# IP-echo and IP-quality endpoints that a split-routing check, leak test or an
# ordinary page can embed. With `mode: rule` a GEOIP,CN rule would send these
# direct, exposing the real client address; the controlled renderer and the GUI
# Merge template both prepend them with the catch-all proxy group.
[
  # IP lookup and geolocation sites.
  "ip.cn" "ip138.com" "cip.cc" "ipip.net" "ipw.cn" "ip125.com"
  "ip.gs" "ipx.al" "ipaddress.com" "ipinfo.io" "ip2location.com"
  "whatismyipaddress.com" "whatismyip.com" "myip.com" "ipchicken.com" "2ip.io"
  "detectradar.com"

  # IP echo and address-information APIs.
  "ip.sb" "ip.3322.net" "ip.me" "ip-api.com" "ipapi.co" "ipapi.is"
  "ipify.org" "icanhazip.com" "ifconfig.me" "ifconfig.co" "ident.me"
  "seeip.org" "checkip.amazonaws.com" "checkip.dyndns.org" "pv.sohu.com"

  # Split-routing and IP-quality checks, including their own echo APIs.
  "ip111.cn" "ip.skk.moe" "ping0.cc" "ippure.com" "iplark.com" "ipcheck.ing"
  "net.coffee" "ip.cx" "ipbot.net" "ipjiance.org" "ipkk.com" "cdnping.org"

  # Browser privacy, DNS-leak and IPv6 checks.
  "whoer.net" "browserleaks.com" "ipleak.net" "dnsleaktest.com" "dnsleak.com"
  "dns-detect.alicdn.com" "test-ipv6.com"

  # DNS lookup and network-connectivity tools.
  "dnschecker.org" "itdog.cn" "17ce.com" "boce.com" "tool.lu" "ping.pe" "chinaz.com"

  # IP reputation lookup endpoints.
  "dnsbl.dronebl.org"

  # Cross-network CDN/trace targets embedded by split-routing test sites.
  # Direct connections expose the client's address in the trace response.
  "cloudflare-cn.com" "qualcomm.cn" "perfops.byte-test.com"
  "visa.cn" "perfops.cloudflareperf.com"

  # NetEase CDN: a HEAD response includes the client's address in cdn-user-ip.
  "necaptcha.nosdn.127.net"
]
