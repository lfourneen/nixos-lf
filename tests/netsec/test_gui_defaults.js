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
    const gui = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { argv0: "/test/.clash-verge-wrapped" });
    try {
      await once(gui, "spawn");
      run();
      assert.equal(read(), "default_latency_test: null\n");
    } finally {
      gui.kill();
      await once(gui, "exit");
    }
    run();
    assert.equal(parse().default_latency_test, expected);

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
