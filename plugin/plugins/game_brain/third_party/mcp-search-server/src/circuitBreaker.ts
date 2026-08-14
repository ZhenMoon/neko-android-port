// Circuit breaker: track recent failures and cooldown
// Reset aggressively: any success clears all history

interface BreakerState {
  failures: number
  lastFailure: number
}

const state = new Map<string, BreakerState>()
const MAX_FAILURES = 10
const COOLDOWN_MS = 30 * 1000

export function isEngineAvailable(name: string): boolean {
  const s = state.get(name)
  if (!s) return true
  if (s.failures < MAX_FAILURES) return true
  if (Date.now() - s.lastFailure > COOLDOWN_MS) {
    s.failures = 0
    return true
  }
  return false
}

export function recordFailure(name: string): void {
  const s = state.get(name) || { failures: 0, lastFailure: 0 }
  s.failures++
  s.lastFailure = Date.now()
  state.set(name, s)
}

export function recordSuccess(name: string): void {
  const s = state.get(name)
  if (s) {
    s.failures = 0
  }
}

export function resetAll(): void {
  state.clear()
}

export function getBreakerStatus(): string[] {
  if (state.size === 0) return ['(无记录)']
  const lines: string[] = []
  for (const [name, s] of state) {
    const blocked = s.failures >= MAX_FAILURES
    const remaining = blocked
      ? Math.max(0, Math.ceil((COOLDOWN_MS - (Date.now() - s.lastFailure)) / 1000))
      : 0
    lines.push(`${name}: ${s.failures}/${MAX_FAILURES}${blocked ? ` (冷却${remaining}s)` : ''}`)
  }
  return lines
}
