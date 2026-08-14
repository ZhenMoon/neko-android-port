import type { SearchResult } from './types.js'

const DATE_PATTERNS = [
  /(\d{4})[-/](\d{1,2})[-/](\d{1,2})/,
  /(\d{4})年(\d{1,2})月(\d{1,2})日/,
  /(\d{1,2})[-/](\d{1,2})[-/](\d{4})/,
  /([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})/,
  /(\d{4})[-/](\d{1,2})/,
  /(\d{4})年(\d{1,2})月/,
]

export function extractDate(text: string): string | undefined {
  for (const pattern of DATE_PATTERNS) {
    const m = text.match(pattern)
    if (m) {
      if (m.length === 4 && m[1].length === 4) {
        const [, y, mo, d] = m
        return `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`
      }
      if (m.length === 4 && m[3].length === 4) {
        const [, mo, d, y] = m
        return `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`
      }
      if (m.length === 3) {
        const [, y, mo] = m
        return `${y}-${mo.padStart(2, '0')}`
      }
    }
  }
  return undefined
}

export function extractDomain(url: string): string {
  try {
    const u = new URL(url)
    return u.hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

export function updateResultMetadata(
  results: SearchResult[],
  scored?: Map<string, number>,
): SearchResult[] {
  return results.map(r => {
    const dateFromDesc = extractDate(r.description)
    const dateFromTitle = extractDate(r.title)
    return {
      ...r,
      publishedDate: dateFromTitle || dateFromDesc,
      score: scored?.get(r.url) || undefined,
    }
  })
}

export function formatResultJson(results: SearchResult[]): string {
  return JSON.stringify(results.map(r => ({
    title: r.title,
    url: r.url,
    description: r.description,
    engine: r.engine,
    domain: extractDomain(r.url),
    publishedDate: r.publishedDate || null,
    score: r.score ?? null,
  })), null, 2)
}
