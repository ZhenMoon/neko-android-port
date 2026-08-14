import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { getCookie, setCookie, clearCookies, warmUp } from '../session.js'
import { pickHeaders, isBlocked, isMobileEnabled, getMobileSearchUrl, pickMobileHeaders, isDelayNormal, normalDelayMs } from '../scraper.js'
import { fetchWithTLS } from '../tlsFingerprint.js'

const DOMAIN = 'baidu.com'

async function doFetch(url: string, headers: Record<string, string>, signal?: AbortSignal): Promise<string | null> {
  // randomly use TLS fingerprint rotation (30% of requests)
  if (Math.random() < 0.3 && process.env.TLS_FINGERPRINT !== 'false') {
    try {
      const r = await fetchWithTLS(url, { headers, signal, timeout: 10000 })
      if (r.status < 400) return r.body
    } catch { /* fall through to normal fetch */ }
  }

  try {
    const res = await fetch(url, { headers, signal })
    return await res.text()
  } catch { return null }
}

function buildHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const cookie = getCookie(DOMAIN)
  const shared = pickHeaders()
  return {
    'User-Agent': shared['User-Agent'],
    'Accept-Language': shared['Accept-Language'],
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Cache-Control': 'no-cache',
    Pragma: 'no-cache',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1',
    Connection: 'keep-alive',
    ...(cookie ? { Cookie: cookie } : {}),
    ...extra,
  }
}

function randomDelay(min = 200, max = 800): Promise<void> {
  let ms: number
  if (isDelayNormal()) {
    const mid = (min + max) / 2
    const sd = (max - min) / 4
    ms = normalDelayMs(mid, sd, min, max)
  } else {
    ms = Math.floor(Math.random() * (max - min + 1)) + min
  }
  return new Promise(r => setTimeout(r, ms))
}

async function fetchBaiduSearch(query: string, pn: number, signal?: AbortSignal): Promise<string | null> {
  const useMobile = isMobileEnabled()
  const baseUrl = useMobile ? 'https://m.baidu.com/s' : 'https://www.baidu.com/s'
  const url = new URL(baseUrl)
  url.searchParams.set('wd', query)
  url.searchParams.set('pn', pn.toString())
  url.searchParams.set('ie', 'utf-8')
  if (!useMobile) {
    url.searchParams.set('f', '8')
    url.searchParams.set('rsv_bp', '1')
    url.searchParams.set('rsv_idx', '1')
  }

  try {
    const headers = useMobile
      ? { ...pickMobileHeaders(), ...(getCookie(DOMAIN) ? { Cookie: getCookie(DOMAIN)! } : {}), Referer: 'https://m.baidu.com/' }
      : buildHeaders({ Referer: 'https://www.baidu.com/' })

    const html = await doFetch(url.toString(), headers, signal)

    if (!html || html.length < 500) return null
    if (isBlocked(html)) return null
    if (!useMobile && html.includes('https://www.baidu.com/cache/setblock/')) return null
    return html
  } catch {
    return null
  }
}

function isBaiduBlocked(html: string): boolean {
  const lower = html.toLowerCase()
  return lower.includes('antispider') || lower.includes('请输入验证码') || lower.includes('访问频率')
    || html.includes('https://www.baidu.com/cache/setblock/')
}

function parseBaiduResults(html: string, maxResults: number, results: SearchResult[]): number {
  const $ = cheerio.load(html)
  const items = $('#content_left').children()
  let count = 0

  for (const el of items) {
    if (results.length >= maxResults) break
    const h3 = $(el).find('h3')
    const title = h3.text().trim()
    if (!title) continue
    const link = h3.find('a').first()
    const rawUrl = link.attr('href') || ''
    if (!rawUrl) continue
    if (title.includes('百度图片') || rawUrl.includes('image.baidu.com')) continue

    const realUrl = resolveBaiduUrl(rawUrl)
    let description = ''
    for (const sel of ['.c-abstract', '.c-color-text', '.c-span18', '.cos-row',
      '.content-right_8Zs40', '.cosc-card-content-border', '[class*="abstract"]',
      '.c-gap-top-small', '.cosc-card-content', '[class*="content-border"]']) {
      const d = $(el).find(sel).first().text().trim().replace(/\s+/g, ' ')
      if (d.length > description.length) description = d
    }
    if (!description) {
      description = $(el).text().replace(/\s+/g, ' ').trim().substring(0, 200)
    }

    results.push({ title, url: realUrl, description, engine: 'baidu' })
    count++
  }

  return count
}

export class BaiduEngine implements SearchEngine {
  readonly name = 'baidu'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []

    await warmUp('https://www.baidu.com/', DOMAIN, buildHeaders({ Referer: 'https://www.baidu.com/' }), signal)
    if (getCookie(DOMAIN)) {
      await randomDelay(300, 1000)
    }

    let pn = 0
    let consecutiveEmpty = 0

    try {
      while (results.length < maxResults) {
        const html = await fetchBaiduSearch(query, pn, signal)
        if (!html) {
          consecutiveEmpty++
          if (consecutiveEmpty >= 2) break
          // retry with fresh cookies
          clearCookies(DOMAIN)
          await warmUp('https://www.baidu.com/', DOMAIN, buildHeaders({ Referer: 'https://www.baidu.com/' }), signal)
          await randomDelay(500, 1500)
          continue
        }

        consecutiveEmpty = 0
        const count = parseBaiduResults(html, maxResults, results)
        if (count === 0) break
        pn += 10

        await randomDelay(100, 400)
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') throw err
    }

    return results.slice(0, maxResults)
  }
}

function resolveBaiduUrl(raw: string): string {
  if (!raw.includes('baidu.com/link?')) return raw
  try {
    const u = new URL(raw.startsWith('http') ? raw : `https://${raw}`)
    const target = u.searchParams.get('url')
    if (target && /^https?:\/\//i.test(target)) return target
    return u.toString()
  } catch {
    return raw
  }
}
