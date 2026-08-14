import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from './types.js'
import type { CustomEngineDef } from './config.js'
import { pickHeaders, isBlocked, delayMs } from './scraper.js'

export class CustomSearchEngine implements SearchEngine {
  readonly name: string
  private def: CustomEngineDef

  constructor(def: CustomEngineDef) {
    this.name = def.name
    this.def = def
  }

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    const def = this.def
    let page = def.startPage || 1

    try {
      while (results.length < maxResults && page <= 5) {
        const url = this.buildUrl(query, page)
        const headers: Record<string, string> = {
          ...pickHeaders(),
          ...def.headers,
        }
        if (def.referer) {
          headers['Referer'] = def.referer
        }

        const res = await fetch(url, { headers, signal })
        const html = await res.text()

        if (isBlocked(html)) break

        const $ = cheerio.load(html)
        const items = $(def.selectors.item)
        if (items.length === 0) break

        let count = 0
        for (const el of items) {
          if (results.length >= maxResults) break
          const title = this.extractText($(el), def.selectors.title)
          const rawUrl = this.extractAttr($(el), def.selectors.url, 'href')
          const description = def.selectors.description
            ? this.extractText($(el), def.selectors.description)
            : ''
          if (!title || !rawUrl) continue
          const resolvedUrl = this.resolveUrl(rawUrl)
          if (!resolvedUrl) continue

          results.push({ title, url: resolvedUrl, description, engine: this.name })
          count++
        }

        if (count === 0) break
        page++
        if (page < 5) await new Promise(r => setTimeout(r, delayMs(200, 800)))
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') throw err
    }

    return results.slice(0, maxResults)
  }

  private buildUrl(query: string, page: number): string {
    let url = this.def.searchUrl
      .replace(/\{query\}/g, encodeURIComponent(query))
      .replace(/\{page\}/g, String(page))
      .replace(/\{encodedQuery\}/g, encodeURIComponent(query))

    if (this.def.pageParam && page > (this.def.startPage || 1)) {
      const sep = url.includes('?') ? '&' : '?'
      url += `${sep}${this.def.pageParam}=${page}`
    }

    return url
  }

  private extractText(el: cheerio.Cheerio<any>, selector: string): string {
    return el.find(selector).first().text().trim()
  }

  private extractAttr(el: cheerio.Cheerio<any>, selector: string, attr: string): string {
    const parts = selector.split('@')
    const sel = parts[0].trim()
    const targetAttr = parts[1] || attr
    return el.find(sel).first().attr(targetAttr) || ''
  }

  private resolveUrl(raw: string): string {
    if (raw.startsWith('http://') || raw.startsWith('https://')) return raw
    if (raw.startsWith('//')) return 'https:' + raw
    try {
      const base = new URL(this.def.searchUrl).origin
      return new URL(raw, base).toString()
    } catch {
      return ''
    }
  }
}
