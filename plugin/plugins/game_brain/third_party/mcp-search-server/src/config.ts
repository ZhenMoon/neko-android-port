import { existsSync, readFileSync } from 'fs'
import path from 'path'

export interface CustomEngineDef {
  name: string
  displayName?: string
  searchUrl: string
  selectors: {
    item: string
    title: string
    url: string
    description: string
    nextPage?: string
  }
  headers?: Record<string, string>
  referer?: string
  pageParam?: string
  startPage?: number
}

export interface RateLimitDef {
  minDelay: number
  maxDelay: number
}

export interface SearchConfig {
  customEngines: CustomEngineDef[]
  proxies: string[]
  rateLimits: Record<string, RateLimitDef>
  extraHeaders: Record<string, Record<string, string>>
}

const DEFAULT_CONFIG: SearchConfig = {
  customEngines: [],
  proxies: [],
  rateLimits: {},
  extraHeaders: {},
}

let loadedConfig: SearchConfig | null = null

function findConfigFile(): string | null {
  const envPath = process.env.SEARCH_CONFIG_PATH
  if (envPath && existsSync(envPath)) return envPath

  const candidates = [
    path.join(process.cwd(), 'mcp-search-config.json'),
    path.join(process.cwd(), 'mcp-search-config.jsonc'),
  ]
  for (const c of candidates) {
    if (existsSync(c)) return c
  }
  return null
}

function stripJsonc(text: string): string {
  const lines = text.split('\n')
  const out: string[] = []
  for (const line of lines) {
    let inString = false
    let stringChar = ''
    let commentStart = -1
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (inString) {
        if (ch === '\\') { i++; continue }
        if (ch === stringChar) inString = false
        continue
      }
      if (ch === '"' || ch === "'") {
        inString = true
        stringChar = ch
        continue
      }
      if (ch === '/' && line[i + 1] === '/') {
        commentStart = i
        break
      }
    }
    if (commentStart >= 0) {
      out.push(line.substring(0, commentStart))
    } else {
      out.push(line)
    }
  }
  let result = out.join('\n')
  // strip block comments (not inside strings — simplified)
  result = result.replace(/\/\*[\s\S]*?\*\//g, '')
  // strip trailing commas before ] or }
  result = result.replace(/,(\s*[\]}])/g, '$1')
  return result
}

export function loadConfig(): SearchConfig {
  if (loadedConfig) return loadedConfig

  const configPath = findConfigFile()
  if (!configPath) {
    loadedConfig = DEFAULT_CONFIG
    return loadedConfig
  }

  try {
    const raw = readFileSync(configPath, 'utf-8')
    const isJsonc = configPath.endsWith('.jsonc')
    const json = isJsonc ? stripJsonc(raw) : raw
    const parsed = JSON.parse(json)

    const customEngines: CustomEngineDef[] = []
    if (Array.isArray(parsed.customEngines)) {
      for (const e of parsed.customEngines) {
        if (e.name && e.searchUrl && e.selectors?.item && e.selectors?.title && e.selectors?.url) {
          customEngines.push({
            name: String(e.name).toLowerCase().replace(/[^a-z0-9_-]/g, ''),
            displayName: e.displayName || e.name,
            searchUrl: e.searchUrl,
            selectors: {
              item: e.selectors.item,
              title: e.selectors.title,
              url: e.selectors.url,
              description: e.selectors.description || '',
              nextPage: e.selectors.nextPage,
            },
            headers: e.headers || {},
            referer: e.referer || '',
            pageParam: e.pageParam || 'page',
            startPage: e.startPage || 1,
          })
        }
      }
    }

    loadedConfig = {
      customEngines,
      proxies: Array.isArray(parsed.proxies) ? parsed.proxies.filter((p: unknown) => typeof p === 'string') : [],
      rateLimits: typeof parsed.rateLimits === 'object' && parsed.rateLimits ? parsed.rateLimits : {},
      extraHeaders: typeof parsed.extraHeaders === 'object' && parsed.extraHeaders ? parsed.extraHeaders : {},
    }

    return loadedConfig
  } catch (err) {
    console.error(`[config] Failed to load config from ${configPath}:`, err)
    loadedConfig = DEFAULT_CONFIG
    return loadedConfig
  }
}

export function getCustomEngines(): CustomEngineDef[] {
  return loadConfig().customEngines
}

export function getProxies(): string[] {
  return loadConfig().proxies
}

export function getRateLimit(domain: string): RateLimitDef | undefined {
  return loadConfig().rateLimits[domain]
}

export function getExtraHeaders(domain: string): Record<string, string> {
  return loadConfig().extraHeaders[domain] || {}
}
