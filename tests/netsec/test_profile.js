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
  for (const url of test.expected.urls) {
    const host = new URL(url).hostname;
    const index = enhanced.rules.indexOf("DOMAIN-SUFFIX," + host + "," + policy);
    assert(index >= 0, `${test.name}: health target ${host} lacks proxy override`);
    assert(index < enhanced.rules.indexOf("DOMAIN-SUFFIX,msftconnecttest.com,DIRECT"));
  }
  const count = enhanced.rules.length;
  assert.equal(context.main(enhanced).rules.length, count, "repeated script run must be idempotent");
  const direct = { rules: ["MATCH,DIRECT"] };
  assert.equal(context.main(direct).rules.length, 1, "DIRECT-only profiles remain observable failures");
  console.log(`${test.name}: rendered routing script passed`);
}
