import { readFile, writeFile, mkdir } from 'fs/promises'
import { existsSync } from 'fs'

interface CookieEntry {
  value: string
  expiresAt: number
  createdAt: number
}

const TTL = 30 * 60 * 1000
const DUMP_PATH = '.opencode/cookies.json'

// Session rotation: cookies older than ROTATION_INTERVAL are cleared and re-fetched
const ROTATION_INTERVAL = (() => {
  const env = process.env.SESSION_ROTATION
  if (env) {
    const ms = parseInt(env, 10)
    if (ms > 0) return ms
  }
  return 5 * 60 * 1000 // 5 minutes default
})()

const cookies = new Map<string, CookieEntry>()
let loaded = false

async function loadDump(): Promise<void> {
  if (loaded) return
  loaded = true
  try {
    if (!existsSync(DUMP_PATH)) return
    const raw = await readFile(DUMP_PATH, 'utf-8')
    const data = JSON.parse(raw) as Record<string, [string, number, number]>
    for (const [domain, [value, expiresAt, createdAt]] of Object.entries(data)) {
      if (Date.now() < expiresAt) cookies.set(domain, { value, expiresAt, createdAt })
    }
  } catch { }
}

async function dumpCookies(): Promise<void> {
  try {
    const obj: Record<string, [string, number, number]> = {}
    for (const [domain, entry] of cookies) {
      if (Date.now() < entry.expiresAt) obj[domain] = [entry.value, entry.expiresAt, entry.createdAt]
    }
    if (!existsSync('.opencode')) await mkdir('.opencode', { recursive: true })
    await writeFile(DUMP_PATH, JSON.stringify(obj))
  } catch { }
}

export function getCookie(domain: string): string | null {
  const entry = cookies.get(domain)
  if (!entry) return null
  if (Date.now() > entry.expiresAt) {
    cookies.delete(domain)
    return null
  }
  // Check session rotation: if cookie is older than rotation interval, treat as expired
  if (Date.now() - entry.createdAt > ROTATION_INTERVAL) {
    cookies.delete(domain)
    dumpCookies()
    return null
  }
  return entry.value
}

export function setCookie(domain: string, value: string): void {
  cookies.set(domain, { value, expiresAt: Date.now() + TTL, createdAt: Date.now() })
  dumpCookies()
}

export function clearCookies(domain?: string): void {
  if (domain) cookies.delete(domain)
  else cookies.clear()
  dumpCookies()
}

export async function warmUp(
  url: string,
  domain: string,
  headers: Record<string, string>,
  signal?: AbortSignal,
): Promise<void> {
  await loadDump()
  // If a valid cookie exists and hasn't exceeded rotation interval, skip warm-up
  const existing = cookies.get(domain)
  if (existing && Date.now() < existing.expiresAt && Date.now() - existing.createdAt <= ROTATION_INTERVAL) return

  // Stale or missing — clear and re-fetch
  if (existing) cookies.delete(domain)

  try {
    const res = await fetch(url, { headers, signal, redirect: 'manual' })
    const allCookies = res.headers.getSetCookie()
    if (allCookies.length > 0) {
      const pairs: string[] = []
      for (const raw of allCookies) {
        const pair = raw.split(';')[0]?.trim()
        if (pair && pair.includes('=')) pairs.push(pair)
      }
      if (pairs.length > 0) setCookie(domain, pairs.join('; '))
    }
  } catch { }
}
