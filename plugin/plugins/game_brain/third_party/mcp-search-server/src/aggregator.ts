import type { SearchResult, SearchEngine, SearchOptions } from './types.js'
import { DuckDuckGoEngine } from './engines/duckduckgo.js'
import { BingEngine } from './engines/bing.js'
import { SogouEngine } from './engines/sogou.js'
import { BaiduEngine } from './engines/baidu.js'
import { BraveEngine } from './engines/brave.js'
import { GitHubEngine } from './engines/github.js'
import { ZhihuEngine } from './engines/zhihu.js'
import { So360Engine } from './engines/360.js'
import { CsdnEngine } from './engines/csdn.js'
import { CustomSearchEngine } from './customEngine.js'
import { getCustomEngines, getRateLimit } from './config.js'
import { trimResults, isFreshnessQuery, isStaticPage } from './filter.js'
import { adaptQuery, getQueryInfo } from './queryAdapter.js'
import { getCached, setCache } from './cache.js'
import { isEngineAvailable, recordFailure, recordSuccess } from './circuitBreaker.js'
import { expandQuery } from './queryExpander.js'
import { resolveResultUrls, adaptiveDelay } from './scraper.js'
import { formatResultJson, extractDate, extractDomain } from './metadata.js'

const ENGINES: Record<string, SearchEngine> = {
  duckduckgo: new DuckDuckGoEngine(),
  bing: new BingEngine(),
  sogou: new SogouEngine(),
  baidu: new BaiduEngine(),
  brave: new BraveEngine(),
  github: new GitHubEngine(),
  zhihu: new ZhihuEngine(),
  '360': new So360Engine(),
  csdn: new CsdnEngine(),
}

// inject custom engines
for (const def of getCustomEngines()) {
  ENGINES[def.name] = new CustomSearchEngine(def)
}

export function listAllEngines(): string[] {
  return Object.keys(ENGINES).sort()
}

const DEFAULT_TIMEOUT = 15000
const MIN_PER_ENGINE = 15

export interface EngineReport {
  engine: string
  status: 'ok' | 'error' | 'empty' | 'skipped'
  count: number
  error?: string
}

export interface AggregateResult {
  results: SearchResult[]
  reports: EngineReport[]
}

function termMatches(text: string, term: string): boolean {
  return text.toLowerCase().includes(term)
}

function scoreRelevance(query: string, result: SearchResult, preferFresh = false): number {
  const info = getQueryInfo(query)
  const allTerms = [...info.terms, ...info.phrases]
  if (allTerms.length === 0) return 0.5

  const title = result.title
  const desc = result.description
  const url = result.url

  let titleHits = 0
  let descHits = 0
  let urlHits = 0

  for (const qt of allTerms) {
    if (termMatches(title, qt)) titleHits++
    if (termMatches(desc, qt)) descHits++
    if (termMatches(url, qt)) urlHits++
  }

  if (titleHits === 0 && descHits === 0 && urlHits === 0) return 0

  const titleScore = titleHits / allTerms.length
  const descScore = descHits / allTerms.length
  const urlScore = urlHits / allTerms.length

  let score = titleScore * 0.55 + descScore * 0.3 + urlScore * 0.15

  // freshness: deprioritize static pages for news queries
  if (preferFresh && isStaticPage(result.url)) {
    score *= 0.3
  }

  return score
}

function normalizeTitle(title: string): string {
  return title.toLowerCase().replace(/[\s,，。、；：！？!?\-—·・]+/g, ' ').replace(/\s+/g, ' ').trim()
}

function titleWords(title: string): Set<string> {
  return new Set(normalizeTitle(title).split(/\s+/).filter(Boolean))
}

function titleSimilarity(a: string, b: string): number {
  const wa = titleWords(a)
  const wb = titleWords(b)
  const intersection = new Set([...wa].filter(w => wb.has(w)))
  const union = new Set([...wa, ...wb])
  return intersection.size / union.size
}

function deduplicateByContent(results: SearchResult[]): SearchResult[] {
  const seen: SearchResult[] = []

  for (const r of results) {
    let isDup = false
    for (const existing of seen) {
      if (existing.title === r.title || titleSimilarity(existing.title, r.title) > 0.6) {
        isDup = true
        break
      }
    }
    if (!isDup) seen.push(r)
  }

  return seen
}

function dedupKey(url: string): string {
  const lower = url.toLowerCase()
  // Redirect-based engines: use full URL (params encode destination)
  if (/\.(baidu|so)\.com\/link/.test(lower)) return lower
  if (/sogou\.com/.test(lower) && lower.includes('url=')) return lower
  // Other URLs: strip tracking params
  return lower.split('?')[0].replace(/\/+$/, '')
}

function deduplicateByUrl(results: SearchResult[]): SearchResult[] {
  const seen = new Map<string, SearchResult[]>()

  for (const r of results) {
    const key = dedupKey(r.url)
    const existing = seen.get(key)
    if (existing) {
      existing.push(r)
    } else {
      seen.set(key, [r])
    }
  }

  const out: SearchResult[] = []
  for (const group of seen.values()) {
    group.sort((a, b) => b.description.length - a.description.length)
    out.push(group[0])
  }
  return out
}

export async function aggregateSearch(options: SearchOptions): Promise<SearchResult[]> {
  const { results } = await aggregateWithReport(options)
  return results
}

