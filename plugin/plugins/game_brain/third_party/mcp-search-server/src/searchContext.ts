import type { SearchResult } from './types.js'

interface SearchSession {
  id: string
  query: string
  allResults: SearchResult[]
  timestamp: number
}

const sessions = new Map<string, SearchSession>()
const TTL = 10 * 60 * 1000

let nextId = 1

export function saveResults(query: string, results: SearchResult[]): string {
  for (const [id, s] of sessions) {
    if (s.query === query && Date.now() - s.timestamp < 60000) {
      s.allResults = results
      s.timestamp = Date.now()
      return id
    }
  }
  const id = `s${nextId++}`
  sessions.set(id, { id, query, allResults: results, timestamp: Date.now() })
  // cleanup expired
  for (const [k, v] of sessions) {
    if (Date.now() - v.timestamp > TTL) sessions.delete(k)
  }
  return id
}

export function getSession(id: string): SearchSession | undefined {
  const s = sessions.get(id)
  if (!s) return undefined
  if (Date.now() - s.timestamp > TTL) {
    sessions.delete(id)
    return undefined
  }
  return s
}

export function refineResults(sessionId: string, opts: {
  engine?: string
  keyword?: string
  domain?: string
  offset?: number
  limit?: number
}): { results: SearchResult[]; total: number } {
  const session = getSession(sessionId)
  if (!session) return { results: [], total: 0 }

  let filtered = session.allResults

  if (opts.engine) {
    const engines = opts.engine.split(',').map(s => s.trim().toLowerCase())
    filtered = filtered.filter(r => engines.includes(r.engine.toLowerCase()))
  }

  if (opts.keyword) {
    const kw = opts.keyword.toLowerCase()
    filtered = filtered.filter(r =>
      r.title.toLowerCase().includes(kw) || r.description.toLowerCase().includes(kw)
    )
  }

  if (opts.domain) {
    filtered = filtered.filter(r => {
      try { return new URL(r.url).hostname.includes(opts.domain!) }
      catch { return false }
    })
  }

  const total = filtered.length
  const offset = opts.offset || 0
  const limit = opts.limit || total
  return { results: filtered.slice(offset, offset + limit), total }
}

export const SEARCH_PROFILES: Record<string, { label: string; engines: string[]; description: string }> = {
  general: {
    label: '综合',
    engines: ['bing', 'baidu', '360', 'github', 'zhihu'],
    description: 'Bing + 百度 + 360 + GitHub + 知乎',
  },
  tech: {
    label: '技术',
    engines: ['bing', 'github', 'zhihu'],
    description: 'Bing + GitHub + 知乎',
  },
  chinese: {
    label: '中文',
    engines: ['bing', 'sogou', 'baidu', 'zhihu'],
    description: '中文内容优先',
  },
  code: {
    label: '代码',
    engines: ['bing', 'github'],
    description: 'GitHub + Bing',
  },
  fast: {
    label: '快速',
    engines: ['bing'],
    description: '仅 Bing，速度最快',
  },
  deep: {
    label: '深度',
    engines: ['bing', 'baidu', 'sogou', '360', 'zhihu', 'github', 'duckduckgo'],
    description: '全部可用引擎',
  },
}

// Allow custom profile via SEARCH_CUSTOM_ENGINES env var
const customEngines = process.env.SEARCH_CUSTOM_ENGINES
if (customEngines) {
  const parts = customEngines.split(',').map(s => s.trim()).filter(Boolean)
  if (parts.length > 0) {
    SEARCH_PROFILES.custom = {
      label: '自定义',
      engines: parts,
      description: parts.join(' + '),
    }
  }
}
