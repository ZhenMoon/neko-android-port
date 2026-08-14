#!/usr/bin/env node
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'
import { aggregateWithReport, listAllEngines } from './aggregator.js'
import { fetchPage } from './fetcher.js'
import { adaptQuery, getQueryInfo } from './queryAdapter.js'
import { saveResults, refineResults, SEARCH_PROFILES } from './searchContext.js'
import { formatResultJson, extractDomain, extractDate } from './metadata.js'
import { loadConfig, getCustomEngines } from './config.js'

const ALL_ENGINES = ['duckduckgo', 'bing', 'sogou', 'baidu', 'brave', 'github', 'zhihu', '360', 'csdn'] as const

type BuiltinEngine = typeof ALL_ENGINES[number]

function defaultEngines(): BuiltinEngine[] {
  const env = process.env.SEARCH_ENGINES
  if (env) {
    const parsed = env.split(',').map(s => s.trim()).filter((s): s is BuiltinEngine =>
      ALL_ENGINES.includes(s as any)
    )
    if (parsed.length > 0) return parsed
  }
  const disabled = process.env.SEARCH_DISABLED_ENGINES
  if (disabled) {
    const set = new Set(disabled.split(',').map(s => s.trim()))
    return ALL_ENGINES.filter(e => !set.has(e))
  }
  return ['bing', 'baidu', '360', 'github', 'zhihu', 'csdn']
}

const ALL_ENGINE_NAMES = listAllEngines()

const server = new McpServer({
  name: 'mcp-search-server',
  version: '1.2.0',
  description: '多引擎聚合搜索本地 MCP 服务器 - 9 引擎并行，模糊去重、正文提取、深度研究，自定义场景，自定义搜索引擎，反爬增强，可选无头浏览器',
})

server.tool(
  'search',
  {
    query: z.string().describe('搜索关键词（支持 -keyword 排除、"短语搜索"、site:域名）'),
    maxResults: z.number().int().min(1).max(50).default(10).describe('最大返回结果数'),
    engines: z
      .array(z.string())
      .default(defaultEngines())
      .describe('搜索引擎列表（内置 + 自定义）'),
    timeout: z.number().int().min(3000).max(60000).default(15000).describe('搜索超时(毫秒)'),
    profile: z.string().optional().describe('搜索场景: general/tech/chinese/code/fast/deep'),
  },
  async ({ query, maxResults, engines, timeout, profile }) => {
    let activeEngines = engines
    if (profile && SEARCH_PROFILES[profile]) {
      activeEngines = SEARCH_PROFILES[profile].engines
    }
    const { results, reports } = await aggregateWithReport({ query, maxResults, engines: activeEngines, timeout })
    if (results.length === 0) {
      const statusLine = reports.map(r => `${r.engine}=${r.status}${r.count > 0 ? `(${r.count})` : ''}`).join(' ')
      return { content: [{ type: 'text', text: `未找到结果\n引擎状态: ${statusLine}` }] }
    }

    const sessionId = saveResults(query, results)
    const info = getQueryInfo(query)
    const header: string[] = []
    if (info.phrases.length > 0) header.push(`短语: ${info.phrases.join(', ')}`)
    if (info.exclusions.length > 0) header.push(`排除: ${info.exclusions.join(', ')}`)
    if (info.siteFilter) header.push(`限定站点: ${info.siteFilter}`)
    if (profile && SEARCH_PROFILES[profile]) header.push(`场景: ${SEARCH_PROFILES[profile].label}`)

    const statusLine = reports.map(r =>
      r.status === 'ok' ? `${r.engine}(${r.count})` :
      r.status === 'empty' ? `${r.engine}(空)` :
      `${r.engine}(失败)`
    ).join(' ')

    const body = formatResults(results)
    const jsonContent = formatResultJson(results)
    return {
      content: [{
        type: 'text',
        text: `【会话ID】${sessionId}\n${header.length > 0 ? `【查询解析】${header.join(' | ')}\n` : ''}【引擎状态】${statusLine}\n\n${body}\n\n---\n${jsonContent}`,
      }],
    }
  },
)

