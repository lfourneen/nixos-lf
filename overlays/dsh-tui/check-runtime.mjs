import assert from 'node:assert/strict'
import { realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const [runtime, hostPath] = process.argv.slice(2)
const tuiPath = join(runtime, 'node_modules/@deepseek-harness-tui/dsh-tui')
const tuiRequire = createRequire(join(tuiPath, 'package.json'))
const hostRequire = createRequire(join(hostPath, 'package.json'))
const adapter = join(tuiPath, 'lib/types/dsh-adapter')
const contract = await import(pathToFileURL(join(adapter, 'contract.js')))
assert.deepEqual(contract.upstreamDrift(), [], 'installed adapter packages must match the compatibility contract')
assert.equal(contract.upstreamDriftSummary(), undefined)
assert.deepEqual(contract.installedUpstreamLines(), [hostRequire('./package.json').version])

// Exercise the actual exported modules, including app-boot's native Loader.
const { HOST_MODULES } = await import(pathToFileURL(join(adapter, 'host-contract.js')))
const bootRequire = createRequire(hostRequire.resolve('@deepseek-ai/dsh-app-boot/package.json'))
for (const module of HOST_MODULES) {
  const req = module.via === 'app-boot' ? bootRequire : hostRequire
  const exports = await import(pathToFileURL(req.resolve(module.specifier)))
  for (const name of module.exports) assert.notEqual(exports[name], undefined, `${module.specifier}.${name}`)
}
assert.equal(realpathSync(tuiRequire.resolve('@deepseek-ai/cordis')), realpathSync(hostRequire.resolve('@deepseek-ai/cordis')))
await import(pathToFileURL(join(tuiPath, 'lib/types/index.js')))
console.log('DSH host exports, adapter contract, shared Cordis and TUI module load passed')
