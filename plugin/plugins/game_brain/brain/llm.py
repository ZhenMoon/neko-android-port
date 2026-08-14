"""LLM 调用客户端（文本 + 视觉）。

支持两种配置来源：
1. 插件面板用户填写的 ``[game_brain]`` 段（llm_url / llm_api_key / llm_model
   / vision_url / vision_api_key / vision_model）。
2. 主系统 cloud 配置回退（通过 ``self.system_info.get_system_config`` 获取）。

视觉调用使用 OpenAI 兼容的 ``content`` 数组形态（``image_url`` data URI），
与 ``utils.llm_client.ChatOpenAI`` 的 ``_normalize_messages`` 完全兼容。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 从主系统 cloud 配置读取 key 的字段名（与 bilibili_danmaku 一致）
_CLOUD_FIELDS = ("url", "api_key", "model")

JsonDumpFn = Callable[[], "dict[str, Any]"]


@dataclass
class LlmSettings:
    """LLM 连接设置。空值表示未配置。"""

    llm_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    vision_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    timeout_sec: float = 60.0
    max_tokens: int = 4096

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "LlmSettings":
        data = data or {}
        return cls(
            llm_url=str(data.get("llm_url") or "").strip().rstrip("/"),
            llm_api_key=str(data.get("llm_api_key") or "").strip(),
            llm_model=str(data.get("llm_model") or "").strip(),
            vision_url=str(data.get("vision_url") or "").strip().rstrip("/"),
            vision_api_key=str(data.get("vision_api_key") or "").strip(),
            vision_model=str(data.get("vision_model") or "").strip(),
            timeout_sec=float(data.get("timeout_sec") or 60.0),
            max_tokens=int(data.get("max_tokens") or 4096),
        )

    def text_configured(self) -> bool:
        return bool(self.llm_url and self.llm_model)

    def vision_configured(self) -> bool:
        return bool(self.vision_url and self.vision_model)

    def to_public(self) -> dict:
        """返回可安全展示的配置（key 打码）。"""
        return {
            "llm_url": self.llm_url or "",
            "llm_model": self.llm_model or "",
            "llm_api_key": _mask(self.llm_api_key),
            "vision_url": self.vision_url or "",
            "vision_model": self.vision_model or "",
            "vision_api_key": _mask(self.vision_api_key),
            "text_configured": self.text_configured(),
            "vision_configured": self.vision_configured(),
        }


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


class LlmClient:
    """基于 utils.llm_client.ChatOpenAI 的文本/视觉客户端。

    视觉能力：把 JPEG base64 data URI 放进 OpenAI 兼容的 content 数组。
    """

    def __init__(
        self,
        settings: LlmSettings,
        *,
        system_config_provider: Optional[JsonDumpFn] = None,
    ):
        self.settings = settings
        self._system_config_provider = system_config_provider
        # 延迟导入，避免插件 __init__ 时因环境问题崩掉
        self._chat_cls: Optional[Any] = None

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------

    async def _resolve_system_cloud(self) -> dict:
        """尝试从主系统配置回退 cloud LLM 配置（面板未填时）。"""
        if self._system_config_provider is None:
            return {}
        try:
            cfg = await self._system_config_provider()
        except Exception as exc:  # pragma: no cover
            logger.debug("system config read failed: %s", exc)
            return {}
        if not isinstance(cfg, dict):
            return {}
        # 主系统 config 里 cloud 通常挂在顶层 "cloud" 或 "background_llm.cloud"
        for key in ("cloud", "background_llm"):
            node = cfg.get(key)
            if isinstance(node, dict):
                cloud = node.get("cloud") if isinstance(node.get("cloud"), dict) else node
                if isinstance(cloud, dict) and cloud.get("url"):
                    return cloud
        return {}

    async def _ensure_text_settings(self) -> LlmSettings:
        if self.settings.text_configured():
            return self.settings
        cloud = await self._resolve_system_cloud()
        if cloud.get("url"):
            return LlmSettings(
                llm_url=str(cloud["url"]).rstrip("/"),
                llm_api_key=str(cloud.get("api_key") or ""),
                llm_model=str(cloud.get("model") or "deepseek-chat"),
                vision_url=str(cloud["url"]).rstrip("/"),
                vision_api_key=str(cloud.get("api_key") or ""),
                vision_model=str(cloud.get("model") or "deepseek-chat"),
                timeout_sec=self.settings.timeout_sec,
                max_tokens=self.settings.max_tokens,
            )
        raise RuntimeError(
            "文本 LLM 未配置：请在插件面板的「LLM 设置」中填写 url/api_key/model"
        )

    def _get_chat_cls(self) -> Any:
        if self._chat_cls is None:
            from utils.llm_client import ChatOpenAI  # type: ignore

            self._chat_cls = ChatOpenAI
        return self._chat_cls

    # ------------------------------------------------------------------
    # 文本调用
    # ------------------------------------------------------------------

    async def complete_text(
        self,
        messages: list[dict],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_mode: bool = False,
    ) -> str:
        """纯文本 chat 完成。返回 content 字符串（已 strip）。"""
        settings = await self._ensure_text_settings()
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "base_url": settings.llm_url,
            "api_key": settings.llm_api_key or "sk-placeholder",
            "max_completion_tokens": max_tokens or settings.max_tokens,
            "max_retries": 2,
            "timeout": settings.timeout_sec,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        client = self._get_chat_cls()(**kwargs)
        resp = await client.ainvoke(messages)
        text = getattr(resp, "content", None)
        if not text:
            raise RuntimeError("LLM 返回空内容")
        return str(text).strip()

    async def complete_json(
        self,
        messages: list[dict],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict:
        """要求模型返回 JSON 对象并解析。解析失败抛 ValueError。"""
        text = await self.complete_text(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
        )
        return parse_json_object(text)

    # ------------------------------------------------------------------
    # 视觉调用
    # ------------------------------------------------------------------

    async def complete_vision(
        self,
        *,
        prompt: str,
        image_jpeg_b64: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """把一张 JPEG(base64) 图片交给视觉模型理解。

        Args:
            prompt: 对图片提出的问题。
            image_jpeg_b64: JPEG 图片的 base64 字符串（不含 data URI 前缀）。
            system: 可选 system prompt。
        """
        if not self.settings.vision_configured():
            raise RuntimeError("视觉模型未配置：请在面板「LLM 设置」填写 vision_url/vision_model")
        data_uri = f"data:image/jpeg;base64,{image_jpeg_b64}"
        content: list[dict] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        kwargs: dict[str, Any] = {
            "model": self.settings.vision_model,
            "base_url": self.settings.vision_url,
            "api_key": self.settings.vision_api_key or "sk-placeholder",
            "max_completion_tokens": max_tokens or 1024,
            "max_retries": 1,
            "timeout": self.settings.timeout_sec,
        }
        client = self._get_chat_cls()(**kwargs)
        resp = await client.ainvoke(messages)
        text = getattr(resp, "content", None)
        if not text:
            raise RuntimeError("视觉模型返回空内容")
        return str(text).strip()


def parse_json_object(text: str) -> dict:
    """从模型输出里稳健地提取 JSON 对象。

    处理带 ```json 围栏、前后杂散文本等情况。
    """
    if not text:
        raise ValueError("空输出，无法解析 JSON")
    t = text.strip()
    if t.startswith("```"):
        # 去掉围栏
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 尝试从第一个 { 到最后一个 }
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"无法解析 LLM 输出为 JSON: {t[:200]}")


def encode_jpeg_base64(jpeg_bytes: bytes) -> str:
    return base64.b64encode(jpeg_bytes).decode("ascii")
