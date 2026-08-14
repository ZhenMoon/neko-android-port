function normalizeLine(s: string): string {
  return s.replace(/[^\w\u4e00-\u9fff]/g, '').toLowerCase().trim()
}

function isRepeatLine(line: string, seen: Set<string>): boolean {
  const key = normalizeLine(line)
  if (!key || key.length < 8) return false
  if (seen.has(key)) return true
  seen.add(key)
  return false
}

function countWords(s: string): number {
  const cleaned = s.replace(/[\u4e00-\u9fff]/g, '  ').trim()
  return cleaned.split(/\s+/).filter(Boolean).length
}

const BOILERPLATE_PATTERNS = [
  /^copyright/i,
  /^all rights reserved/i,
  /^版权所有/i,
  /^转载请注明/i,
  /^本文链接/i,
  /^来源：/i,
  /^作者：/i,
  /^编辑：/i,
  /^责编：/i,
  /^声明：/i,
  /^免责声明/i,
  /^关注我们/i,
  /^扫码关注/i,
  /^欢迎关注/i,
  /^点击关注/i,
  /^分享到/i,
  /^推荐阅读/i,
  /^延伸阅读/i,
  /^相关阅读/i,
  /^推荐文章/i,
  /^猜你喜欢/i,
  /^你可能/i,
  /^广告\s*$/i,
  /^推广\s*$/i,
  /^赞助/i,
]

const TAIL_KEYWORDS = [
  '推荐阅读', '延伸阅读', '相关文章', '相关阅读', '猜你喜欢',
  '上一篇', '下一篇', '相关推荐', '编辑推荐',
  '免责声明', '版权声明', '版权保护',
  '关注我们', '联系我们', '关于我们',
  '分享到', '赞赏', '点赞', '在看',
  '欢迎在评论区', '欢迎在下方',
  'topic', 'related', 'recommended',
  'comments', 'comment',
]

function isBoilerplate(line: string): boolean {
  const trimmed = line.trim()
  if (!trimmed) return false
  if (BOILERPLATE_PATTERNS.some(p => p.test(trimmed))) return true
  return false
}

function isTailMarker(line: string): boolean {
  const trimmed = line.trim().toLowerCase()
  return TAIL_KEYWORDS.some(k => trimmed.startsWith(k))
}

function hasTooManyRepeatedChars(text: string): boolean {
  const runs = text.match(/(.)\1{10,}/g)
  return runs !== null
}

export function deduplicateContent(text: string): string {
  if (!text) return text

  if (hasTooManyRepeatedChars(text)) return ''

  const lines = text.split('\n')
  const seen = new Set<string>()
  const result: string[] = []
  let foundTail = false

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      if (result.length > 0 && result[result.length - 1] !== '') {
        result.push('')
      }
      continue
    }

    if (foundTail) continue

    if (isBoilerplate(trimmed)) {
      if (i > lines.length * 0.5) continue
    }

    if (isTailMarker(trimmed)) {
      const remainingText = lines.slice(i).join(' ').trim()
      if (countWords(remainingText) < 30) {
        foundTail = true
        continue
      }
    }

    if (isRepeatLine(trimmed, seen)) continue

    result.push(trimmed)
  }

  while (result.length > 0 && result[result.length - 1] === '') {
    result.pop()
  }

  return result.join('\n')
}
