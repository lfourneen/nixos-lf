import { existsSync, lstatSync, mkdirSync, readFileSync, readlinkSync, renameSync, symlinkSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

const runtime = resolve(process.argv[2])
const profile = join(process.env.DSH_HOME, 'profiles', 'dsh-tui')
const manifestPath = join(profile, 'package.json')
const marker = join(profile, '.nix-runtime')
const managed = ['@deepseek-ai/dsh-base', '@deepseek-harness-tui/dsh-tui']
mkdirSync(profile, { recursive: true })

// Refuse to adopt an existing unmanaged profile rather than replace its TUI.
if (existsSync(manifestPath) && !existsSync(marker)) {
  throw new Error(`Existing unmanaged profile: ${profile}. Set DSH_TUI_HOME to a separate directory.`)
}
const manifest = existsSync(manifestPath)
  ? JSON.parse(readFileSync(manifestPath, 'utf8'))
  : {
      name: 'dsh-profile-dsh-tui-nix', private: true, dependencies: {},
      dsh: { profile: { bundles: managed } },
    }
manifest.dependencies ??= {}

// Validate every destination before changing anything. pnpm's link: protocol
// keeps bundled dependencies in the store and third party installs writable.
for (const name of managed) {
  const path = join(profile, 'node_modules', name)
  try {
    if (!lstatSync(path).isSymbolicLink() || !readlinkSync(path).startsWith('/nix/store/')) {
      throw new Error(`Nix-owned bundle was replaced: ${path}`)
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
}
for (const name of managed) {
  const target = join(runtime, 'node_modules', name)
  const path = join(profile, 'node_modules', name)
  manifest.dependencies[name] = `link:${target}`
  mkdirSync(dirname(path), { recursive: true })
  if (existsSync(path) && readlinkSync(path) === target) continue
  const temporary = `${path}.nix-${process.pid}`
  symlinkSync(target, temporary)
  renameSync(temporary, path)
}
const text = `${JSON.stringify(manifest, null, 2)}\n`
if (!existsSync(manifestPath) || readFileSync(manifestPath, 'utf8') !== text) {
  writeFileSync(`${manifestPath}.nix-tmp`, text)
  renameSync(`${manifestPath}.nix-tmp`, manifestPath)
}
for (const [name, contents] of [
  ['cordis.patch.yml', '# User overrides, applied after bundle patches.\n[]\n'],
  ['pnpm-workspace.yaml', 'packages:\n  - .\nnodeLinker: hoisted\nautoInstallPeers: false\n'],
]) {
  if (!existsSync(join(profile, name))) writeFileSync(join(profile, name), contents)
}
writeFileSync(marker, `${runtime}\n`)
