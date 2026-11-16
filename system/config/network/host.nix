{
  # This host trusts its configured subscription sources. Reusable modules and
  # VM fixtures retain the strict option default.
  my.proxy.profileRefreshPolicy = "auto";
  my.proxy.controlledSources = true;
  my.proxy.strictBrowser = true;
  my.proxy.subscriptionTrust = "fixed";
  my.proxy.dnsEncryption = "strict";

  # B validates DNSSEC through the core validators and keeps a domestic split:
  # `geosite:cn` uses 360 DoH over a direct connection, everything else the
  # remote-proxied Cloudflare/Google DoH. A validates through Unbound and the
  # same 360 endpoints over DoT.
  my.proxy.localDnssec = true;

  my.proxy.authorizationLeaseSeconds = 60;

  # Cold foreign DNSSEC validation can outlive the module's 12 s probe budget;
  # leave room for the 3 s fake-IP lookups plus the HTTP/TUN checks.
  my.proxy.healthBudgetSeconds = 18;

  # Keep the offline content audit in blocking mode for this host. Reusable
  # modules and VM fixtures retain the warn default.
  my.proxy.subscriptionAudit = "enforce";
  my.proxy.subscriptionAuditAutoApprove = [ "node_identity" "node_authentication" "node_transport" ];
  my.proxy.remoteOnlyCatchAll = true;

  # Subscription hosts overrides are limited to these public time services.
  my.proxy.subscriptionAuditAllowedHosts = {
    "time.android.com" = "17.253.84.125";
    "time.facebook.com" = "17.253.84.125";
  };

  # The TUN self-test is force-proxied through the selected node. A domestic
  # HTTP target is slow and occasionally blocked over an overseas node, so the
  # fast global endpoint runs first and the domestic name stays as a fallback.
  my.proxy.tunProbeNames = [ "cp.cloudflare.com" "www.baidu.com" ];
}
