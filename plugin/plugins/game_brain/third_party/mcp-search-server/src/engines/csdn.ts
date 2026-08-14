import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked, adaptiveDelay } from '../scraper.js'

const API = 'https://so.csdn.net/api/v2/search'
const DOMAIN = 'csdn.net'

function stripEm(text: string): string {
  return text.replace(/<[^>]+>/g, '').trim()
}

function cleanUrl(raw: string): string {
  try {
    const u = new URL(raw)
    const allowed = ['articleid', 'spm']
    const keys = [...u.searchParams.keys()]
    for (const k of keys) {
      if (!allowed.includes(k)) u.searchParams.delete(k)
    }
    return u.toString()
  } catch {
    return raw
  }
}

function parseTime(ts: string): string | undefined {
  const n = parseInt(ts, 10)
  if (!isNaN(n) && n > 0) {
    const d = new Date(n)
    if (!isNaN(d.getTime())) return d.toISOString().split('T')[0]
  }
  return undefined
}

export class CsdnEngine implements SearchEngine {
  readonly name = 'csdn'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    let page = 1

    try {
      while (results.length < maxResults && page <= 5) {
        const url = new URL(API)
        url.searchParams.set('q', query)
        url.searchParams.set('t', 'blog')
        url.searchParams.set('p', String(page))
        url.searchParams.set('s', '0')
        url.searchParams.set('tm', '0')
        url.searchParams.set('v', '3')
        url.searchParams.set('l', 'null')
        url.searchParams.set('ft', 'null')
        url.searchParams.set('lv', 'null')
        url.searchParams.set('isc', '')
        url.searchParams.set('page_size', String(Math.min(maxResults, 20)))

        const res = await fetch(url.toString(), {
          headers: {
            ...pickHeaders(DOMAIN),
            Accept: 'application/json, text/plain, */*',
            Referer: 'https://so.csdn.net/',
          },
          signal,
        })

        const text = await res.text()
        if (isBlocked(text)) break

        const data = JSON.parse(text)
        const items = data.result_vos
        if (!items || items.length === 0) break

        let count = 0
        for (const item of items) {
          if (results.length >= maxResults) break
          const title = stripEm(item.title || '')
          const rawUrl = item.url || ''
          const description = stripEm(item.description || item.digest || '')
          if (!title || !rawUrl) continue

          results.push({
            title,
            url: cleanUrl(rawUrl),
            description,
            engine: this.name,
            publishedDate: parseTime(item.create_time) || item.created_at || undefined,
          })
          count++
        }

        if (count === 0) break
        page++
        if (page < 5) await adaptiveDelay(DOMAIN, 500, 1500)
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') throw err
    }

    return results.slice(0, maxResults)
  }
}
