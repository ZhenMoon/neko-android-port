import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked } from '../scraper.js'

const GITHUB_URL = 'https://github.com/search'

export class GitHubEngine implements SearchEngine {
  readonly name = 'github'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const results: SearchResult[] = []
    let page = 1

    try {
      while (results.length < maxResults) {
        const url = new URL(GITHUB_URL)
        url.searchParams.set('q', query)
        url.searchParams.set('type', 'repositories')
        url.searchParams.set('p', String(page))

        const res = await fetch(url.toString(), {
          headers: pickHeaders(),
          signal,
        })

        const html = await res.text()
        if (isBlocked(html)) break

        const $ = cheerio.load(html)
        const items = $('[data-testid="results-list"] > div')

        if (items.length === 0) break

        let pageCount = 0
        for (const el of items) {
          if (results.length >= maxResults) break

          const repoLink = $(el).find('a[href^="/"][href*="/"]').first()
          const href = repoLink.attr('href') || ''
          const segments = href.split('/').filter(Boolean)
          if (segments.length < 2) continue

          const fullUrl = `https://github.com${href}`
          const title = `${segments[0]}/${segments[1]}`
          const descEl = $(el).find('p, div[class*="description"]')
          const description = descEl.text().trim()

          results.push({ title, url: fullUrl, description, engine: this.name })
          pageCount++
        }

        if (pageCount === 0) break
        page++
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') return results.slice(0, maxResults)
      throw err
    }

    return results.slice(0, maxResults)
  }
}
