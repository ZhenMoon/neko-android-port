"""攻略搜索模块（MCP 后端，唯一搜索通道）。

通过跨插件调用 ``mcp_adapter:call_tool`` 调用外部 ``mcp-search-server``
（多引擎聚合搜索 MCP 服务器），不再依赖 web_search 插件。

依赖：
  - ``mcp_adapter`` 插件已启用，且配置了 stdio server（默认名 ``search``），
    指向 ``game_brain/third_party/mcp-search-server/build/index.js``。

后端工具：
  - ``search``      多引擎搜索（返回文本 + 末尾 JSON 结构化结果）
  - ``fetch``       抓取网页正文
  - ``search_and_fetch``  搜索 + 抓取前 N 条正文
  - ``research``    深度研究
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# 跨插件调用 mcp_adapter:call_tool 的函数签名
McpCallable = Callable[[str, str, dict], Awaitable[Any]]

# 默认搜索工具参数
_DEFAULT_ENGINES = ["bing", "baidu", "360", "github", "zhihu", "csdn"]
# search_and_fetch 抓正文的最大字符
_FETCH_BODY_MAX = 6000


class GuideSearch:
    """MCP 多引擎攻略搜索。"""

    def __init__(
        self,
        *,
        mcp_call: Optional[McpCallable] = None,
        server_name: str = "search",
        rounds: int = 3,
        max_results_per_query: int = 6,
        engines: Optional[list[str]] = None,
    ):
        self._mcp_call = mcp_call
        self.server_name = server_name
        self.rounds = rounds
        self.max_results_per_query = max_results_per_query
        self.engines = list(engines or _DEFAULT_ENGINES)

    # ------------------------------------------------------------------

    def set_mcp_call(self, mcp_call: McpCallable) -> None:
        self._mcp_call = mcp_call

    def set_server_name(self, server_name: str) -> None:
        self.server_name = server_name

    def set_engines(self, engines: Optional[list[str]]) -> None:
        if engines:
            self.engines = list(engines)

    def is_available(self) -> bool:
        return self._mcp_call is not None

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    async def search_game(
        self,
        game_name: str,
        *,
        extra_keywords: Optional[list[str]] = None,
        with_rounds: Optional[int] = None,
    ) -> dict:
        """多轮搜索一个游戏的攻略，返回聚合结果。

        Returns:
            {
              "game_name": ...,
              "queries": [...],
              "results": [ {title, url, snippet, engine, domain, ...} ... ],
              "body_snippets": [ {url, title, text} ... ],   # 抓到的正文片段
              "available": bool,
            }
        """
        if not self.is_available():
            return {
                "game_name": game_name,
                "queries": [],
                "results": [],
                "body_snippets": [],
                "available": False,
            }

        rounds = int(with_rounds or self.rounds)
        queries = _build_queries(game_name, extra_keywords=extra_keywords, rounds=rounds)
        results: list[dict] = []
        seen_urls: set[str] = set()

        for q in queries:
            items = await self._mcp_search(q, self.max_results_per_query)
            for item in items:
                url = str(item.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(item)

        # 可选：抓正文片段（用 mcp fetch 工具）
        body_snippets: list[dict] = []
        if results:
            body_snippets = await self._fetch_bodies(results)

        return {
            "game_name": game_name,
            "queries": queries,
            "results": results,
            "body_snippets": body_snippets,
            "available": True,
        }

    async def _mcp_search(self, query: str, max_results: int) -> list[dict]:
        """调用 mcp search 工具并解析结构化结果。"""
        if not self._mcp_call:
            return []
        try:
            raw = await self._mcp_call(
                self.server_name,
                "search",
                {
                    "query": query,
                    "maxResults": max_results,
                    "engines": self.engines,
                    "timeout": 20000,
                },
            )
        except Exception as exc:
            logger.warning("mcp search failed %r: %s", query, exc)
            return []
        text = _extract_mcp_text(raw)
        return _parse_search_json(text)

    async def _fetch_bodies(self, results: list[dict], max_pages: int = 3) -> list[dict]:
        """用 mcp fetch 工具抓取前 N 条正文。"""
        if not self._mcp_call:
            return []
        out: list[dict] = []
        for item in results[:max_pages]:
            url = str(item.get("url") or "")
            if not url:
                continue
            try:
                raw = await self._mcp_call(
                    self.server_name,
                    "fetch",
                    {"url": url, "timeout": 15000, "maxLength": _FETCH_BODY_MAX},
                )
            except Exception as exc:
                logger.debug("mcp fetch failed %s: %s", url, exc)
                continue
            text = _extract_mcp_text(raw)
            body = _parse_fetch_body(text)
            if body:
                out.append({"url": url, "title": str(item.get("title") or ""), "text": body})
        return out


# ---------------------------------------------------------------------------
# MCP 响应解析
# ---------------------------------------------------------------------------

def _extract_mcp_text(raw: Any) -> str:
    """从 mcp_adapter:call_tool 的 Ok 值里提取 MCP 文本内容。

    形态（call_tool 返回 Ok(payload)）：
      payload = {"result": {"content": [{"type": "text", "text": "..."}]}, "summary": ...}
    也可能直接返回 {"result": {...}} 或 {"content": [...]}。
    """
    if raw is None:
        return ""
    value = raw
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, dict):
        result = value.get("result")
        if isinstance(result, dict):
            value = result
        elif result is not None:
            # 某些网关路径 result 直接是 content 结构
            pass
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        parts = []
        for c in value.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text") or ""))
        return "\n".join(parts)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return ""


def _parse_search_json(text: str) -> list[dict]:
    """从 search 工具输出里解析末尾 JSON 数组。

    mcp-search-server 的 search 输出为：
      标题行/摘要行... \n---\n[{title,url,description,engine,domain,publishedDate,score}]
    """
    if not text:
        return []
    idx = text.rfind("\n---")
    json_part = text[idx + 4 :] if idx >= 0 else text
    start = json_part.find("[")
    if start < 0:
        return []
    try:
        data = json.loads(json_part[start:])
    except json.JSONDecodeError:
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or item.get("snippet") or ""),
            "engine": str(item.get("engine") or ""),
            "domain": str(item.get("domain") or ""),
            "publishedDate": item.get("publishedDate"),
            "score": item.get("score"),
        })
    return out


def _parse_fetch_body(text: str) -> str:
    """从 fetch 工具输出里提取正文。

    fetch 输出：`标题: ...\nURL: ...\n字数: N\n\n<正文>`
    """
    if not text:
        return ""
    marker = "\n\n"
    idx = text.find(marker)
    body = text[idx + len(marker) :] if idx >= 0 else text
    return body.strip()


# ---------------------------------------------------------------------------
# 查询词
# ---------------------------------------------------------------------------

def _build_queries(game_name: str, *, extra_keywords: Optional[list[str]], rounds: int) -> list[str]:
    """生成多轮搜索查询词。"""
    base = game_name.strip()
    queries: list[str] = []
    if extra_keywords:
        for kw in extra_keywords[:3]:
            queries.append(f"{base} {kw}")
    patterns = [
        f"{base} 攻略",
        f"{base} 键位 操作 教程",
        f"{base} 新手 入门 玩法",
        f"{base} 战斗 系统 界面",
        f"{base} 进阶 技巧 心得",
    ]
    queries.extend(patterns)
    seen: set[str] = set()
    dedup: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            dedup.append(q)
    return dedup[:max(1, rounds)]
