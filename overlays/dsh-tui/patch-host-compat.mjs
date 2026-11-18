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

if (tui.version === '0.20.1' && host.version === '0.2.2-rc.4') {
  const contractPath = join(tuiPath, 'lib/types/dsh-adapter/contract.js')
  const { UPSTREAM_BLESSED_PACKAGES } = await import(pathToFileURL(contractPath))
  const packages = [...new Set([...UPSTREAM_BLESSED_PACKAGES, '@deepseek-ai/dsh'])].sort()
  const hash = createHash('sha256')

  function hashDeclarations(base, relative = '') {
    for (const name of readdirSync(join(base, relative)).sort()) {
      const path = join(relative, name)
      const absolute = join(base, path)
      if (statSync(absolute).isDirectory()) {
        hashDeclarations(base, path)
      } else if (/\.d\.[cm]?ts$/.test(path)) {
        hash.update(path + '\0').update(readFileSync(absolute)).update('\0')
      }
    }
  }

  // Compared with rc.3, rc.4 adds CONTENT_REFUSED to dsh-llm's error codes.
  // Bind this local compatibility entry to the reviewed adapter declarations.
  for (const name of packages) {
    const base = name === '@deepseek-ai/dsh' ? hostPath : join(hostPath, 'node_modules', name)
    hash.update(name + '\0')
    hashDeclarations(join(base, 'lib'))
  }
  assert.equal(hash.digest('hex'), '7d9595ab8d2125309319c97df481aece2f87610120661ee1fc44b3e6953f4edf',
    'DSH adapter declarations changed. Review host compatibility again.')

  function replaceOnce(path, before, after) {
    const text = readFileSync(path, 'utf8')
    assert.equal(text.split(before).length, 2, `DSH TUI compatibility anchor changed in ${path}`)
    writeFileSync(path, text.replace(before, after))
  }
  replaceOnce(contractPath, "    '0.2.2-rc.3',\n];", "    '0.2.2-rc.3',\n    '0.2.2-rc.4',\n];")
  replaceOnce(join(tuiPath, 'lib/types/dsh-adapter/contract.d.ts'), '"0.2.2-rc.3"];', '"0.2.2-rc.3", "0.2.2-rc.4"];')

  const { satisfies } = createRequire(manifestPath)('semver')
  for (const [name, range] of Object.entries(tui.peerDependencies)) {
    if (!name.startsWith('@deepseek-ai/dsh-')) continue
    assert(satisfies('0.2.2-rc.3', range), `Unexpected DSH TUI peer range for ${name}`)
    tui.peerDependencies[name] = `${range} || ${host.version}`
  }
  writeFileSync(manifestPath, JSON.stringify(tui, null, 2) + '\n')
  console.log('Applied reviewed DSH TUI 0.20.1 compatibility with DSH 0.2.2-rc.4')
}