server.tool(
  'refine',
  {
    sessionId: z.string().describe('search 返回的会话 ID'),
    engine: z.string().optional().describe('按引擎过滤，逗号分隔（如 bing,baidu）'),
    keyword: z.string().optional().describe('关键词过滤'),
    domain: z.string().optional().describe('域名过滤（如 zhihu.com）'),
    offset: z.number().int().min(0).default(0).describe('偏移量'),
    limit: z.number().int().min(1).max(50).default(10).describe('返回数量'),
  },
  async ({ sessionId, engine, keyword, domain, offset, limit }) => {
    const { results, total } = refineResults(sessionId, { engine, keyword, domain, offset, limit })
    if (results.length === 0) {
      return { content: [{ type: 'text', text: `无匹配结果（共 ${total} 条原始结果）` }] }
    }
    return {
      content: [{
        type: 'text',
        text: `【过滤结果】${results.length}/${total} 条\n\n${formatResults(results)}`,
      }],
    }
  },
)

server.tool(
  'search_profiles',
  {},
  async () => {
    const lines = Object.entries(SEARCH_PROFILES).map(([id, p]) =>
      `  ${id}: ${p.label} — ${p.description} (引擎: ${p.engines.join(', ')})`
    )
    return { content: [{ type: 'text', text: `可用搜索场景:\n${lines.join('\n')}` }] }
  },
)

server.tool(
  'analyze',
  {
    query: z.string().describe('要分析的主题或问题'),
    mode: z.enum(['对比', '综合', '正反面']).default('综合').describe('分析模式'),
    engines: z
      .array(z.string())
      .default(defaultEngines())
      .describe('搜索引擎列表（内置 + 自定义）'),
    timeout: z.number().int().min(5000).max(60000).default(20000).describe('搜索超时(毫秒)'),
  },
  async ({ query, mode, engines, timeout }) => {
    const { results, reports } = await aggregateWithReport({ query, maxResults: 15, engines, timeout })
    if (results.length === 0) {
      const statusLine = reports.map(r => `${r.engine}=${r.status}`).join(' ')
      return { content: [{ type: 'text', text: `无法获取分析素材\n引擎状态: ${statusLine}` }] }
    }

    const byEngine = new Map<string, typeof results>()
    for (const r of results) {
      const list = byEngine.get(r.engine) || []
      list.push(r)
      byEngine.set(r.engine, list)
    }

    const lines: string[] = [
      `【分析主题】${query}`,
      `【分析模式】${mode}`,
      `【引擎概况】${reports.filter(r => r.status === 'ok').map(r => `${r.engine} ${r.count}条`).join(' | ')}`,
      '',
    ]

    if (mode === '对比') {
      for (const [engine, items] of byEngine) {
        lines.push(`── ${engine} ──`)
        items.slice(0, 5).forEach((r, i) => {
          lines.push(`  ${i + 1}. ${r.title}`)
          if (r.description) lines.push(`     ${r.description.substring(0, 120)}`)
        })
        lines.push('')
      }
    } else if (mode === '正反面') {
      const pros: typeof results = []
      const cons: typeof results = []
      const neutral: typeof results = []
      const pos = ['优点', '优势', '利好', '发展', '创新', '进步', '突破', '增长', '推荐']
      const neg = ['缺点', '风险', '问题', '争议', '批评', '下滑', '衰退', '危机', '警惕']

      for (const r of results) {
        const text = (r.title + ' ' + r.description).toLowerCase()
        const hasPos = pos.some(k => text.includes(k))
        const hasNeg = neg.some(k => text.includes(k))
        if (hasPos && !hasNeg) pros.push(r)
        else if (hasNeg && !hasPos) cons.push(r)
        else neutral.push(r)
      }

      lines.push('【正面观点】')
      pros.slice(0, 5).forEach((r, i) => lines.push(`  ${i + 1}. [${r.engine}] ${r.title}`))
      lines.push('')
      lines.push('【负面/争议观点】')
      cons.slice(0, 5).forEach((r, i) => lines.push(`  ${i + 1}. [${r.engine}] ${r.title}`))
      lines.push('')
      lines.push('【中性/其他】')
      neutral.slice(0, 3).forEach((r, i) => lines.push(`  ${i + 1}. [${r.engine}] ${r.title}`))
    } else {
      lines.push('【多引擎综合结果】')
      results.slice(0, 12).forEach((r, i) => {
        lines.push(`  ${i + 1}. [${r.engine}] ${r.title}`)
        if (r.description) lines.push(`     ${r.description.substring(0, 120)}`)
      })
    }

    lines.push('', `--- 共 ${results.length} 条结果，来自 ${byEngine.size} 个引擎 ---`)
    return { content: [{ type: 'text', text: lines.join('\n') }] }
  },
)

