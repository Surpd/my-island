import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { build } from 'vite'

const root = fileURLToPath(new URL('.', import.meta.url))
const output = process.argv[2]
if (!output) throw new Error('Укажите путь к HTML-файлу для автономного демо.')

const { output: assets } = await build({
  configFile: resolve(root, 'vite.config.mjs'),
  build: { write:false, assetsInlineLimit:Infinity, cssCodeSplit:false, modulePreload:false },
})
const getAsset = kind => assets.find(a=>a.fileName.endsWith(kind))
const js = getAsset('.js').code.replaceAll('</script', '<\\/script')
const css = getAsset('.css').source
const licenses = await Promise.all([
  'fonts/spectral-OFL.txt', 'fonts/oswald-OFL.txt',
  '../../node_modules/react/LICENSE', '../../node_modules/react-dom/LICENSE',
  '../../node_modules/lucide-react/LICENSE',
].map(path=>readFile(resolve(root,path),'utf8')))
const page = getAsset('.html').source
  .replace(/<script\b[^>]*src="[^"]+"[^>]*><\/script>/, '')
  .replace(/<link\b[^>]*rel="stylesheet"[^>]*>/, () => '<style>'+css+'</style>')
  .replace('</body>', () => '<script type="module">'+js+'</script></body>')
await writeFile(output, page+'\n<!--\n'+licenses.join('\n\n').replaceAll('-->', '-- >')+'\n-->\n')
console.log(output)
