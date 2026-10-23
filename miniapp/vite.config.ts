import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
import { fileURLToPath } from 'node:url'
import { spawn, type ChildProcess } from 'node:child_process'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const DIST = fileURLToPath(new URL('./dist', import.meta.url))

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
  '.woff2': 'font/woff2',
}

/** Запрос пришёл не с этой машины и не из локальной сети, а через HTTPS-туннель (адрес *.lhr.life и т. п.) */
function viaTunnel(hostHeader: string | undefined) {
  const host = (hostHeader ?? '').replace(/:\d+$/, '').toLowerCase()
  if (!host || host === 'localhost' || host === 'miniapp' || host.endsWith('.local')) return false
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.startsWith('[')) return false // IP: телефон в той же Wi-Fi
  return true
}

/**
 * Mini App через туннель. Бесплатный туннель медленный (~30 КБ/с, без сжатия), а dev-сервер Vite
 * отдаёт сотни несжатых модулей: страница грузится больше минуты. Поэтому запросам через туннель
 * отдаём собранную версию из dist/ со сжатием gzip (~125 КБ JS), а localhost остаётся с HMR.
 * dist/ пересобирается сам в фоне при каждой правке (vite build --watch).
 * Выключить: KORSIK_TUNNEL_BUILD=0 — тогда через туннель тоже идёт dev-сервер.
 */
function tunnelServesBuild(): Plugin {
  const gzCache = new Map<string, { mtime: number; buf: Buffer }>()
  return {
    name: 'korsik-tunnel-serves-build',
    apply: 'serve',
    configureServer(server) {
      if (process.env.KORSIK_TUNNEL_BUILD === '0') return
      // фоновая сборка в dist/ с пересборкой на правки: отдельный процесс с NODE_ENV=production,
      // как у `npm run build`. При перезапуске dev-сервера старый процесс гасим.
      const g = globalThis as { __korsikBuild?: ChildProcess; __korsikWatcher?: { close: () => Promise<void> } }
      g.__korsikBuild?.kill()
      void g.__korsikWatcher?.close() // сборщик из прошлой версии этого файла
      g.__korsikWatcher = undefined
      const root = server.config.root
      const child = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), 'build', '--watch', '--logLevel', 'warn'], {
        cwd: root,
        env: { ...process.env, NODE_ENV: 'production' },
        stdio: 'inherit',
      })
      g.__korsikBuild = child
      process.once('exit', () => child.kill())

      server.middlewares.use((req, res, next) => {
        if (!viaTunnel(req.headers.host)) return next()
        const url = decodeURIComponent((req.url ?? '/').split('?')[0])
        if (url.startsWith('/api')) return next() // API идёт через прокси Vite
        const index = path.join(DIST, 'index.html')
        if (!fs.existsSync(index)) return next() // сборки ещё нет: отдаём dev-версию
        let file = path.normalize(path.join(DIST, url))
        if (!file.startsWith(DIST)) {
          res.statusCode = 403
          return res.end()
        }
        if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = index // SPA: любой путь → index.html
        const ext = path.extname(file)
        res.setHeader('Content-Type', MIME[ext] ?? 'application/octet-stream')
        res.setHeader('Cache-Control', file === index ? 'no-cache' : 'public, max-age=31536000, immutable')
        res.setHeader('Vary', 'Accept-Encoding')
        const mtime = fs.statSync(file).mtimeMs
        const compressible = ['.html', '.js', '.css', '.svg', '.json'].includes(ext)
        if (compressible && /\bgzip\b/.test(String(req.headers['accept-encoding'] ?? ''))) {
          let hit = gzCache.get(file)
          if (!hit || hit.mtime !== mtime) {
            hit = { mtime, buf: zlib.gzipSync(fs.readFileSync(file), { level: 9 }) }
            gzCache.set(file, hit)
          }
          res.setHeader('Content-Encoding', 'gzip')
          return res.end(hit.buf)
        }
        res.end(fs.readFileSync(file))
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tunnelServesBuild()],
  build: { chunkSizeWarningLimit: 800 },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // для туннеля в «МАКС»
    allowedHosts: true,
    proxy: {
      '/api': {
        target: process.env.API_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
