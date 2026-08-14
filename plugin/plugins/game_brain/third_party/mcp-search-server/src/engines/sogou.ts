import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked, adaptiveDelay, isMobileEnabled, pickMobileHeaders } from '../scraper.js'
import { fetchWithTLS } from '../tlsFingerprint.js'

const DOMAIN = 'sogou.com'

async function doFetch(url: string, headers: Record<string, string>, signal?: AbortSignal): Promise<string | null> {
  if (Math.random() < 0.3 && process.env.TLS_FINGERPRINT !== 'false') {
    try {
      const r = await fetchWithTLS(url, { headers, signal, timeout: 10000 })
      if (r.status < 400) return r.body
    } catch { /* fall through */ }
  }
  try {
    const res = await fetch(url, { headers, signal })
    return await res.text()
  } catch { return null }
}

export class SogouEngine implements SearchEngine {
  readonly name = 'sogou'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    let page = 1
    const useMobile = isMobileEnabled()

    while (results.length < maxResults && page <= 2) {
      const baseUrl = useMobile ? 'https://m.sogou.com/web' : 'https://www.sogou.com/web'
      const url = new URL(baseUrl)
      url.searchParams.set('query', query)
      url.searchParams.set('page', String(page))
      url.searchParams.set('ie', 'utf8')

      try {
        const headers = useMobile
          ? { ...pickMobileHeaders(), Referer: 'https://m.sogou.com/', 'Accept-Encoding': 'gzip, deflate' }
          : pickHeaders(DOMAIN)

        const html = await doFetch(url.toString(), headers, signal)
        if (!html) break
        if (isBlocked(html)) break

        const $ = cheerio.load(html)
        const items = useMobile
          ? $('.result, .vrwrap, .rb, .result-item')
          : $('.vrwrap, .rb, .result')

        let count = 0
        for (const el of items) {
          if (results.length >= maxResults) break
          const titleLink = $(el).find('h3 a, h2 a, .vr-title a, .title a').first()
          const rawUrl = titleLink.attr('href') || ''
          const title = titleLink.text().trim()
          if (!title || !rawUrl) continue
          const realUrl = resolveUrl(rawUrl)
          if (!realUrl) continue
          const description = $(el).find('.str_info, .ft, .text-layout, .star-wiki, .des, .summary').first().text().trim()
          results.push({ title, url: realUrl, description, engine: 'sogou' })
          count++
        }
        if (count === 0) break
        page++
        if (page < 3) await adaptiveDelay(DOMAIN, 1000, 2000)
      } catch {
        break
      }
    }

    return results.slice(0, maxResults)
  }
}

function resolveUrl(raw: string): string {
  try {
    const u = new URL(raw, 'https://www.sogou.com/web')
    const target = u.searchParams.get('url') || u.searchParams.get('u') || u.searchParams.get('link')
    if (target && /^https?:\/\//i.test(target)) return target
    if (u.protocol.startsWith('http')) return u.toString()
  } catch { }
  return ''
}
