import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked } from '../scraper.js'

const DDG_HTML_URL = 'https://html.duckduckgo.com/html/'

export class DuckDuckGoEngine implements SearchEngine {
  readonly name = 'duckduckgo'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    let offset = 0

    try {
      while (results.length < maxResults) {
        const body = new URLSearchParams({ q: query })
        if (offset > 0) {
          body.set('s', offset.toString())
          body.set('dc', offset.toString())
        }

        const res = await fetch(DDG_HTML_URL, {
          method: 'POST',
          headers: {
            ...pickHeaders(),
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: body.toString(),
          signal,
        })

        const html = await res.text()
        if (isBlocked(html)) break

        const $ = cheerio.load(html)
        const items = $('div.result')

        if (items.length === 0) break

        let pageCount = 0
        for (const el of items) {
          if (results.length >= maxResults) break
          const titleEl = $(el).find('a.result__a')
          const snippetEl = $(el).find('.result__snippet')
          const title = titleEl.text().trim()
          const url = titleEl.attr('href') || ''
          const description = snippetEl.text().trim()

          if (title && url && !$(el).hasClass('result--ad')) {
            results.push({ title, url, description, engine: this.name })
            pageCount++
          }
        }

        if (pageCount === 0) break
        offset += pageCount
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') return results.slice(0, maxResults)
      throw err
    }

    return results.slice(0, maxResults)
  }
}
