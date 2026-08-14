interface HeaderProfile {
  ua: string
  lang: string
  platform: string
  mobile: string
  brand: string
}

const PROFILES: HeaderProfile[] = [
  {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
    lang: 'zh-CN,zh;q=0.9',
    platform: '"Windows"',
    mobile: '?0',
    brand: '"Chromium";v="136", "Google Chrome";v="136", "Not?A_Brand";v="24"',
  },
  {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
    lang: 'zh-CN,zh;q=0.9,en;q=0.8',
    platform: '"Windows"',
    mobile: '?0',
    brand: '"Chromium";v="135", "Google Chrome";v="135", "Not?A_Brand";v="24"',
  },
  {
    ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
    lang: 'en-US,en;q=0.9,zh-CN;q=0.8',
    platform: '"macOS"',
    mobile: '?0',
    brand: '"Chromium";v="136", "Google Chrome";v="136", "Not?A_Brand";v="24"',
  },
  {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0',
    lang: 'zh-CN,zh;q=0.9,en-US;q=0.8',
    platform: '"Windows"',
    mobile: '?0',
    brand: '"Firefox";v="137", "Not?A_Brand";v="24"',
  },
  {
    ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15',
    lang: 'en-US,en;q=0.9',
    platform: '"macOS"',
    mobile: '?0',
    brand: '',
  },
  {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0',
    lang: 'en,zh-CN;q=0.9,zh;q=0.8',
    platform: '"Windows"',
    mobile: '?0',
    brand: '"Chromium";v="134", "Microsoft Edge";v="134", "Not?A_Brand";v="24"',
  },
  {
    ua: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
    lang: 'en-US,en;q=0.9',
    platform: '"Linux"',
    mobile: '?0',
    brand: '"Chromium";v="136", "Google Chrome";v="136", "Not?A_Brand";v="24"',
  },
  {
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    lang: 'zh-CN,zh;q=0.9',
    platform: '"Windows"',
    mobile: '?0',
    brand: '"Chromium";v="128", "Google Chrome";v="128", "Not?A_Brand";v="24"',
  },
]

const BLOCKED_PATTERNS = [
  /请输入验证码/,
  /验证码/,
  /访问过于频繁/,
  /antispider/i,
  /just\s*a\s*moment/i,
  /please\s*wait/i,
  /access\s*denied/i,
  /rate\s*limit/i,
  /too\s*many\s*requests/i,
  /sorry,\s*your\s*request/i,
]

const MOBILE_ENDPOINTS: Record<string, string> = {
  baidu: 'https://m.baidu.com/s',
  bing: 'https://m.bing.com/search',
  sogou: 'https://m.sogou.com/web',
  '360': 'https://m.so.com/s',
}

let profileIndex = 0
const redirectCache = new Map<string, string>()

export function isMobileEnabled(): boolean {
  return process.env.MOBILE_ENDPOINT === 'true' || process.env.MOBILE_ENDPOINT === '1'
}

export function getMobileSearchUrl(engine: string, desktopUrl: string): string {
  const base = MOBILE_ENDPOINTS[engine]
  if (!base) return desktopUrl
  const parsed = new URL(desktopUrl)
  return base + parsed.search
}

export function pickMobileHeaders(): Record<string, string> {
  profileIndex = (profileIndex + 1) % PROFILES.length
  const p = PROFILES[profileIndex]

  const ua = p.ua
    .replace(/Windows NT 10\.0; Win64; x64/, 'Linux; Android 14; Mobile')
    .replace(/Macintosh; Intel Mac OS X \d+_\d+_\d+/, 'Linux; Android 14; Mobile')
    .replace(/X11; Linux x86_64/, 'Linux; Android 14; Mobile')

  return {
    'User-Agent': ua,
    'Accept-Language': p.lang,
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Cache-Control': 'no-cache',
    Pragma: 'no-cache',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1',
  }
}

