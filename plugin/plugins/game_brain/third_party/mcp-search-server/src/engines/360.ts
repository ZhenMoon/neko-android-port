import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked, adaptiveDelay, isMobileEnabled, pickMobileHeaders } from '../scraper.js'
import { fetchWithTLS } from '../tlsFingerprint.js'

const DOMAIN = 'so.com'

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

export class So360Engine implements SearchEngine {
  readonly name = '360'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    let page = 1
    const useMobile = isMobileEnabled()

    try {
      while (results.length < maxResults && page <= 5) {
        const baseUrl = useMobile ? 'https://m.so.com/s' : 'https://www.so.com/s'
        const url = new URL(baseUrl)
        url.searchParams.set('q', query)
        url.searchParams.set('ie', 'utf-8')
        if (page > 1) url.searchParams.set('pn', String(page))

        const headers = useMobile
          ? { ...pickMobileHeaders(), Referer: 'https://m.so.com/' }
          : { ...pickHeaders(DOMAIN), Referer: 'https://www.so.com/' }

        const html = await doFetch(url.toString(), headers, signal)
        if (!html) break
        if (isBlocked(html)) break

        const $ = cheerio.load(html)

        if (useMobile) {
          const items = $('.result, .item, .res-list')
          if (items.length === 0) break
          let count = 0
          for (const el of items) {
            if (results.length >= maxResults) break
            const titleEl = $(el).find('h3 a, .title a, .res-title a').first()
            const title = titleEl.text().trim()
            const rawUrl = titleEl.attr('href') || $(el).find('a').first().attr('href') || ''
            if (!title || !rawUrl) continue
            const url = resolveUrl(rawUrl)
            if (!url) continue
            const description = $(el).find('.des, .res-desc, .summary').first().text().trim()
            results.push({ title, url, description, engine: this.name })
            count++
          }
          if (count === 0) break
        } else {
          const items = $('.res-list')
          if (items.length === 0) break
          let count = 0
          for (const el of items) {
            if (results.length >= maxResults) break
            const titleEl = $(el).find('h3 a').first()
            const title = titleEl.text().trim()
            const rawUrl = titleEl.attr('href') || ''
            if (!title || !rawUrl) continue
            const url = resolveUrl(rawUrl)
            if (!url) continue
            const description = $(el).find('.res-desc').first().text().trim()
              || $(el).find('.res-rich').first().text().trim()
            results.push({ title, url, description, engine: this.name })
            count++
          }
          if (count === 0) break
        }

        page++
        if (page < 5) await adaptiveDelay(DOMAIN, 300, 1000)
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') return results.slice(0, maxResults)
      throw err
    }

    return results.slice(0, maxResults)
  }
}

function resolveUrl(raw: string): string {
  if (raw.startsWith('http')) return raw
  if (raw.startsWith('//')) return 'https:' + raw
  if (raw.startsWith('/link?m=') || raw.startsWith('link?m=')) {
    return 'https://www.so.com' + (raw.startsWith('/') ? '' : '/') + raw
  }
  return ''
}