server.tool(
  'search_engines',
  {},
  async () => {
    return {
      content: [{ type: 'text', text: ALL_ENGINE_NAMES.join('\n') }],
    }
  },
)

server.tool(
  'custom_engines',
  {},
  async () => {
    const defs = getCustomEngines()
    if (defs.length === 0) {
      return { content: [{ type: 'text', text: '尚未配置自定义搜索引擎。\n在项目目录下创建 mcp-search-config.json 并添加 customEngines 数组。' }] }
    }
    const lines = defs.map(d =>
      `  ${d.name}: ${d.displayName || d.name}\n    URL: ${d.searchUrl}\n    选择器: item=${d.selectors.item}, title=${d.selectors.title}, url=${d.selectors.url}`
    )
    return { content: [{ type: 'text', text: `自定义搜索引擎 (${defs.length} 个):\n\n${lines.join('\n')}` }] }
  },
)

server.tool(
  'search_and_fetch',
  {
    query: z.string().describe('搜索关键词'),
    maxResults: z.number().int().min(1).max(20).default(5).describe('搜索结果数'),
    fetchCount: z.number().int().min(0).max(5).default(3).describe('抓取前 N 条正文'),
    engines: z.array(z.string()).default(defaultEngines()).describe('搜索引擎列表（内置 + 自定义）'),
    timeout: z.number().int().min(3000).max(60000).default(15000).describe('超时(毫秒)'),
    profile: z.string().optional().describe('搜索场景'),
  },
  async ({ query, maxResults, fetchCount, engines, timeout, profile }) => {
    let activeEngines = engines
    if (profile && SEARCH_PROFILES[profile]) {
      activeEngines = SEARCH_PROFILES[profile].engines
    }
    const { results, reports } = await aggregateWithReport({ query, maxResults, engines: activeEngines, timeout })
    if (results.length === 0) {
      return { content: [{ type: 'text', text: `未找到结果\n引擎状态: ${reports.map(r => `${r.engine}=${r.status}`).join(' ')}` }] }
    }

    const toFetch = results.slice(0, fetchCount)
    const fetched = await Promise.allSettled(toFetch.map(r => fetchPage(r.url, timeout)))

    const lines = [`【搜索结果】`]
    results.forEach((r, i) => {
      lines.push(`\n${i + 1}. ${r.title}`)
      lines.push(`   URL: ${r.url}`)
      lines.push(`   来源: ${r.engine}`)
      if (i < fetchCount) {
        const f = fetched[i]
        if (f.status === 'fulfilled' && f.value.content) {
          lines.push(`   正文: ${f.value.content.substring(0, 300)}...`)
        } else {
          lines.push(`   正文: (抓取失败)`)
        }
      }
    })
    lines.push(`\n---\n${formatResultJson(results)}`)
    return { content: [{ type: 'text', text: lines.join('\n') }] }
  },
)

