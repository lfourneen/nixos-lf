// Exercise the generated script's probe-domain routing against a subscription.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const probeDomains = [
  "ip.cn", "browserleaks.com", "ipinfo.io", "necaptcha.nosdn.127.net",
  "visa.cn", "net.coffee", "ip.cx", "ipbot.net", "ipjiance.org", "ipkk.com",
  "cdnping.org", "perfops.cloudflareperf.com", "dnsbl.dronebl.org",
];

for (const test of JSON.parse(fs.readFileSync(process.argv[2], "utf8"))) {
  const context = vm.createContext({});
  new vm.Script(fs.readFileSync(test.script, "utf8")).runInContext(context);
  const policy = "selected/[group]";
  const config = { rules: [
    ...probeDomains.map(domain => "DOMAIN-SUFFIX," + domain + ",DIRECT"),
    "DOMAIN-SUFFIX,msftconnecttest.com,DIRECT", "MATCH," + policy,
  ] };
  const enhanced = context.main(config);
  assert.equal(JSON.stringify(enhanced["lf-private-targets"]), JSON.stringify(test.expected.targets));
  if (test.expected.proxyOnly) {
    assert.deepEqual(Array.from(enhanced.rules), ["MATCH," + policy]);
    for (const rejected of ["DIRECT", "COMPATIBLE", "REJECT", "REJECT-DROP", "PASS"]) {
      assert.throws(() => context.main({ rules: ["MATCH," + rejected] }));
    }
  } else {
    for (const domain of probeDomains) {
      const index = enhanced.rules.indexOf("DOMAIN-SUFFIX," + domain + "," + policy);
      assert(index >= 0,
             `${test.name}: embedded IP probe ${domain} lacks proxy override`);
      assert(index < enhanced.rules.indexOf("DOMAIN-SUFFIX," + domain + ",DIRECT"),
             `${test.name}: embedded IP probe ${domain} still selects subscription DIRECT rule`);
    }
    for (const url of test.expected.urls) {
    const host = new URL(url).hostname;
    const index = enhanced.rules.indexOf("DOMAIN-SUFFIX," + host + "," + policy);
    assert(index >= 0, `${test.name}: health target ${host} lacks proxy override`);
    assert(index < enhanced.rules.indexOf("DOMAIN-SUFFIX,msftconnecttest.com,DIRECT"));
    }
    assert(enhanced.rules.includes("DOMAIN-SUFFIX,msftconnecttest.com,DIRECT"),
           "split mode must retain subscription DIRECT rules");
  }
  const count = enhanced.rules.length;
  assert.equal(context.main(enhanced).rules.length, count, "repeated script run must be idempotent");
  const direct = { rules: ["MATCH,DIRECT"] };
  if (test.expected.proxyOnly) assert.throws(() => context.main(direct));
  else assert.equal(context.main(direct).rules.length, 1, "DIRECT-only profiles remain observable failures");
  const tests = {
    "proxy-groups": [
      { type: "url-test", url: "http://probe.example/204", interval: 123 },
      { type: "fallback" },
      { type: "load-balance", url: "https://custom.example/204" },
      { type: "select", url: "http://manual.example/204" },
    ],
    "proxy-providers": {
      active: { "health-check": { enable: true, url: "http://probe.example/204", interval: 321 } },
      missing: { "health-check": { enable: true } },
      secure: { "health-check": { enable: true, url: "https://custom.example/204" } },
      disabled: { "health-check": { enable: false, url: "http://disabled.example/204" } },
    },
  };
  context.main(tests);
  assert.equal(tests["proxy-groups"][0].url, test.expected.delayUrl);
  assert.equal(tests["proxy-groups"][0].interval, 123);
  assert.equal(tests["proxy-groups"][1].url, test.expected.delayUrl);
  assert.equal(tests["proxy-groups"][2].url, "https://custom.example/204");
  assert.equal(tests["proxy-groups"][3].url, "http://manual.example/204");
  for (const name of ["active", "missing"]) assert.equal(tests["proxy-providers"][name]["health-check"].url, test.expected.delayUrl);
  assert.equal(tests["proxy-providers"].active["health-check"].interval, 321);
  assert.equal(tests["proxy-providers"].secure["health-check"].url, "https://custom.example/204");
  assert.equal(tests["proxy-providers"].disabled["health-check"].enable, false);
  const encoded = JSON.stringify(tests);
  assert.equal(JSON.stringify(context.main(tests)), encoded, "HTTPS normalization is idempotent");

  // Leaves that disable TLS certificate validation are unusable unless
  // my.proxy.insecureProxyExceptions names them: the merge filter drops the
  // rest (and prunes group references) so the profile still starts, while a
  // named leaf is left for the core's matching exception.
  const exceptions = test.expected.insecureExceptions || [];
  const allowed = exceptions.length ? exceptions[0] : null;
  const tlsProxies = [
    { name: "keep", type: "vless", "skip-cert-verify": false },
    { name: "drop", type: "hysteria2", "skip-cert-verify": true },
    { name: "nested", type: "ss", plugin: { skip_cert_verify: "true" } },
  ];
  const tlsMembers = ["keep", "drop", "nested"];
  if (allowed) {
    tlsProxies.push({ name: allowed, type: "hysteria2", "skip-cert-verify": true });
    tlsMembers.push(allowed);
  }
  const tls = context.main({
    proxies: tlsProxies,
    "proxy-groups": [{ name: "select", type: "select", proxies: tlsMembers }],
    rules: ["MATCH,select"],
  });
  const tlsExpected = allowed ? ["keep", allowed] : ["keep"];
  assert.deepEqual(Array.from(tls.proxies).map(proxy => proxy.name), tlsExpected);
  assert.deepEqual(Array.from(tls["proxy-groups"][0].proxies), tlsExpected);
  assert.throws(() => context.main({
    proxies: [{ name: "only", type: "hysteria2", "skip-cert-verify": true }],
    "proxy-groups": [{ name: "only", type: "select", proxies: ["only"] }],
    rules: ["MATCH,only"],
  }), /disables TLS verification/);
  for (const value of [false, 0, "false", "False", "FALSE", "0", "f", "F"]) {
    const result = context.main({proxies:[{name:"boolean-safe", nested:{SKIP_CERT_VERIFY:value}}]});
    assert.equal(result.proxies.length, 1, `${test.name}: false Boolean form filtered`);
  }
  const provider = {"proxy-providers":{source:{type:"http", url:"https://fixture.example/proxies"}},
    proxies:[{name:"drop", "skip-cert-verify":true}],
    "proxy-groups":[{name:"provider-group", proxies:["drop"], use:["source"]}]};
  const pruned = context.main(provider);
  assert.equal(pruned.proxies.length,0);
  assert.equal(pruned["proxy-groups"][0].proxies.length,0);
  assert.equal(pruned["proxy-providers"].source.url,"https://fixture.example/proxies");
  console.log(`${test.name}: rendered routing script passed`);
}