export function pickHeaders(domain?: string): Record<string, string> {
  profileIndex = (profileIndex + 1) % PROFILES.length
  const p = PROFILES[profileIndex]

  const headers: Record<string, string> = {
    'User-Agent': p.ua,
    'Accept-Language': p.lang,
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Cache-Control': 'no-cache',
    Pragma: 'no-cache',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1',
  }

  if (p.brand) {
    headers['sec-ch-ua'] = p.brand
    headers['sec-ch-ua-mobile'] = p.mobile
    headers['sec-ch-ua-platform'] = p.platform
  }

  if (domain) {
    try {
      const { getExtraHeaders } = require('./config.js')
      const extra = getExtraHeaders(domain)
      Object.assign(headers, extra)
    } catch { }
  }

  return headers
}

export function createFetchOptions(domain?: string): Record<string, unknown> {
  const opts: Record<string, unknown> = {}
  const proxyUrl = process.env.SEARCH_PROXY
  if (proxyUrl) {
    try {
      const { ProxyAgent } = require('undici')
      opts.dispatcher = new ProxyAgent(proxyUrl)
    } catch { }
  }
  return opts
}

export function isBlocked(html: string): boolean {
  const lower = html.toLowerCase()
  if (BLOCKED_PATTERNS.some(p => p.test(lower))) return true
  if (html.length < 8000 && (/captcha/.test(lower) || /challenge/.test(lower) || /verify/.test(lower) || /blocked/.test(lower) || /安全检查/.test(lower) || /安全验证/.test(lower) || /安全检测/.test(lower))) return true
  return false
}

export function delayMs(min = 300, max = 1200): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

// Box-Muller transform for normal distribution
function gaussRandom(mean: number, stddev: number): number {
  let u = 0, v = 0
  while (u === 0) u = Math.random()
  while (v === 0) v = Math.random()
  const z = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v)
  return Math.round(mean + z * stddev)
}

export function normalDelayMs(mean = 800, stddev = 300, min = 100, max = 3000): number {
  let d = gaussRandom(mean, stddev)
  d = Math.max(min, Math.min(max, d))
  return d
}

export function isDelayNormal(): boolean {
  return process.env.DELAY_MODE === 'normal'
}

export async function adaptiveDelay(domain: string, baseMin = 400, baseMax = 1500): Promise<void> {
  try {
    const { getRateLimit } = require('./config.js')
    const limit = getRateLimit(domain)
    if (limit) {
      if (isDelayNormal()) {
        const mid = (limit.minDelay + limit.maxDelay) / 2
        const sd = (limit.maxDelay - limit.minDelay) / 4
        return new Promise(r => setTimeout(r, normalDelayMs(mid, sd, limit.minDelay, limit.maxDelay)))
      }
      return new Promise(r => setTimeout(r, delayMs(limit.minDelay, limit.maxDelay)))
    }
  } catch { }
  if (isDelayNormal()) {
    const mid = (baseMin + baseMax) / 2
    const sd = (baseMax - baseMin) / 4
    return new Promise(r => setTimeout(r, normalDelayMs(mid, sd, baseMin, baseMax)))
  }
  return new Promise(r => setTimeout(r, delayMs(baseMin, baseMax)))
}

export async function resolveRedirect(url: string): Promise<string> {
  const cached = redirectCache.get(url)
  if (cached) return cached

  try {
    const res = await fetch(url, {
      method: 'HEAD',
      headers: pickHeaders(),
      redirect: 'manual',
      signal: AbortSignal.timeout(3000),
    })
    if (res.status >= 300 && res.status < 400) {
      const loc = res.headers.get('location')
      if (loc) {
        const resolved = new URL(loc, url).toString()
        redirectCache.set(url, resolved)
        return resolved
      }
    }
  } catch { }

  return url
}

export async function resolveResultUrls(
  results: Array<{ url: string }>,
  concurrency = 3
): Promise<void> {
  const redirectDomains = ['so.com/link', 'baidu.com/link', 'sogou.com']
  const toResolve = results.filter(r => redirectDomains.some(d => r.url.includes(d)))
  if (toResolve.length === 0) return

  for (let i = 0; i < toResolve.length; i += concurrency) {
    const batch = toResolve.slice(i, i + concurrency)
    await Promise.all(batch.map(async r => {
      r.url = await resolveRedirect(r.url)
    }))
  }
}
