# MCP Search Server

[English](./README.en.md) | 中文

<div align="center">

多引擎聚合搜索本地 MCP 服务器 — **9 引擎并行** + **模糊去重** + **网页正文提取** + **深度研究** + **自定义场景** + **自定义搜索引擎**。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen)](package.json)

兼容 **Cursor** · **Claude Desktop** · **Continue.dev** · **Windsurf** · **Trae**

**隐私安全** · **零 API 费用** · **完全开源** · **可部署内网**

</div>

---

## 目录

- [功能特性](#功能特性)
- [搜索引擎](#搜索引擎)
- [快速开始](#快速开始)
- [客户端配置](#客户端配置)
- [环境变量](#环境变量)
- [配置文件](#配置文件)
- [MCP 工具](#mcp-工具)
- [项目结构](#项目结构)
- [使用示例](#使用示例)

---

## 功能特性

- **多引擎并行** — 同时调用 8+ 个搜索引擎；`Promise.allSettled` 确保单引擎失败不影响整体
- **深度研究** — `research` 一键搜索 → 抓取 → 综合结论
- **复合工具** — `search_and_fetch` 搜索同时抓取正文
- **搜索会话** — 结果持久化，`refine` 二次过滤（引擎/关键词/域名/分页）
- **搜索场景** — 6 种预设（general / tech / chinese / code / fast / deep）+ 环境变量自定义
- **结果聚合** — 模糊去重（Jaccard 标题相似度 + URL 去重）、关联性评分排序、引擎均衡输出
- **结构化输出** — 每条结果附带 `score`、`publishedDate`、`domain`，末尾嵌入 JSON
- **查询扩展** — 结果不足时自动用同义词扩展查询，提升召回率
- **无用过滤** — 剔除广告词、导航词、跟踪参数/域名、短摘要、错误页面
- **网页抓取** — 使用 Mozilla Readability 提取正文，自动删除重复段落、版权声明、尾部推荐
- **磁盘缓存** — 5 分钟 TTL，重复查询秒级响应
- **熔断保护** — 引擎连续失败后自动冷却 30 秒，成功后全额重置
- **自定义搜索引擎** — 通过 JSON 配置文件添加任意 HTML 引擎（CSS 选择器驱动）
- **反爬增强** — 8 组浏览器指纹轮换、按域名频率限制、自定义请求头、代理支持
- **浏览器隐身** — 隐藏 `navigator.webdriver`、随机 UA/视口、Chrome stealth 启动参数
- **无头浏览器** — 设置 `HEADLESS_BROWSER=true` 启用 Puppeteer，知乎直连搜索绕过 403
- **MCP 协议** — 标准 stdio 传输，Cursor / Claude Desktop 即插即用
- **隐私安全** — 全部本地运行，搜索记录不经过任何第三方服务器
- **零 API 费用** — 直接调用免费搜索引擎，无需付费 API

---

## 搜索引擎

| 引擎 | 类型 | 说明 |
|------|------|------|
| `bing` | 通用 | 微软必应，中文搜索结果较好 |
| `baidu` | 通用 | 百度搜索（Cookie 热身后调用） |
| `360` | 通用 | 360 搜索（so.com），国内可用 |
| `sogou` | 通用 | 搜狗搜索（反爬限制较严） |
| `duckduckgo` | 通用 | DuckDuckGo（国内网络可能被阻断） |
| `brave` | 通用 | Brave Search（支持 API 密钥） |
| `github` | 代码 | GitHub 仓库搜索 |
| `zhihu` | 内容 | 知乎（浏览器模式直连 / Bing `site:` 回退） |
| `csdn` | 技术 | CSDN 博客搜索（API 直连，无需浏览器） |
| 自定义 | 通用 | 通过 `mcp-search-config.json` 添加任意引擎 |

默认启用：`bing` `baidu` `360` `github` `zhihu` `csdn`

> 国内网络：sogou / duckduckgo / brave 可能失效，可用 `SEARCH_DISABLED_ENGINES` 禁用。

---
## 快速开始

### 直接运行

```bash
git clone https://github.com/ZhenMoon/mcp-search-server.git
cd mcp-search-server
npm install
npm run build
```

### Docker 一键部署

```bash
# 构建镜像
docker compose build

# 启动（前台运行，MCP stdio 模式）
docker compose run --rm mcp-search

# 或在后台运行
docker compose up -d
```

客户端配置（Docker）：

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

使用自定义配置文件：

```bash
# 创建配置目录
mkdir config
cp mcp-search-config.example.jsonc config/mcp-search-config.json

# 编辑 config/mcp-search-config.json 后启动
docker compose run --rm mcp-search
```

环境变量通过 `.env` 文件或 `docker-compose.yml` 中的 `environment` 设置。支持所有 [环境变量](#环境变量)。

---

## 客户端配置

在 MCP 客户端配置中添加以下条目（将 `<path>` 替换为实际路径）：

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

**配置文件位置：**

| 客户端 | 配置文件路径 |
|--------|-------------|
| Cursor | `~/.cursor/mcp.json` |
| Claude Desktop | `~/.claude/settings.json` |
| Continue.dev | `~/.continue/config.json` |
| Windsurf / Trae | 在 MCP 配置中添加相同 command/args |

---

## 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `SEARCH_ENGINES` | 启用指定引擎（逗号分隔） | `bing,baidu,360` |
| `SEARCH_DISABLED_ENGINES` | 禁用指定引擎 | `duckduckgo,brave,sogou` |
| `SEARCH_CUSTOM_ENGINES` | 自定义搜索场景引擎列表 | `bing,github` |
| `SEARCH_CONFIG_PATH` | 配置文件路径 | `/path/to/mcp-search-config.json` |
| `SEARCH_PROXY` | HTTP 代理 | `http://user:pass@proxy:8080` |
| `HEADLESS_BROWSER` | 启用无头浏览器 | `true` |
| `BRAVE_API_KEY` | Brave Search API 密钥 | 在 https://brave.com/search/api/ 申请 |

启用无头浏览器后：
- 知乎引擎通过 Puppeteer 直连知乎搜索，绕过 403 限制
- 自动获取本地 Chrome 的用户数据和 cookies（登录态共享），可绕过部分验证码
- 如果 Chrome 已在运行，自动使用独立 profile 启动新实例

国内网络建议：
```bash
SEARCH_DISABLED_ENGINES=duckduckgo,brave,sogou node build/index.js
```

---

## 配置文件

在项目根目录创建 `mcp-search-config.json`（或 `.jsonc` 支持注释），可实现：

### 自定义搜索引擎

添加任意支持 HTML 抓取的搜索引擎，使用 CSS 选择器提取结果：

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

占位符：`{query}`、`{encodedQuery}`、`{page}`。详细示例见 `mcp-search-config.example.jsonc`。

### 频率限制

按域名设置请求间隔（毫秒），避免触发反爬：

```json
{
  "rateLimits": {
    "baidu.com": { "minDelay": 1000, "maxDelay": 3000 },
    "so.com": { "minDelay": 800, "maxDelay": 2000 }
  }
}
```

### 额外请求头

按域名附加自定义请求头：

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

## MCP 工具

### `search` — 多引擎聚合搜索

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | `string` | **必填** | 搜索关键词（支持 `-keyword` 排除、`"短语"`、`site:`） |
| `maxResults` | `number` | `10` | 最大返回结果数（1–50） |
| `engines` | `string[]` | 默认 5 引擎 | 搜索引擎列表（内置 + 自定义） |
| `timeout` | `number` | `15000` | 搜索超时（毫秒） |
| `profile` | `string` | — | 搜索场景：`general`/`tech`/`chinese`/`code`/`fast`/`deep` |

返回结果包含 `【会话ID】` 和末尾 JSON 结构化数据（`score`、`publishedDate`、`domain`）。

### `refine` — 精炼搜索结果

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sessionId` | `string` | **必填** | `search` 返回的会话 ID |
| `engine` | `string` | — | 按引擎过滤，逗号分隔 |
| `keyword` | `string` | — | 关键词过滤 |
| `domain` | `string` | — | 域名过滤 |
| `offset` | `number` | `0` | 偏移量 |
| `limit` | `number` | `10` | 返回数量 |

### `search_profiles` — 列出可用搜索场景

无参数。

### `search_engines` — 列出可用搜索引擎（含自定义）

无参数。

### `custom_engines` — 列出已配置的自定义引擎

无参数。显示自定义引擎的名称、URL 模板和选择器。

### `fetch` — 抓取网页正文

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `url` | `string` | **必填** | 网页 URL |
| `timeout` | `number` | `15000` | 抓取超时（毫秒） |
| `maxLength` | `number` | `8000` | 返回内容最大长度 |

### `search_and_fetch` — 搜索 + 抓取正文

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | `string` | **必填** | 搜索关键词 |
| `maxResults` | `number` | `5` | 搜索结果数 |
| `fetchCount` | `number` | `3` | 抓取前 N 条正文 |
| `engines` | `string[]` | 默认 5 引擎 | 搜索引擎列表（内置 + 自定义） |
| `timeout` | `number` | `15000` | 超时(毫秒) |
| `profile` | `string` | — | 搜索场景 |

### `research` — 深度研究（搜索 → 抓取 → 综合报告）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | `string` | **必填** | 研究主题 |
| `maxResults` | `number` | `8` | 搜索结果数 |
| `fetchCount` | `number` | `3` | 深入阅读前 N 条 |
| `engines` | `string[]` | 默认 5 引擎 | 搜索引擎列表（内置 + 自定义） |
| `timeout` | `number` | `20000` | 超时(毫秒) |

### `analyze` — 搜索结果分析（对比 / 正反面 / 综合）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | `string` | **必填** | 要分析的主题或问题 |
| `mode` | `string` | `综合` | 分析模式：`对比`/`综合`/`正反面` |
| `engines` | `string[]` | 默认 5 引擎 | 搜索引擎列表（内置 + 自定义） |
| `timeout` | `number` | `20000` | 搜索超时(毫秒) |

---

## 项目结构

```
mcp-search-server/
├── src/
│   ├── index.ts              # MCP 服务器入口（9 个工具）
│   ├── types.ts              # 类型定义
│   ├── aggregator.ts         # 多引擎聚合 + 去重 + 排序 + 元数据
│   ├── config.ts             # 配置文件加载（JSON/JSONC）
│   ├── customEngine.ts       # 配置驱动的通用搜索引擎
│   ├── metadata.ts           # 时间/域名提取、JSON 格式化
│   ├── browser.ts            # 无头浏览器管理 + 隐身增强
│   ├── cache.ts              # 磁盘缓存 (TTL 5min)
│   ├── circuitBreaker.ts     # 引擎熔断保护
│   ├── dedupContent.ts       # 网页正文去重
│   ├── filter.ts             # 无用信息过滤
│   ├── queryExpander.ts      # 同义词查询扩展
│   ├── queryAdapter.ts       # 引擎查询语法适配
│   ├── fetcher.ts            # 网页抓取 (Readability + 轮换 UA)
│   ├── scraper.ts            # 反爬共享工具（指纹轮换、代理、频率限制）
│   ├── searchContext.ts      # 搜索结果会话管理 + 搜索场景
│   ├── session.ts            # Cookie 会话管理 + 磁盘持久化
│   └── engines/
│       ├── bing.ts           # 必应
│       ├── baidu.ts          # 百度
│       ├── 360.ts            # 360 搜索
│       ├── sogou.ts          # 搜狗
│       ├── duckduckgo.ts     # DuckDuckGo
│       ├── brave.ts          # Brave Search
│       ├── github.ts         # GitHub
│       └── zhihu.ts          # 知乎（浏览器直连 / Bing site: 回退）
├── mcp-search-config.example.json   # 配置示例（纯净 JSON）
├── mcp-search-config.example.jsonc  # 配置示例（JSONC，含注释）
├── package.json
├── tsconfig.json
├── README.md                 # 中文文档
└── README.en.md              # English
```

---

## 使用示例

```text
搜索 + 抓取组合使用：
  search("Rust 语言 入门教程", maxResults: 5)
  fetch("https://rustwiki.org/zh-CN/book/")

指定引擎（含自定义）：
  search(engines: ["bing", "mysearch"], query: "Vue.js 教程")

深度研究：
  research("Rust vs Go 性能对比", maxResults: 8, fetchCount: 3)

结果分析：
  analyze("量子计算最新进展", mode: "正反面", engines: ["bing", "zhihu"])

环境变量自定义：
  SEARCH_ENGINES=bing,github node build/index.js

配置文件 (mcp-search-config.json)：
  export SEARCH_CONFIG_PATH=/etc/mcp-search-config.json
```

---

## License

MIT
