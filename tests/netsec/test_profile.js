// Exercise the generated script's probe-domain routing against a subscription.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

for (const test of JSON.parse(fs.readFileSync(process.argv[2], "utf8"))) {
  const context = vm.createContext({});
  new vm.Script(fs.readFileSync(test.script, "utf8")).runInContext(context);
  const policy = "selected/[group]";
  const config = { rules: ["DOMAIN-SUFFIX,msftconnecttest.com,DIRECT", "MATCH," + policy] };
  const enhanced = context.main(config);
  assert.equal(JSON.stringify(enhanced["lf-private-targets"]), JSON.stringify(test.expected.targets));
  if (test.expected.proxyOnly) {
    assert.deepEqual(Array.from(enhanced.rules), ["MATCH," + policy]);
    for (const rejected of ["DIRECT", "COMPATIBLE", "REJECT", "REJECT-DROP", "PASS"]) {
      assert.throws(() => context.main({ rules: ["MATCH," + rejected] }));
    }
  } else {
    for (const domain of ["ip.cn", "browserleaks.com", "ipinfo.io", "necaptcha.nosdn.127.net"]) {
      assert(enhanced.rules.includes("DOMAIN-SUFFIX," + domain + "," + policy),
             `${test.name}: embedded IP probe ${domain} lacks proxy override`);
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
  console.log(`${test.name}: rendered routing script passed`);
}
