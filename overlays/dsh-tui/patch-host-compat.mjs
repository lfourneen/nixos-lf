import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const [runtime, hostPath] = process.argv.slice(2)
const tuiPath = join(runtime, 'node_modules/@deepseek-harness-tui/dsh-tui')
const manifestPath = join(tuiPath, 'package.json')
const tui = JSON.parse(readFileSync(manifestPath, 'utf8'))
const host = JSON.parse(readFileSync(join(hostPath, 'package.json'), 'utf8'))
const { satisfies } = createRequire(manifestPath)('semver')
const options = { includePrerelease: true }
if (!satisfies(host.version, tui.peerDependencies['@deepseek-ai/dsh-app-boot'], options)) {
  // Local compatibility review for this exact pair; later RCs still fail.
  assert.equal(tui.version, '0.20.0', 'unreviewed TUI release')
  assert.equal(host.version, '0.2.2-rc.3', 'unreviewed DSH host')
  const contractPath = join(tuiPath, 'lib/types/dsh-adapter/contract.js')
  const { UPSTREAM_BLESSED_PACKAGES } = await import(pathToFileURL(contractPath))

  // These 249 declarations are identical in the published rc.2/rc.3 hosts.
  // Include the CLI and every package the TUI's adapter contract consumes.
  const hash = createHash('sha256')
  function declarations(base, relative = '') {
    for (const name of readdirSync(join(base, relative)).sort()) {
      const path = join(relative, name)
      if (statSync(join(base, path)).isDirectory()) declarations(base, path)
      else if (path.endsWith('.d.ts')) {
        hash.update(path + '\0')
        hash.update(readFileSync(join(base, path)))
        hash.update('\0')
      }
    }
  }
  for (const name of [...UPSTREAM_BLESSED_PACKAGES, '@deepseek-ai/dsh'].sort()) {
    hash.update(name + '\0')
    declarations(join(name === '@deepseek-ai/dsh' ? hostPath : join(hostPath, 'node_modules', name), 'lib'))
  }
  assert.equal(hash.digest('hex'), 'd9ecad33049d9271ebf14cf5fb28525e01e3f9367ed9a23aaff4dfbe1a51ea77',
    'DSH adapter API declarations changed; review compatibility again')

  function replaceOnce(path, before, after) {
    const text = readFileSync(path, 'utf8')
    assert.equal(text.split(before).length, 2, `compatibility anchor changed: ${path}`)
    writeFileSync(path, text.replace(before, after))
  }
  replaceOnce(contractPath, "    '0.2.2-rc.2',\n];", "    '0.2.2-rc.2',\n    '0.2.2-rc.3', // Locally reviewed Nix host compatibility.\n];")
  replaceOnce(join(tuiPath, 'lib/types/dsh-adapter/contract.d.ts'), '"0.2.2-rc.2"];', '"0.2.2-rc.2", "0.2.2-rc.3"];')
  for (const [name, range] of Object.entries(tui.peerDependencies)) {
    if (!name.startsWith('@deepseek-ai/dsh-')) continue
    assert(satisfies('0.2.2-rc.2', range, options), `unexpected peer range: ${name}`)
    tui.peerDependencies[name] = range + ' || 0.2.2-rc.3'
  }
  writeFileSync(manifestPath, JSON.stringify(tui, null, 2) + '\n')
  console.log('Applied reviewed TUI 0.20.0 / DSH 0.2.2-rc.3 compatibility')
}
