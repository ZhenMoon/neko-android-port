# MCP Search Server

[中文文档](./README.md) | English

<div align="center">

Local multi-engine aggregated search MCP server — **9+ engines parallel** + **fuzzy dedup** + **page fetch** + **deep research** + **custom profiles** + **custom engines**.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen)](package.json)

Compatible with **Cursor** · **Claude Desktop** · **Continue.dev** · **Windsurf** · **Trae**

**Privacy-first** · **Zero API cost** · **Fully open source** · **On-premises deployable**

</div>

---

## Table of Contents

- [Features](#features)
- [Search Engines](#search-engines)
- [Quick Start](#quick-start)
- [Client Configuration](#client-configuration)
- [Environment Variables](#environment-variables)
- [Configuration File](#configuration-file)
- [Available Tools](#available-tools)
- [Project Structure](#project-structure)
- [Usage Examples](#usage-examples)

---

## Features

- **Multi-engine Parallel** — Queries 8+ engines simultaneously; `Promise.allSettled` ensures single engine failure doesn't affect overall result
- **Deep Research** — `research` does search → fetch → per-page summary in one step
- **Composite Tools** — `search_and_fetch` fetches pages alongside search results
- **Search Sessions** — Persistent results, `refine` for secondary filtering (engine/keyword/domain/pagination)
- **Search Profiles** — 6 presets (general / tech / chinese / code / fast / deep) + env var customization
- **Result Aggregation** — Cross-engine deduplication (URL + Jaccard title similarity), relevance scoring, engine-balanced output
- **Structured Output** — Every result includes `score`, `publishedDate`, `domain`; JSON appended to output
- **Query Expansion** — Automatic synonym expansion when results are scarce, improving recall
- **Spam Filtering** — Removes ads, navigation keywords, tracking parameters/domains, short descriptions, error pages
- **Page Fetching** — Mozilla Readability content extraction, auto-removes duplicates, copyright notices, tail recommendations
- **Disk Cache** — 5-minute TTL, sub-second response for repeated queries
- **Circuit Breaker** — Automatic 30s cooldown after consecutive engine failures, full reset on success
- **Custom Search Engines** — Add any HTML-based search engine via JSON config (CSS selector driven)
- **Anti-scraping** — 8 browser fingerprint profiles, per-domain rate limiting, custom headers, proxy support
- **Browser Stealth** — Hides `navigator.webdriver`, random UA/viewport, Chrome stealth launch flags
- **Headless Browser** — Set `HEADLESS_BROWSER=true` to enable Puppeteer; Zhihu engine uses direct search, bypassing 403
- **MCP Protocol** — Standard stdio transport, works with Cursor / Claude Desktop out of the box
- **Privacy-first** — Fully local, search history never leaves your machine
- **Zero API Cost** — Uses free search engines directly, no paid API required

---

## Search Engines

| Engine | Type | Notes |
|--------|------|-------|
| `bing` | General | Microsoft Bing, good for Chinese queries |
| `baidu` | General | Baidu Search (cookie warm-up before querying) |
| `360` | General | 360 Search (so.com), accessible from China |
| `sogou` | General | Sogou Search (aggressive anti-scraping) |
| `duckduckgo` | General | DuckDuckGo (may be blocked in China) |
| `brave` | General | Brave Search (supports API key) |
| `github` | Code | GitHub repository search |
| `zhihu` | Content | Zhihu Q&A (browser direct / Bing `site:` fallback) |
| `csdn` | Tech | CSDN blog search (API-based, no browser needed) |
| custom | General | Add any engine via `mcp-search-config.json` |

Default engines: `bing` `baidu` `360` `github` `zhihu` `csdn`

> For users in China: sogou / duckduckgo / brave may be unreliable. Use `SEARCH_DISABLED_ENGINES` to disable them.

---

## Quick Start

### Local

```bash
git clone https://github.com/ZhenMoon/mcp-search-server.git
cd mcp-search-server
npm install
npm run build
```

### Docker (one-click deploy)

```bash
docker compose build
docker compose run --rm mcp-search
```

Client config (Docker):

```json
{
  "mcpServers": {
    "mcp-search": {
      "command": "docker",
      "args": ["compose", "run", "--rm", "mcp-search"]
    }
  }
}
```

Custom config via `config/` directory:

```bash
mkdir config
cp mcp-search-config.example.jsonc config/mcp-search-config.json
# edit config/mcp-search-config.json then:
docker compose run --rm mcp-search
```

Environment variables can be set via `.env` file or `docker-compose.yml`. All [env vars](#environment-variables) are supported.

---

## Client Configuration

Add the following entry to your MCP client config (replace `<path>` with your actual path):

```json
{
  "mcpServers": {
    "mcp-search": {
      "command": "node",
      "args": ["<path>/mcp-search-server/build/index.js"]
    }
  }
}
```

**Config file locations:**

| Client | Config Path |
|--------|-------------|
| Cursor | `~/.cursor/mcp.json` |
| Claude Desktop | `~/.claude/settings.json` |
| Continue.dev | `~/.continue/config.json` |
| Windsurf / Trae | Add the same `command`/`args` to MCP settings |

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SEARCH_ENGINES` | Comma-separated list of engines to enable | `bing,baidu,360` |
| `SEARCH_DISABLED_ENGINES` | Comma-separated list of engines to disable | `duckduckgo,brave,sogou` |
| `SEARCH_CUSTOM_ENGINES` | Custom search profile engine list | `bing,github` |
| `SEARCH_CONFIG_PATH` | Path to configuration file | `/path/to/mcp-search-config.json` |
| `SEARCH_PROXY` | HTTP proxy URL | `http://user:pass@proxy:8080` |
| `HEADLESS_BROWSER` | Enable headless browser | `true` |
| `BRAVE_API_KEY` | Brave Search API key (free 1000 req/month) | Get at https://brave.com/search/api/ |

When headless browser is enabled:
- Zhihu engine uses Puppeteer for direct search, bypassing 403
- Auto-detects and uses your local Chrome profile (shared cookies & login sessions)
- If Chrome is already running, launches a new instance with an isolated profile

```bash
SEARCH_DISABLED_ENGINES=duckduckgo,brave,sogou node build/index.js
```

---

## Configuration File

Create `mcp-search-config.json` (or `.jsonc` with comments) in the project root.

### Custom Search Engines

Add any HTML-based search engine with CSS selectors:

```json
{
  "customEngines": [
    {
      "name": "mysearch",
      "displayName": "MySearch",
      "searchUrl": "https://example.com/search?q={query}&page={page}",
      "selectors": {
        "item": ".result-item",
        "title": "h2 a",
        "url": "h2 a@href",
        "description": ".desc"
      },
      "headers": { "Referer": "https://example.com/" },
      "pageParam": "page",
      "startPage": 1
    }
  ]
}
```

Placeholders: `{query}`, `{encodedQuery}`, `{page}`. See `mcp-search-config.example.jsonc` for a full example.

### Rate Limiting

Set per-domain request intervals (milliseconds) to avoid triggering anti-scraping:

```json
{
  "rateLimits": {
    "baidu.com": { "minDelay": 1000, "maxDelay": 3000 },
    "so.com": { "minDelay": 800, "maxDelay": 2000 }
  }
}
```

### Extra Headers

Attach custom headers per domain:

```json
{
  "extraHeaders": {
    "baidu.com": {
      "Referer": "https://www.baidu.com/",
      "Connection": "keep-alive"
    }
  }
}
```

---

## Available Tools

### `search` — Multi-engine aggregated search

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `string` | **required** | Search keywords (`-keyword` exclusion, `"phrases"`, `site:`) |
| `maxResults` | `number` | `10` | Max results (1–50) |
| `engines` | `string[]` | 5 engines | Search engines (built-in + custom) |
| `timeout` | `number` | `15000` | Search timeout (ms) |
| `profile` | `string` | — | Search profile: `general`/`tech`/`chinese`/`code`/`fast`/`deep` |

Returns a `【Session ID】` with JSON structured data (`score`, `publishedDate`, `domain`) appended.

### `refine` — Refine search results

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sessionId` | `string` | **required** | Session ID from `search` |
| `engine` | `string` | — | Filter by engine (comma-separated) |
| `keyword` | `string` | — | Filter by keyword |
| `domain` | `string` | — | Filter by domain |
| `offset` | `number` | `0` | Offset |
| `limit` | `number` | `10` | Max items to return |

### `search_profiles` — List available search profiles

No parameters.

### `search_engines` — List available engines (including custom)

No parameters.

### `custom_engines` — List configured custom engines

No parameters. Shows name, URL template, and selectors.

### `fetch` — Fetch and extract page content

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `string` | **required** | Page URL |
| `timeout` | `number` | `15000` | Fetch timeout (ms) |
| `maxLength` | `number` | `8000` | Max content length to return |

### `search_and_fetch` — Search + fetch page content

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `string` | **required** | Search keywords |
| `maxResults` | `number` | `5` | Max results |
| `fetchCount` | `number` | `3` | Fetch top N pages |
| `engines` | `string[]` | 5 engines | Search engines (built-in + custom) |
| `timeout` | `number` | `15000` | Timeout (ms) |
| `profile` | `string` | — | Search profile |

### `research` — Deep research (search → fetch → report)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `string` | **required** | Research topic |
| `maxResults` | `number` | `8` | Max results |
| `fetchCount` | `number` | `3` | Deep-read top N |
| `engines` | `string[]` | 5 engines | Search engines (built-in + custom) |
| `timeout` | `number` | `20000` | Timeout (ms) |

### `analyze` — Search result analysis (compare / pro-con / overview)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `string` | **required** | Topic or question to analyze |
| `mode` | `string` | `综合` | Analysis mode: `对比`(compare)/`综合`(overview)/`正反面`(pro-con) |
| `engines` | `string[]` | 5 engines | Search engines (built-in + custom) |
| `timeout` | `number` | `20000` | Search timeout (ms) |

---

## Project Structure

```
mcp-search-server/
├── src/
│   ├── index.ts              # MCP server entry point (9 tools)
│   ├── types.ts              # Type definitions
│   ├── aggregator.ts         # Multi-engine aggregation, dedup, scoring, metadata
│   ├── config.ts             # Config file loader (JSON/JSONC)
│   ├── customEngine.ts       # Config-driven generic search engine
│   ├── metadata.ts           # Date/domain extraction, JSON formatting
│   ├── browser.ts            # Headless browser manager + stealth
│   ├── cache.ts              # Disk cache (TTL 5min)
│   ├── circuitBreaker.ts     # Engine circuit breaker
│   ├── dedupContent.ts       # Page content deduplication
│   ├── filter.ts             # Spam filtering
│   ├── queryExpander.ts      # Synonym query expansion
│   ├── queryAdapter.ts       # Engine-specific query adaptation
│   ├── fetcher.ts            # Page fetching (Readability + rotating UA)
│   ├── scraper.ts            # Anti-scraping utilities (fingerprint rotation, proxy, rate limit)
│   ├── searchContext.ts      # Search session management + profiles
│   ├── session.ts            # Cookie session management + disk persistence
│   └── engines/
│       ├── bing.ts           # Bing
│       ├── baidu.ts          # Baidu
│       ├── 360.ts            # 360 Search
│       ├── sogou.ts          # Sogou
│       ├── duckduckgo.ts     # DuckDuckGo
│       ├── brave.ts          # Brave Search
│       ├── github.ts         # GitHub
│       └── zhihu.ts          # Zhihu (browser direct / Bing site: fallback)
├── mcp-search-config.example.json   # Config example (plain JSON)
├── mcp-search-config.example.jsonc  # Config example (JSONC with comments)
├── package.json
├── tsconfig.json
├── README.md                 # Chinese documentation
└── README.en.md              # English
```

---

## Usage Examples

```text
Search + fetch workflow:
  search("Rust language tutorial", maxResults: 5)
  fetch("https://doc.rust-lang.org/book/")

Specify engines (including custom):
  search(engines: ["bing", "mysearch"], query: "Vue.js tutorial")

Deep research:
  research("Rust vs Go performance", maxResults: 8, fetchCount: 3)

Result analysis:
  analyze("quantum computing breakthroughs", mode: "正反面", engines: ["bing", "zhihu"])

Environment variable:
  SEARCH_ENGINES=bing,github node build/index.js

Config file:
  export SEARCH_CONFIG_PATH=/etc/mcp-search-config.json
```

---

## License

MIT
