// Exercise the generated script's probe-domain routing against a subscription.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const transportCases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const remote = {name: "Remote", type: "ss", cipher: "aes-128-gcm", password: "fixture"};

const probeDomains = [
  "ip.cn", "browserleaks.com", "ipinfo.io", "necaptcha.nosdn.127.net",
  "visa.cn", "net.coffee", "ip.cx", "ipbot.net", "ipjiance.org", "ipkk.com",
  "cdnping.org", "perfops.cloudflareperf.com", "dnsbl.dronebl.org",
];

for (const test of JSON.parse(fs.readFileSync(process.argv[2], "utf8"))) {
  const context = vm.createContext({});
  new vm.Script(fs.readFileSync(test.script, "utf8")).runInContext(context);
  const policy = "selected/[group]";
  const config = { proxies: [remote],
    "proxy-groups": [{name: policy, type: "select", proxies: ["Remote"]}], rules: [
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
  if (test.expected.proxyOnly || test.expected.remoteOnlyCatchAll) assert.throws(() => context.main(direct));
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

  // The merge filter drops unchecked TLS leaves and their group references.
  const tlsProxies = [
    { name: "keep", type: "vless", tls: true, "skip-cert-verify": false },
    { name: "drop", type: "hysteria2", "skip-cert-verify": true },
    { name: "nested", type: "ss", plugin: { "\u017fkip_cert_verify": "true" } },
  ];
  const tlsMembers = ["keep", "drop", "nested"];
  const tls = context.main({
    proxies: tlsProxies,
    "proxy-groups": [{ name: "select", type: "select", proxies: tlsMembers }],
    rules: ["MATCH,select"],
  });
  const tlsExpected = ["keep"];
  assert.deepEqual(Array.from(tls.proxies).map(proxy => proxy.name), tlsExpected);
  assert.deepEqual(Array.from(tls["proxy-groups"][0].proxies), tlsExpected);
  assert.throws(() => context.main({
    proxies: [{ name: "only", type: "hysteria2", "skip-cert-verify": true }],
    "proxy-groups": [{ name: "only", type: "select", proxies: ["only"] }],
    rules: ["MATCH,only"],
  }), /removed by the managed transport policy/);
  for (const value of [false, 0, "false", "False", "FALSE", "0", "f", "F"]) {
    const result = context.main({proxies:[{...remote, nested:{SKIP_CERT_VERIFY:value}}]});
    const expected = test.expected.targets["require-protected-node-transport"] && value !== false ? 0 : 1;
    assert.equal(result.proxies.length, expected, `${test.name}: certificate Boolean handling`);
  }
  const provider = {"proxy-providers":{source:{type:"http", url:"https://fixture.example/proxies"}},
    proxies:[{name:"drop", "skip-cert-verify":true}],
    "proxy-groups":[{name:"provider-group", proxies:["drop"], use:["source"]}],
    rules:["MATCH,provider-group"]};
  const pruned = context.main(provider);
  assert.equal(pruned.proxies.length,0);
  assert.equal(pruned["proxy-groups"][0].proxies.length,0);
  assert.equal(pruned["proxy-providers"].source.url,"https://fixture.example/proxies");
  assert.doesNotThrow(() => context.main({proxies:[remote],rules:["MATCH,Remote"]}));
  if (test.expected.targets["require-protected-node-transport"]) {
    const names = transportCases.map(item => item.node.name);
    const accepted = transportCases.filter(item => item.accept).map(item => item.node.name);
    const fixture = context.main({
      proxies: transportCases.map(item => item.node),
      "proxy-groups": [
        {name: "Parent", type: "select", proxies: ["Empty", "Mixed"]},
        {name: "Mixed", type: "url-test", proxies: names},
        {name: "Empty", type: "select", proxies: ["Legacy VMess"]},
      ],
      rules: ["DOMAIN,Legacy VMess,Parent", "MATCH,Parent"],
    });
    assert.deepEqual(Array.from(fixture.proxies, node => node.name), accepted);
    assert.deepEqual(Array.from(fixture["proxy-groups"], group => group.name), ["Parent", "Mixed"]);
    assert.deepEqual(Array.from(fixture["proxy-groups"][0].proxies), ["Mixed"]);
    assert.deepEqual(Array.from(fixture["proxy-groups"][1].proxies), accepted);
    assert.throws(() => context.main({
      proxies: [remote, transportCases[0].node],
      rules: ["DOMAIN-SUFFIX,fixture.invalid,Legacy VMess", "MATCH,Remote"],
    }), /removed by the managed transport policy/);
    if (test.name === "default") {
      const generated = context.main({
        proxies: transportCases.slice(0, 4).map(item => ({
          server: "node.fixture.example", port: 443, ...item.node,
        })),
        "proxy-groups": [{name: "Remote group", type: "select",
          proxies: transportCases.slice(0, 4).map(item => item.node.name)}],
        rules: ["MATCH,Remote group"],
      });
      assert.deepEqual(Array.from(generated.proxies, node => node.name), ["VMess AEAD"]);
      generated["lf-private-targets"]["blocked-domains-file"] = "";
      fs.writeFileSync(process.argv[4], JSON.stringify(generated));
    }
  }
  const nested = {
    proxies: [remote, {name: "Local alias", type: "direct"}],
    "proxy-groups": [
      {name: "CatchAll", type: "select", proxies: ["DIRECT", "Choice"]},
      {name: "Choice", type: "select", proxies: ["Local alias", "REJECT", "Remote"]},
      {name: "Unrelated", type: "select", proxies: ["DIRECT", "Remote"]},
    ],
    rules: ["DOMAIN-SUFFIX,domestic.example,DIRECT", "FINAL,CatchAll"],
  };
  context.main(nested);
  if (test.expected.remoteOnlyCatchAll) {
    assert.deepEqual(Array.from(nested["proxy-groups"][0].proxies), ["Choice"]);
    assert.deepEqual(Array.from(nested["proxy-groups"][1].proxies), ["Remote"]);
    assert.throws(() => context.main({"proxy-groups": [
      {name: "Empty", type: "select", proxies: ["DIRECT"]}], rules: ["MATCH,Empty"]}), /no remote members/);
    assert.throws(() => context.main({"proxy-groups": [
      {name: "Cycle", type: "select", proxies: ["Cycle"]}], rules: ["MATCH,Cycle"]}), /cycle/);
  } else {
    assert(nested["proxy-groups"][0].proxies.includes("DIRECT"));
  }
  assert(nested["proxy-groups"][2].proxies.includes("DIRECT"));
  if (!test.expected.proxyOnly) assert(nested.rules.includes("DOMAIN-SUFFIX,domestic.example,DIRECT"));
  console.log(`${test.name}: rendered routing script passed`);
}
