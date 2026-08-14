import { JSDOM } from 'jsdom'
import { Readability } from '@mozilla/readability'
import { deduplicateContent } from './dedupContent.js'
import { pickHeaders } from './scraper.js'
import { fetchWithTLS } from './tlsFingerprint.js'

const MAX_RETRIES = 2
const RETRY_DELAY = 1000

export interface FetchResult {
  url: string
  title: string
  content: string
  excerpt: string
  length: number
}

async function fetchWithRetry(url: string, timeout: number, attempt: number): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const origin = new URL(url).hostname

    if (Math.random() < 0.3 && process.env.TLS_FINGERPRINT !== 'false') {
      try {
        const r = await fetchWithTLS(url, {
          headers: pickHeaders(origin),
          signal: controller.signal,
          timeout,
        })
        if (r.status < 400) {
          clearTimeout(timer)
          return new Response(r.body, {
            status: r.status,
            statusText: r.statusText,
            headers: r.headers,
          })
        }
      } catch { /* fall through */ }
    }

    const res = await fetch(url, {
      headers: pickHeaders(origin),
      signal: controller.signal,
      redirect: 'follow',
    })
    return res
  } catch (e) {
    if (attempt < MAX_RETRIES) {
      await new Promise(r => setTimeout(r, RETRY_DELAY * (attempt + 1)))
      return fetchWithRetry(url, timeout, attempt + 1)
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}

export async function fetchPage(url: string, timeout = 15000): Promise<FetchResult> {
  const res = await fetchWithRetry(url, timeout, 0)

  const html = await res.text()
  const dom = new JSDOM(html, { url })
  const reader = new Readability(dom.window.document)
  const article = reader.parse()

  let content: string

  if (!article) {
    const title = dom.window.document.title?.trim() || ''
    const body = dom.window.document.body
    if (body) {
      const clone = body.cloneNode(true) as HTMLElement
      const $scr = clone.querySelectorAll('script, style, nav, footer, header, aside, iframe, svg, form, noscript, [role="navigation"]')
      for (const el of $scr) el.remove()
      content = clone.textContent?.replace(/\s+/g, ' ').trim() || ''
    } else {
      content = ''
    }
  } else {
    content = article.textContent?.replace(/\s+/g, ' ').trim() || ''
  }

  const cleaned = deduplicateContent(content)

  return {
    url: res.url,
    title: article?.title || dom.window.document.title?.trim() || '',
    content: cleaned,
    excerpt: cleaned.substring(0, 200),
    length: cleaned.length,
  }
}
