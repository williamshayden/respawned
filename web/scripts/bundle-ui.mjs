/** Rebuild the checked-in package assets; Node is only needed by maintainers. */
import { createHash } from 'node:crypto'
import { cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const web = fileURLToPath(new URL('../', import.meta.url))
const packaged = path.resolve(web, '../src/respawned/web_assets')
const mode = process.argv[2]

async function files(directory, prefix = '') {
  let entries
  try { entries = await readdir(directory, { withFileTypes: true }) }
  catch (error) { if (error.code === 'ENOENT') return []; throw error }
  const result = []
  for (const entry of entries) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.isDirectory()) result.push(...await files(path.join(directory, entry.name), relative))
    else if (entry.isFile()) result.push(relative)
    else throw new Error(`Unexpected symlink or special file: ${path.join(directory, entry.name)}`)
  }
  return result.sort()
}

async function notices() {
  const packages = ['react', 'react-dom', 'scheduler', 'lucide-react', '@fontsource-variable/inter']
  const sections = ['Respawned browser interface: bundled third-party notices.\n']
  for (const name of packages) {
    const directory = path.join(web, 'node_modules', name)
    const metadata = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8'))
    const license = (await readFile(path.join(directory, 'LICENSE'), 'utf8')).replaceAll('\r\n', '\n').trim()
    sections.push(`${name} ${metadata.version}\n${'='.repeat(72)}\n${license}\n`)
  }
  return sections.join('\n')
}

async function sourceDigest() {
  const inputs = ['index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts']
  for (const directory of ['src', 'scripts']) {
    inputs.push(...(await files(path.join(web, directory))).map(file => `${directory}/${file}`))
  }
  const hash = createHash('sha256')
  for (const file of inputs.sort()) {
    hash.update(file).update('\0').update(await readFile(path.join(web, file))).update('\0')
  }
  return hash.digest('hex')
}

async function main() {
  if (!['--write', '--check'].includes(mode) || process.argv.length !== 3) {
    throw new Error('Use npm run bundle to update assets, or npm run bundle:check to verify them.')
  }
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'respawned-ui-build-'))
  try {
    let build
    try { ({ build } = await import('vite')) }
    catch { throw new Error('Frontend build dependencies are missing. Run npm ci in web/ first.') }
    await build({
      root: web,
      configFile: path.join(web, 'vite.config.ts'),
      mode: 'production',
      build: { outDir: temporary, emptyOutDir: true, sourcemap: false },
    })
    await writeFile(path.join(temporary, 'THIRD_PARTY_NOTICES.txt'), await notices())
    await writeFile(path.join(temporary, 'bundle-manifest.json'), JSON.stringify({
      schema: 1, source_sha256: await sourceDigest(),
    }, null, 2) + '\n')
    if (mode === '--write') {
      // This fixed package directory contains generated files only.
      await rm(packaged, { recursive: true, force: true })
      await mkdir(packaged, { recursive: true })
      await cp(temporary, packaged, { recursive: true })
      console.log('Updated src/respawned/web_assets. Commit these assets with the frontend sources.')
      return
    }
    const expected = await files(temporary)
    const existing = await files(packaged)
    const different = []
    for (const file of [...new Set([...expected, ...existing])].sort()) {
      if (!expected.includes(file) || !existing.includes(file)
        || !(await readFile(path.join(temporary, file))).equals(await readFile(path.join(packaged, file)))) {
        different.push(file)
      }
    }
    if (different.length) {
      throw new Error(`Packaged UI is missing or stale (${different.join(', ')}). Run npm run bundle and commit the updated assets.`)
    }
    console.log(`Packaged UI matches the locked frontend build (${expected.length} files).`)
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1 })
