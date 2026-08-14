import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { pickHeaders, isBlocked } from '../scraper.js'

const API_URL = 'https://api.search.brave.com/res/v1/web/search'
const HTML_URL = 'https://search.brave.com/search'

export class BraveEngine implements SearchEngine {
  readonly name = 'brave'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const apiKey = process.env.BRAVE_API_KEY
    if (apiKey) {
      const results = await tryApi(query, maxResults, apiKey, signal)
      if (results.length > 0) return results
    }
    return tryScrape(query, maxResults, signal)
  }
}

async function tryApi(query: string, maxResults: number, apiKey: string, signal?: AbortSignal): Promise<SearchResult[]> {
  try {
    const url = new URL(API_URL)
    url.searchParams.set('q', query)
    url.searchParams.set('count', String(Math.min(maxResults, 20)))

    const res = await fetch(url.toString(), {
      headers: {
        'X-Subscription-Token': apiKey,
        Accept: 'application/json',
      },
      signal,
    })

    if (!res.ok) return []

    const data = await res.json() as any
    const results: SearchResult[] = []

    const web = data.web as { results?: Array<{ title: string; url: string; description: string }> }
    if (web?.results) {
      for (const r of web.results) {
        if (results.length >= maxResults) break
        results.push({
          title: r.title || '',
          url: r.url || '',
          description: r.description || '',
          engine: 'brave',
        })
      }
    }

    return results
  } catch {
    return []
  }
}

async function tryScrape(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
  const results: SearchResult[] = []
  let offset = 0

  try {
    while (results.length < maxResults) {
      const url = new URL(HTML_URL)
      url.searchParams.set('q', query)
      url.searchParams.set('source', 'web')
      url.searchParams.set('offset', String(offset))

      const res = await fetch(url.toString(), {
        headers: pickHeaders(),
        signal,
      })

      const html = await res.text()
      if (isBlocked(html)) break

      const $ = cheerio.load(html)
      const items = $('#results .snippet')

      if (items.length === 0) break

      let pageCount = 0
      for (const el of items) {
        if (results.length >= maxResults) break
        const content = $(el).find('.result-content').first()
        if (content.length === 0) continue

        const mainLink = content.find('> a').first()
        const rawUrl = mainLink.attr('href') || ''
        const title = mainLink.find('.search-snippet-title').text().trim()
        const description = content.find('.generic-snippet').text().trim()

        if (title && rawUrl) {
          results.push({ title, url: rawUrl, description, engine: 'brave' })
          pageCount++
        }
      }

      if (pageCount === 0) break
      offset += pageCount
    }
  } catch {
    // silent
  }

  return results.slice(0, maxResults)
}
