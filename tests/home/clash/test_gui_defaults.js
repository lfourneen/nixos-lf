// Exercise the packaged startup helper using isolated mutable GUI settings.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { once } = require("node:events");

const [helper, yq, expected] = process.argv.slice(2);
assert.equal(new URL(expected).protocol, "https:");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "gui-defaults-"));
const data = path.join(root, "data");
const settings = path.join(data, "io.github.clash-verge-rev.clash-verge-rev/verge.yaml");
const env = { ...process.env, HOME: root, XDG_DATA_HOME: data };
const run = (success = true, extraEnv = {}) => {
  const result = spawnSync(helper, [expected], { env: { ...env, ...extraEnv }, encoding: "utf8" });
  assert.equal(result.status, success ? 0 : 1, result.stderr);
};
const write = (text) => fs.writeFileSync(settings, text, { mode: 0o640 });
const read = () => fs.readFileSync(settings, "utf8");
const parse = () => {
  const result = spawnSync(yq, ["-o=json", ".", settings], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
};

async function main() {
  try {
    // Let upstream initialize its complete defaults on the first launch.
    run();
    assert.equal(fs.existsSync(settings), false);
    fs.mkdirSync(path.dirname(settings), { recursive: true });
    for (const value of ["", "default_latency_test: null\n", 'default_latency_test: ""\n', 'default_latency_test: "  \t "\n']) {
      write("# keep this comment\nlanguage: zh\ncustom: {nested: true}\n" + value);
      run();
      assert.deepEqual(parse(), { language: "zh", custom: { nested: true }, default_latency_test: expected });
      assert(read().includes("# keep this comment"));
      assert.equal(fs.statSync(settings).mode & 0o777, 0o640);
      const once = read();
      run();
      assert.equal(read(), once, "idempotent");
    }
    for (const url of ["https://custom.example/test", "http://custom.example/test"]) {
      const original = `# custom URL\ndefault_latency_test: " ${url} "\n`;
      write(original);
      run();
      assert.equal(read(), original, "preserve explicit preferences byte for byte");
    }
    for (const original of ["invalid: [\n", "- unexpected sequence\n", "default_latency_test: false\n"]) {
      write(original);
      run(false);
      assert.equal(read(), original, "do not rewrite unexpected settings");
    }
    const linked = path.join(root, "linked.yaml");
    fs.writeFileSync(linked, "default_latency_test: null\n");
    fs.unlinkSync(settings);
    fs.symlinkSync(linked, settings);
    run(false);
    assert(fs.lstatSync(settings).isSymbolicLink());
    assert.equal(fs.readFileSync(linked, "utf8"), "default_latency_test: null\n");
    fs.unlinkSync(settings);

    // Match the wrapped GUI's actual process name and avoid editing live settings.
    write("default_latency_test: null\n");
    for (const argv0 of ["/test/.clash-verge-wrapped", "/test/.clash-verge-launcher", "/test/clash-verge"]) {
    const gui = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { argv0 });
    try {
      await once(gui, "spawn");
      run();
      assert.equal(read(), "default_latency_test: null\n");
    } finally {
      gui.kill();
      await once(gui, "exit");
    }
    }
    run();
    assert.equal(parse().default_latency_test, expected);

    // Profile interval normalization: only existing remote integers 1..1439 rise.
    const profiles = path.join(path.dirname(settings), "profiles.yaml");
    const parseFile = (file) => {
      const result = spawnSync(yq, ["-o=json", ".", file], { encoding: "utf8" });
      assert.equal(result.status, 0, result.stderr);
      return JSON.parse(result.stdout);
    };
    const profileText = [
      "# keep this comment",
      "current: a",
      "items:",
      "- {uid: a, type: remote, updated: 1, option: {update_interval: 1, allow_auto_update: false, merge: m}}",
      "- {uid: b, type: remote, option: {update_interval: 60}}",
      "- {uid: c, type: remote, option: {update_interval: 1439}}",
      "- {uid: d, type: remote, option: {update_interval: 0}}",
      "- {uid: e, type: remote}",
      "- {uid: f, type: remote, option: {update_interval: 1440}}",
      "- {uid: g, type: remote, option: {update_interval: 4320}}",
      "- {uid: h, type: local, option: {update_interval: 5}}",
      "- {uid: i, type: remote, option: {update_interval: \"60\"}}",
      "- {uid: j, type: remote, option: {merge: m}}",
    ].join("\n") + "\n";
    fs.writeFileSync(profiles, profileText, { mode: 0o640 });
    run();
    const items = Object.fromEntries(parseFile(profiles).items.map(item => [item.uid, item]));
    assert.equal(items.a.option.update_interval, 1440);
    assert.equal(items.b.option.update_interval, 1440);
    assert.equal(items.c.option.update_interval, 1440);
    assert.equal(items.d.option.update_interval, 0);
    assert.equal(items.e.option, undefined, "do not create a missing option");
    assert.equal(items.f.option.update_interval, 1440);
    assert.equal(items.g.option.update_interval, 4320);
    assert.equal(items.h.option.update_interval, 5, "local profiles are untouched");
    assert.equal(items.i.option.update_interval, "60", "non-integer intervals are untouched");
    assert.equal(items.j.option.update_interval, undefined, "missing interval is not created");
    assert.equal(items.a.option.allow_auto_update, false);
    assert.equal(items.b.option.allow_auto_update, undefined, "auto-update flags are untouched");
    assert(fs.readFileSync(profiles, "utf8").includes("# keep this comment"));
    assert.equal(fs.statSync(profiles).mode & 0o777, 0o640);
    const profileOnce = fs.readFileSync(profiles, "utf8");
    run();
    assert.equal(fs.readFileSync(profiles, "utf8"), profileOnce, "profile normalization is idempotent");
    for (const original of ["- unexpected sequence\n", "items: nope\n",
                            "items:\n- {type: remote, option: 3}\n"]) {
      fs.writeFileSync(profiles, original);
      run(false);
      assert.equal(fs.readFileSync(profiles, "utf8"), original, "invalid profile index is preserved");
    }
    fs.unlinkSync(profiles);
    run();

    // Also honor the standard HOME fallback when XDG_DATA_HOME is empty.
    const fallback = path.join(root, ".local/share/io.github.clash-verge-rev.clash-verge-rev/verge.yaml");
    fs.mkdirSync(path.dirname(fallback), { recursive: true });
    fs.writeFileSync(fallback, "language: en\n");
    run(true, { XDG_DATA_HOME: "" });
    assert(fs.readFileSync(fallback, "utf8").includes(expected));
    console.log("GUI defaults: unset values, custom URLs, file integrity, live GUI and XDG paths passed");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