export async function aggregateWithReport(options: SearchOptions): Promise<AggregateResult> {
  const {
    query,
    maxResults = 10,
    engines: engineNames = ['bing', 'baidu', '360', 'github', 'zhihu'],
    timeout = DEFAULT_TIMEOUT,
  } = options

  // cache check
  const cached = await getCached(query, engineNames as string[], false)
  if (cached) {
    return {
      results: cached.results.slice(0, maxResults),
      reports: cached.reports as EngineReport[],
    }
  }

  const selectedEngines = engineNames
    .filter(name => name in ENGINES)
    .filter(name => isEngineAvailable(name))
    .map(name => ({ name, engine: ENGINES[name] }))

  if (selectedEngines.length === 0) return { results: [], reports: [] }

  const perEngine = Math.max(MIN_PER_ENGINE, Math.ceil(maxResults * 2.5 / selectedEngines.length))

  const enginesWithSignal = selectedEngines.map(({ name, engine }) => {
    const controller = new AbortController()
    const timer = setTimeout(() => { controller.abort() }, timeout)
    return { name, engine, controller, timer, signal: controller.signal }
  })

  const settled = await Promise.allSettled(
    enginesWithSignal.map(({ name, engine, signal, timer }) => {
      const adaptedQuery = adaptQuery(query, name)
      return engine.search(adaptedQuery, perEngine, signal).finally(() => {
        clearTimeout(timer)
      })
    })
  )

  const reports: EngineReport[] = []
  const all: SearchResult[] = []

  for (let i = 0; i < selectedEngines.length; i++) {
    const { name } = selectedEngines[i]
    const result = settled[i]

    if (result.status === 'fulfilled') {
      const items = result.value
      if (items.length === 0) {
        reports.push({ engine: name, status: 'empty', count: 0 })
      } else {
        reports.push({ engine: name, status: 'ok', count: items.length })
        all.push(...items)
        recordSuccess(name)
      }
    } else {
      const reason = result.reason
      reports.push({
        engine: name,
        status: 'error',
        count: 0,
        error: reason instanceof Error ? reason.message : String(reason),
      })
      recordFailure(name)
    }
  }

  for (const { timer } of enginesWithSignal) {
    clearTimeout(timer)
  }

  // query expansion fallback: if too few results, try expanded queries
  let results = all
  if (all.length < maxResults * 2 && all.length > 0) {
    const expanded = expandQuery(query)
    if (expanded.length > 1) {
      for (const eq of expanded.slice(1)) {
        if (results.length >= maxResults * 3) break
        const adapted = adaptQuery(eq, selectedEngines[0]?.name || 'bing')
        const fallbackEngine = ENGINES[selectedEngines[0]?.name || 'bing']
        if (fallbackEngine) {
          try {
            const extra = await fallbackEngine.search(adapted, perEngine)
            results.push(...extra)
          } catch { /* skip */ }
        }
      }
    }
  }

  const trimmed = trimResults(results)

  const preferFresh = isFreshnessQuery(query)

  const scored = trimmed
    .map(r => ({ result: r, score: scoreRelevance(query, r, preferFresh) }))
    .filter(x => x.score > 0)

  scored.sort((a, b) => b.score - a.score)

  let ranked = scored.map(x => x.result)

  // floor: always keep at least maxResults items even if relevance score is low
  if (ranked.length < maxResults && all.length > ranked.length) {
    const scoredSet = new Set(ranked.map(r => r.url))
    const extras = all.filter(r => !scoredSet.has(r.url)).slice(0, maxResults - ranked.length)
    ranked.push(...extras)
  }

  if (ranked.length === 0 && all.length > 0) {
    const coreTerms = query.replace(/[^\w\u4e00-\u9fff\s]/g, ' ').split(/\s+/).filter(t => t.length > 1)
    if (coreTerms.length > 1) {
      const simplified = coreTerms.slice(0, 2).join(' ')
      const fallbackScored = trimmed
        .map(r => ({ result: r, score: scoreRelevance(simplified, r, preferFresh) }))
        .filter(x => x.score > 0)
      fallbackScored.sort((a, b) => b.score - a.score)
      ranked = fallbackScored.map(x => x.result)
    }
    if (ranked.length === 0 && all.length > 0) {
      ranked = all.slice(0, maxResults)
    }
  }

  // engine guarantee: 1 result per engine from the trimmed (pre-score) set,
  // then fill remaining from scored+deduped results
  const byEngine = new Map<string, SearchResult[]>()
  for (const r of trimmed) {
    const list = byEngine.get(r.engine) || []
    list.push(r)
    byEngine.set(r.engine, list)
  }

  const guaranteed: SearchResult[] = []
  const seenKeys = new Set<string>()
  for (const [, items] of byEngine) {
    const best = items[0]
    if (!best) continue
    const key = normalizeTitle(best.title)
    if (seenKeys.has(key)) continue
    seenKeys.add(key)
    guaranteed.push(best)
  }

  // fill remaining slots from scored+deduped results
  const pool = deduplicateByContent(ranked)
  const finalPool = deduplicateByUrl(pool)

  for (const r of finalPool) {
    if (guaranteed.length >= maxResults) break
    const key = normalizeTitle(r.title)
    if (seenKeys.has(key)) continue
    seenKeys.add(key)
    guaranteed.push(r)
  }

  const final = guaranteed.slice(0, maxResults)

  // resolve redirect URLs (360, Baidu, Sogou) for final results
  await resolveResultUrls(final)

  // attach metadata: score, publishedDate, domain
  const scoreMap = new Map(scored.map(x => [x.result.url, x.score]))
  for (const r of final) {
    r.publishedDate = extractDate(r.title) || extractDate(r.description) || undefined
    r.score = scoreMap.get(r.url) || 0
  }

  // write cache
  setCache(query, engineNames as string[], false, { results: final, reports })

  return { results: final, reports }
}