server.tool(
  'research',
  {
    query: z.string().describe('研究主题'),
    maxResults: z.number().int().min(3).max(20).default(8).describe('搜索结果数'),
    fetchCount: z.number().int().min(1).max(5).default(3).describe('深入阅读前 N 条'),
    engines: z.array(z.enum(ALL_ENGINES)).default(defaultEngines()).describe('搜索引擎列表'),
    timeout: z.number().int().min(5000).max(60000).default(20000).describe('超时(毫秒)'),
  },
  async ({ query, maxResults, fetchCount, engines, timeout }) => {
    const { results, reports } = await aggregateWithReport({ query, maxResults, engines, timeout })
    if (results.length === 0) {
      return { content: [{ type: 'text', text: `未找到素材\n引擎状态: ${reports.map(r => `${r.engine}=${r.status}`).join(' ')}` }] }
    }

    const toFetch = results.slice(0, fetchCount)
    const fetched = await Promise.allSettled(toFetch.map(r => fetchPage(r.url, timeout)))

    const lines: string[] = [`【深度阅读】${query}`, `来源: ${results.length} 条结果 / ${fetchCount} 篇详细阅读`, '']

    for (let i = 0; i < toFetch.length; i++) {
      const r = toFetch[i]
      const f = fetched[i]
      lines.push(`--- 第 ${i + 1} 篇: ${r.title} ---`)
      lines.push(`URL: ${r.url}`)
      if (f.status === 'fulfilled' && f.value.content) {
        lines.push(`正文: ${f.value.content.substring(0, 500)}`)
      } else {
        lines.push('(抓取失败)')
      }
      lines.push('')
    }

    lines.push(`--- ${reports.filter(r => r.status === 'ok').map(r => `${r.engine} ${r.count}条`).join(' | ')} ---`)
    return { content: [{ type: 'text', text: lines.join('\n') }] }
  },
)

server.tool(
  'fetch',
  {
    url: z.string().url().describe('要抓取的网页 URL'),
    timeout: z.number().int().min(3000).max(60000).default(15000).describe('抓取超时(毫秒)'),
    maxLength: z.number().int().min(500).max(100000).default(8000).describe('返回内容最大长度'),
  },
  async ({ url, timeout, maxLength }) => {
    const result = await fetchPage(url, timeout)
    if (!result.content) {
      return { content: [{ type: 'text', text: '无法获取页面内容' }] }
    }
    const truncated = result.content.length > maxLength
      ? result.content.substring(0, maxLength) + `\n\n...（内容过长，截取前 ${maxLength} 字符）`
      : result.content
    return {
      content: [{
        type: 'text',
        text: `标题: ${result.title}\nURL: ${result.url}\n字数: ${result.length}\n\n${truncated}`,
      }],
    }
  },
)

function truncateAtSentence(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text
  const cut = text.substring(0, maxLen)
  const sentenceEnd = Math.max(
    cut.lastIndexOf('。'),
    cut.lastIndexOf('.'),
    cut.lastIndexOf('！'),
    cut.lastIndexOf('？'),
    cut.lastIndexOf('\n'),
  )
  if (sentenceEnd > maxLen * 0.6) return text.substring(0, sentenceEnd + 1)
  return cut + '...'
}

function formatResults(results: Array<{ title: string; url: string; description: string; engine: string }>): string {
  return results
    .map((r, i) => {
      const lines = [`${i + 1}. ${r.title}`]
      lines.push(`   URL: ${r.url}`)
      if (r.description) lines.push(`   摘要: ${truncateAtSentence(r.description, 150)}`)
      lines.push(`   来源: ${r.engine}`)
      return lines.join('\n')
    })
    .join('\n\n')
}

async function main() {
  const transport = new StdioServerTransport()
  await server.connect(transport)
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
