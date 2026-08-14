import { readFile, writeFile, mkdir } from 'fs/promises'
import { existsSync } from 'fs'
import { createHash } from 'crypto'
import type { SearchResult } from './types.js'

const CACHE_DIR = '.opencode'
const CACHE_TTL = 5 * 60 * 1000

interface CacheEntry {
  results: SearchResult[]
  reports: Array<{ engine: string; status: string; count: number; error?: string }>
  timestamp: number
}

function cacheKey(query: string, engines: string[], useNeural: boolean): string {
  const raw = `${query}::${engines.sort().join(',')}::${useNeural}`
  return createHash('md5').update(raw).digest('hex')
}

function cachePath(key: string): string {
  return `${CACHE_DIR}/search_${key}.json`
}

export async function getCached(query: string, engines: string[], useNeural: boolean): Promise<CacheEntry | null> {
  try {
    const key = cacheKey(query, engines, useNeural)
    const path = cachePath(key)
    if (!existsSync(path)) return null
    const raw = await readFile(path, 'utf-8')
    const entry: CacheEntry = JSON.parse(raw)
    if (Date.now() - entry.timestamp > CACHE_TTL) return null
    return entry
  } catch {
    return null
  }
}

export async function setCache(
  query: string, engines: string[], useNeural: boolean,
  data: { results: SearchResult[]; reports: Array<{ engine: string; status: string; count: number; error?: string }> }
): Promise<void> {
  try {
    if (!existsSync(CACHE_DIR)) await mkdir(CACHE_DIR, { recursive: true })
    const key = cacheKey(query, engines, useNeural)
    const path = cachePath(key)
    const entry: CacheEntry = { ...data, timestamp: Date.now() }
    await writeFile(path, JSON.stringify(entry), 'utf-8')
  } catch {
    // silent
  }
}
