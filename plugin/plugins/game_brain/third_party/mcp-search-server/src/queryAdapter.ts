/**
 * Engine-specific query adaptation.
 * Different engines handle syntax like -keyword, "phrase", site: differently.
 */

export interface AdaptedQuery {
  raw: string
  adapted: string
  exclusions: string[]
  phrases: string[]
  siteFilter: string
}

function parseQuery(raw: string): { terms: string[]; exclusions: string[]; phrases: string[]; siteFilter: string } {
  const exclusions: string[] = []
  const phrases: string[] = []
  let siteFilter = ''
  const terms: string[] = []

  // extract quoted phrases
  let rest = raw.replace(/["""」『]([^""」』]+)["""」』]/g, (_, p) => {
    phrases.push(p.trim())
    return ''
  })

  // extract -keyword exclusions
  rest = rest.replace(/-(\S+)/g, (_, kw) => {
    exclusions.push(kw)
    return ''
  })

  // extract site: filter
  rest = rest.replace(/site:(\S+)/gi, (_, domain) => {
    siteFilter = domain
    return ''
  })

  // remaining terms
  for (const t of rest.split(/\s+/)) {
    const trimmed = t.trim()
    if (trimmed) terms.push(trimmed)
  }

  return { terms, exclusions, phrases, siteFilter }
}

const ENGINE_QUERY_CONFIG: Record<string, {
  excludePrefix: string  // how to prepend exclusion words
  phraseWrapper: (p: string) => string
  maxTerms: number
}> = {
  bing: {
    excludePrefix: '-',
    phraseWrapper: p => `"${p}"`,
    maxTerms: 30,
  },
  baidu: {
    excludePrefix: '-',
    phraseWrapper: p => `"${p}"`,
    maxTerms: 20,
  },
  sogou: {
    excludePrefix: '-',  // sogou supports -keyword
    phraseWrapper: p => `"${p}"`,
    maxTerms: 20,
  },
  duckduckgo: {
    excludePrefix: '-',
    phraseWrapper: p => `"${p}"`,
    maxTerms: 30,
  },
  brave: {
    excludePrefix: '-',
    phraseWrapper: p => `"${p}"`,
    maxTerms: 30,
  },
  github: {
    excludePrefix: '-',  // actually NOT supported, converted below
    phraseWrapper: p => `"${p}"`,
    maxTerms: 10,
  },
  zhihu: {
    excludePrefix: '-',
    phraseWrapper: p => `"${p}"`,
    maxTerms: 30,
  },
}

export function adaptQuery(raw: string, engine: string): string {
  const config = ENGINE_QUERY_CONFIG[engine]
  if (!config) return raw

  const { terms, exclusions, phrases, siteFilter } = parseQuery(raw)
  const parts: string[] = []

  // site: filter (some engines don't support it)
  if (siteFilter && engine !== 'baidu') {
    parts.push(`site:${siteFilter}`)
  }

  // quoted phrases
  for (const p of phrases) {
    parts.push(config.phraseWrapper(p))
  }

  // regular terms
  if (terms.length > 0) {
    parts.push(terms.slice(0, config.maxTerms).join(' '))
  }

  // exclusions - GitHub doesn't support -keyword, convert to NOT
  for (const ex of exclusions) {
    if (engine === 'github') {
      parts.push(`NOT ${ex}`)
    } else {
      parts.push(`${config.excludePrefix}${ex}`)
    }
  }

  return parts.join(' ').trim() || raw
}

// Expose parsed info for analysis
export function getQueryInfo(raw: string): { terms: string[]; exclusions: string[]; phrases: string[]; siteFilter: string } {
  return parseQuery(raw)
}
