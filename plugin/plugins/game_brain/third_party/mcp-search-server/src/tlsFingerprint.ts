import https from 'https'
import http from 'http'
import tls from 'tls'
import { URL } from 'url'

interface TlsProfile {
  ciphers: string
  minVersion: string
  maxVersion?: string
  honorCipherOrder: boolean
  ecdhCurve: string
}

// Browser TLS cipher profiles — different ordering = different JA3 fingerprint
const TLS_PROFILES: TlsProfile[] = [
  // Chrome 136 (Windows)
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'prime256v1:secp384r1:X25519',
  },
  // Firefox 137 (Windows)
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_256_GCM_SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA384:ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'X25519:prime256v1:secp384r1',
  },
  // Safari 18.5 (macOS)
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'prime256v1:secp384r1:X25519',
  },
  // Edge 134 (Windows)
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-SHA256:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA384:ECDHE-RSA-AES256-SHA',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'prime256v1:secp384r1:X25519',
  },
  // Chrome 135 (macOS) — different curve order
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_256_GCM_SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'X25519:prime256v1',
  },
  // Firefox 136 (Linux)
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_256_GCM_SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'X25519:prime256v1:secp384r1',
  },
  // Incognito Chrome — reduced cipher set
  {
    ciphers: 'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384',
    minVersion: 'TLSv1.2',
    honorCipherOrder: true,
    ecdhCurve: 'prime256v1:secp384r1:X25519',
  },
]

let tlsIndex = 0

function getNextTlsProfile(): TlsProfile {
  tlsIndex = (tlsIndex + 1) % TLS_PROFILES.length
  return TLS_PROFILES[tlsIndex]
}

export interface FetchTlsResult {
  status: number
  statusText: string
  headers: Record<string, string>
  body: string
  url: string
}

function normalizeHeaders(headers: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(headers)) {
    out[k.toLowerCase()] = v
  }
  return out
}

export function fetchWithTLS(
  url: string,
  options: {
    method?: string
    headers?: Record<string, string>
    body?: string
    signal?: AbortSignal
    timeout?: number
    maxRedirects?: number
  } = {}
): Promise<FetchTlsResult> {
  return doFetchWithRedirect(url, options, options.maxRedirects ?? 5)
}

function doFetchWithRedirect(
  url: string,
  options: {
    method?: string
    headers?: Record<string, string>
    body?: string
    signal?: AbortSignal
    timeout?: number
  },
  redirectsLeft: number
): Promise<FetchTlsResult> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url)
    const profile = getNextTlsProfile()
    const method = options.method || 'GET'
    const rawHeaders = options.headers || {}
    const normalized = normalizeHeaders(rawHeaders)

    if (!normalized['accept']) {
      normalized['accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    if (!normalized['accept-language']) {
      normalized['accept-language'] = 'zh-CN,zh;q=0.9'
    }

    const isHttps = parsed.protocol === 'https:'
    const port = parseInt(parsed.port, 10) || (isHttps ? 443 : 80)
    const hostname = parsed.hostname
    const path = parsed.pathname + parsed.search

    const tlsOptions: tls.ConnectionOptions = {
      host: hostname,
      port,
      servername: hostname,
      ciphers: profile.ciphers,
      honorCipherOrder: profile.honorCipherOrder,
      minVersion: profile.minVersion as tls.SecureVersion,
      ecdhCurve: profile.ecdhCurve,
    }

    let clientReq: http.ClientRequest | null = null
    const abortHandler = () => {
      if (clientReq) clientReq.destroy()
      reject(new DOMException('The operation was aborted', 'AbortError'))
    }

    if (options.signal) {
      options.signal.addEventListener('abort', abortHandler, { once: true })
    }

    clientReq = (isHttps ? https : http).request(
      {
        hostname,
        port,
        path,
        method,
        headers: normalized as http.OutgoingHttpHeaders,
        ...(isHttps ? tlsOptions : {}),
      },
      (res) => {
        if (options.signal) {
          options.signal.removeEventListener('abort', abortHandler)
        }

        const status = res.statusCode || 0
        const statusText = res.statusMessage || ''

        // Follow redirects
        if (status >= 300 && status < 400 && redirectsLeft > 0) {
          const location = res.headers.location || res.headers.Location
          if (location) {
            const redirectUrl = new URL(location as string, url).toString()
            res.resume()
            resolve(doFetchWithRedirect(redirectUrl, options, redirectsLeft - 1))
            return
          }
        }

        const responseHeaders: Record<string, string> = {}
        for (let i = 0; i < res.rawHeaders.length; i += 2) {
          const key = res.rawHeaders[i].toLowerCase()
          const value = res.rawHeaders[i + 1]
          if (responseHeaders[key]) {
            responseHeaders[key] += ', ' + value
          } else {
            responseHeaders[key] = value
          }
        }

        const chunks: Buffer[] = []
        res.on('data', (chunk: Buffer) => chunks.push(chunk))
        res.on('end', () => {
          const encoding = responseHeaders['content-encoding']
          const raw = Buffer.concat(chunks)

          let body: string
          if (encoding === 'gzip' || encoding === 'x-gzip') {
            try {
              const zlib = require('zlib')
              body = zlib.gunzipSync(raw).toString('utf-8')
            } catch {
              body = raw.toString('utf-8')
            }
          } else if (encoding === 'deflate') {
            try {
              const zlib = require('zlib')
              body = zlib.inflateSync(raw).toString('utf-8')
            } catch {
              body = raw.toString('utf-8')
            }
          } else if (encoding === 'br') {
            try {
              const zlib = require('zlib')
              body = zlib.brotliDecompressSync(raw).toString('utf-8')
            } catch {
              body = raw.toString('utf-8')
            }
          } else {
            body = raw.toString('utf-8')
          }

          resolve({
            status,
            statusText,
            headers: responseHeaders,
            body,
            url: res.url || url,
          })
        })
      }
    )

    // Timeout handling via signal or socket timeout
    if (options.timeout) {
      clientReq.setTimeout(options.timeout, () => {
        clientReq?.destroy()
        reject(new Error('TLS request timed out'))
      })
    }

    clientReq.on('error', (err) => {
      if (options.signal) {
        options.signal.removeEventListener('abort', abortHandler)
      }
      reject(err)
    })

    if (options.body) {
      clientReq.write(options.body)
    }

    clientReq.end()
  })
}
