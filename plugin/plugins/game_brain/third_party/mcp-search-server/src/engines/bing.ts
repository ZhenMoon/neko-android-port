import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked, adaptiveDelay, isMobileEnabled, pickMobileHeaders } from '../scraper.js'
import { fetchWithTLS } from '../tlsFingerprint.js'

const BING_DOMAIN = 'bing.com'

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

export class BingEngine implements SearchEngine {
  readonly name = 'bing'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    let page = 0
    const useMobile = isMobileEnabled()

    try {
      while (results.length < maxResults) {
        const baseUrl = useMobile ? 'https://m.bing.com/search' : 'https://www.bing.com/search'
        const url = new URL(baseUrl)
        url.searchParams.set('q', query)
        url.searchParams.set('first', String(1 + page * 10))
        url.searchParams.set('setlang', 'zh-CN')

        const headers = useMobile
          ? { ...pickMobileHeaders(), Referer: 'https://m.bing.com/' }
          : pickHeaders(BING_DOMAIN)

        const html = await doFetch(url.toString(), headers, signal)
        if (!html) break
        if (isBlocked(html)) break

        const $ = cheerio.load(html)
        const selector = useMobile ? '.b_algo, .card-wide' : '#b_results .b_algo'
        const items = $(selector)

        if (items.length === 0) break

        let pageCount = 0
        for (const el of items) {
          if (results.length >= maxResults) break
          const link = $(el).find('h2 a')
          const title = link.text().trim()
          const url = link.attr('href') || ''
          const description = $(el).find('.b_caption p').text().trim()

          if (title && url) {
            results.push({ title, url, description, engine: this.name })
            pageCount++
          }
        }

        if (pageCount === 0) break
        page++
        if (page < 3) await adaptiveDelay(BING_DOMAIN, 300, 1200)
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') throw err
    }

    return results.slice(0, maxResults)
  }
}
