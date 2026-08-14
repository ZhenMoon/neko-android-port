import type { SearchResult } from './types.js'

const AD_KEYWORDS = [
  '广告', 'ad', 'sponsored', '推广', 'promoted',
  'recommended', '推荐', '智能分身', '视频大全',
  '代售', '正版', '安全交易', '游戏交易',
  '免费下载', '官方正版', '最新版',
]

const NAV_KEYWORDS = [
  '登录', '注册', 'sign in', 'sign up', 'login', 'register',
  '首页', 'home', '联系我们', 'contact us',
  '关于我们', 'about us', '隐私政策', 'privacy policy',
  '服务条款', 'terms of service', 'cookie',
]

const BAD_TITLE_PATTERNS = [
  /^\d{1,3}\s*(错误|error|warning|notice|page not found|404)/i,
  /^(just a moment|please wait|验证码|安全验证)/i,
  /^403|^404|^500|^502|^503/,
  /视频大全/,
  /智能分身/,
  /免费阅读/,
  /最新章节/,
  /百度文库/,
  /安全.*交易/,
  /代售/,
]

const TRACKING_DOMAINS = [
  'doubleclick.net', 'googlesyndication.com', 'googleadservices.com',
  'google-analytics.com', 'facebook.com/tr', 'amazon-adsystem.com',
]

const STATIC_PATTERNS = [
  /baike\.baidu\.com/i,
  /zh\.wikipedia\.org/i,
  /encyclopedia/i,
  /词典/i,
  /辞海/i,
  /百科/i,
]

const NEWS_KEYWORDS = ['新闻', '最新', '今天', '报道', '快讯', '实时', '更新', '突发', '直播', '时讯']

const SHORT_DESC_THRESHOLD = 5

export function isFreshnessQuery(query: string): boolean {
  const lower = query.toLowerCase()
  return NEWS_KEYWORDS.some(k => lower.includes(k))
}

export function isStaticPage(url: string): boolean {
  return STATIC_PATTERNS.some(p => p.test(url))
}

export function isLowQuality(result: SearchResult): boolean {
  const title = result.title.trim()
  const desc = result.description.trim()

  if (!title && !desc) return true

  if (title.length < 2) return true

  if (BAD_TITLE_PATTERNS.some(p => p.test(title))) return true

  if (TRACKING_DOMAINS.some(d => result.url.toLowerCase().includes(d))) return true

  try {
    const hostname = new URL(result.url).hostname.toLowerCase()
    if (TRACKING_DOMAINS.some(d => hostname === d || hostname.endsWith('.' + d))) return true
  } catch {
    // invalid URL — filter it out
    return true
  }

  const descLower = desc.toLowerCase()
  const titleLower = title.toLowerCase()
  if (AD_KEYWORDS.some(k => titleLower.includes(k) || descLower.includes(k))) return true

  if (NAV_KEYWORDS.some(k => titleLower === k || descLower === k)) return true

  const descWordCount = desc.split(/[\s,，。、；:：]+/).filter(w => w.length > 0).length
  const hasChinese = /[\u4e00-\u9fff]/.test(desc)
  if (!hasChinese && descWordCount < SHORT_DESC_THRESHOLD) return true

  return false
}

export function trimResults(results: SearchResult[]): SearchResult[] {
  return results.filter(r => !isLowQuality(r))
}
