import * as cheerio from 'cheerio'
import type { SearchResult, SearchEngine } from '../types.js'
import { getPage, isBrowserEnabled } from '../browser.js'
import { pickHeaders, isBlocked } from '../scraper.js'

export class ZhihuEngine implements SearchEngine {
  readonly name = 'zhihu'

  async search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
    if (isBrowserEnabled()) {
      const results = await tryDirectSearch(query, maxResults, signal)
      if (results.length > 0) return results
    }
    return tryBingSearch(query, maxResults, signal)
  }
}

async function tryDirectSearch(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
  let page: Awaited<ReturnType<typeof getPage>> | null = null
  try {
    page = await getPage()
    if (!page) return []

    const url = `https://www.zhihu.com/search?type=content&q=${encodeURIComponent(query)}`

    await page.goto(url, { waitUntil: 'networkidle0', timeout: 15000 })

    await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight)
    })
    await new Promise(r => setTimeout(r, 1000))

    const html = await page.content()
    const $ = cheerio.load(html)

    const results: SearchResult[] = []
    const items = $('.ContentItem-title, .SearchResult-card, .List-item')
    for (const el of items) {
      if (results.length >= maxResults) break
      const link = $(el).find('a[href*="zhihu.com/question"], a[href*="zhihu.com/zvideo"], a[href*="zhuanlan.zhihu.com"]').first()
      const title = link.text().trim()
      const url = link.attr('href') || ''
      if (!title || !url) continue
      const desc = $(el).find('.RichText').first().text().trim()
        || $(el).find('span[class*="summary"]').first().text().trim()
      results.push({ title, url: url.startsWith('/') ? `https://www.zhihu.com${url}` : url, description: desc, engine: 'zhihu' })
    }

    return results
  } catch {
    return []
  } finally {
    if (page) try { await page.close() } catch { }
  }
}

async function tryBingSearch(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]> {
  const results: SearchResult[] = []
  const siteQuery = `site:zhihu.com ${query}`
  let page = 0

  try {
    while (results.length < maxResults) {
      const url = new URL('https://www.bing.com/search')
      url.searchParams.set('q', siteQuery)
      url.searchParams.set('first', String(1 + page * 10))
      url.searchParams.set('setlang', 'zh-CN')

      const res = await fetch(url.toString(), {
        headers: pickHeaders(),
        signal,
      })

      const html = await res.text()
      if (isBlocked(html)) break

      const $ = cheerio.load(html)
      const items = $('#b_results .b_algo')
      if (items.length === 0) break

      let count = 0
      for (const el of items) {
        if (results.length >= maxResults) break
        const link = $(el).find('h2 a')
        const rawUrl = link.attr('href') || ''
        const title = link.text().trim()
        const description = $(el).find('.b_caption p').text().trim()
        if (title && rawUrl && rawUrl.includes('zhihu.com')) {
          results.push({ title, url: rawUrl, description, engine: 'zhihu' })
          count++
        }
      }
      if (count === 0) break
      page++
    }
  } catch (err) {
    if ((err as Error).name !== 'AbortError') return results.slice(0, maxResults)
    throw err
  }

  return results.slice(0, maxResults)
}
