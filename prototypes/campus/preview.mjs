import { build } from 'vite'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'

const root = fileURLToPath(new URL('.', import.meta.url))
const destination = join(root, 'review/mobile')
const digest = bytes => createHash('sha256').update(bytes).digest('hex')

// The online entry shares the artwork and fonts already present in this branch.
const originals = new Map()
for (const directory of ['art', 'fonts']) {
  for (const name of await readdir(join(root, directory))) {
    const bytes = await readFile(join(root, directory, name))
    originals.set(digest(bytes), '../../' + directory + '/' + name)
  }
}

const { output } = await build({
  configFile: join(root, 'vite.config.mjs'),
  build: {
    write: false, assetsInlineLimit: 0, assetsDir: '', modulePreload: false,
    rolldownOptions: { output: { entryFileNames: 'campus.js', assetFileNames: '[name]-[hash][extname]' } },
  },
})
const aliases = new Map()
for (const file of output) {
  if (file.type === 'chunk' || file.fileName.endsWith('.html')) continue
  if (file.fileName.endsWith('.css')) aliases.set(file.fileName, 'campus.css')
  else {
    const path = originals.get(digest(file.source))
    if (!path) throw Error('Missing source asset for ' + file.fileName)
    aliases.set(file.fileName, path)
  }
}

await mkdir(destination, { recursive: true })
for (const file of output) {
  if (!/\.(html|js|css)$/.test(file.fileName)) continue
  let contents = file.type === 'chunk' ? file.code : file.source
  for (const [generated, original] of aliases) {
    // Vite may use './name' in HTML/CSS and just 'name' beside import.meta.url.
    contents = contents.replaceAll('./' + generated, original).replaceAll(generated, original)
  }
  await writeFile(join(destination, aliases.get(file.fileName) || file.fileName), contents.replace(/\r+\n/g, '\n'))
}
const licensePaths = ['fonts/spectral-OFL.txt', 'fonts/oswald-OFL.txt',
  '../../node_modules/react/LICENSE', '../../node_modules/react-dom/LICENSE', '../../node_modules/lucide-react/LICENSE']
const licenses = await Promise.all(licensePaths.map(path => readFile(join(root, path), 'utf8')))
await writeFile(join(destination, 'LICENSES.txt'), licenses.map(text => text.replaceAll(/[^\S\r\n]+$/gm, '').trimEnd()).join('\n\n') + '\n')
