# -*- coding: utf-8 -*-
"""
Android shims for libraries that have no wheel for the Android platform.

Chaquopy installs pip packages with ``--platform android_21_arm64_v8a
--only-binary :all:``. Libraries like PyYAML / PyAV / pyautogui / pyzmq
publish neither a pure-python wheel nor an android wheel, so the
N.E.K.O. runtime would hit ``ModuleNotFoundError`` at import time.

This module registers placeholder modules in ``sys.modules`` BEFORE any
N.E.K.O. package is imported. Placeholders answer attribute access with a
clear ``NotImplementedError`` so individual features degrade visibly instead
of crashing the whole server at import.

``orjson`` gets a real fallback to the stdlib ``json`` module.
"""

from __future__ import annotations

import importlib.util
import json
import sys


def _new_module(name: str) -> "ModuleType":
    """Create a fresh module object with a valid ``__spec__``.

    Libraries like yarl probe ``find_spec("pydantic_core")`` to decide whether
    pydantic v2 is present; a bare ``types.ModuleType`` has ``__spec__ is
    None`` and makes ``find_spec`` raise ``ValueError``.
    """
    mod = type(sys)(name)
    try:
        mod.__spec__ = importlib.util.spec_from_loader(name, loader=None)
    except Exception:
        pass
    return mod


class _Unavailable:
    """Module-like object whose every attribute access raises a clear error."""

    def __init__(self, name: str):
        self.__name__ = name
        self._name = name

    def __getattr__(self, item):
        raise NotImplementedError(
            f"'{self._name}' is not available on this platform (no Android "
            f"wheel). The feature using it is degraded on N.E.K.O. for Android."
        )

    def __call__(self, *args, **kwargs):
        raise NotImplementedError(
            f"'{self._name}' is not available on this platform (no Android "
            f"wheel). The feature using it is degraded on N.E.K.O. for Android."
        )

    def __repr__(self):
        return f"<unavailable shim {self._name}>"


def _register_unavailable(name: str) -> None:
    sys.modules.setdefault(name, _Unavailable(name))


def _register_orjson() -> None:
    if "orjson" in sys.modules:
        return
    shim = _new_module("orjson")
    shim.dumps = lambda obj, *a, **k: json.dumps(
        obj, ensure_ascii=False, default=str, **{kk: vv for kk, vv in k.items() if kk not in ("option",)}
    )
    shim.loads = json.loads
    shim.dump = lambda obj, fp, *a, **k: json.dump(obj, fp, ensure_ascii=False, default=str)
    shim.load = json.load
    sys.modules["orjson"] = shim


def _patch_pydantic_v1() -> None:
    """Expose the small pydantic-v2 API surface N.E.K.O. uses on v1.

    Android has no ``pydantic-core`` wheel, so pip resolves pydantic 1.10.x.
    The boot path only uses a handful of v2 names; alias them to the v1
    equivalents so the routers import without source edits:
      * ``model_validate`` -> ``parse_obj``
      * ``model_dump`` -> ``dict``
      * ``field_validator`` -> ``validator``
      * ``ConfigDict`` -> plain dict (v1 ignores ``model_config`` at class level)
    """
    try:
        import pydantic
    except Exception:
        return
    try:
        version = getattr(pydantic, "VERSION", "") or ""
    except Exception:
        version = ""
    if not version.startswith("1."):
        return
    BaseModel = pydantic.BaseModel
    if not hasattr(BaseModel, "model_validate"):
        BaseModel.model_validate = classmethod(BaseModel.parse_obj.__func__)
    if not hasattr(BaseModel, "model_validate_json"):
        BaseModel.model_validate_json = classmethod(BaseModel.parse_raw.__func__)
    if not hasattr(BaseModel, "model_dump"):
        _v1_dict = BaseModel.dict

        def _model_dump(self, *args, **kwargs):
            data = _v1_dict(self, *args, **kwargs)
            if isinstance(data, dict) and "model_config" in data:
                data.pop("model_config", None)
            return data

        BaseModel.model_dump = _model_dump
    if not hasattr(BaseModel, "model_dump_json"):
        BaseModel.model_dump_json = BaseModel.json
    if not hasattr(pydantic, "field_validator"):
        _v1_validator = pydantic.validator

        def _field_validator(*fields, **kwargs):
            mode = kwargs.pop("mode", "after")
            if mode == "before":
                kwargs["pre"] = True
            # v2 defaults check_fields to False; v1 defaults to True. Drop it
            # so validators on computed/serialized fields don't fail import.
            kwargs.pop("check_fields", None)
            return _v1_validator(*fields, **kwargs)

        pydantic.field_validator = _field_validator
    if not hasattr(pydantic, "model_validator"):
        def _model_validator(*args, **kwargs):
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return lambda f: f

        pydantic.model_validator = _model_validator
    # pydantic v2 custom serializers have no v1 equivalent; a no-op decorator
    # lets models that declare ``@field_serializer(...)`` still import (the
    # custom serialization silently falls back to default field serialization).
    if not hasattr(pydantic, "field_serializer"):
        pydantic.field_serializer = lambda *a, **k: (lambda f: f)
    if not hasattr(pydantic, "ConfigDict"):
        pydantic.ConfigDict = dict

    if not hasattr(pydantic, "type_adapter"):
        class _V1TypeAdapter:
            """v2-style ``TypeAdapter`` backed by pydantic v1 model methods.

            The plugin SDK validates RPC envelopes through
            ``TypeAdapter(RpcEnvelope).validate_python(...)``.  On v1 the same
            work is ``RpcEnvelope.parse_obj(...)``.
            """

            def __init__(self, model_type, *args, **kwargs):
                self.model_type = model_type

            def validate_python(self, data):
                return self.model_type.parse_obj(data)

            def validate_json(self, raw):
                return self.model_type.parse_raw(raw)

            def dump_python(self, obj, *args, **kwargs):
                return obj

        _ta_mod = _new_module("pydantic.type_adapter")
        _ta_mod.TypeAdapter = _V1TypeAdapter
        sys.modules["pydantic.type_adapter"] = _ta_mod
        pydantic.type_adapter = _ta_mod
        if not hasattr(pydantic, "TypeAdapter"):
            pydantic.TypeAdapter = _V1TypeAdapter

    if not hasattr(pydantic, "ValidationInfo"):
        class ValidationInfo:  # noqa: N801
            __slots__ = ("config", "context")

            def __init__(self, config=None, context=None):
                self.config = config
                self.context = context

        pydantic.ValidationInfo = ValidationInfo

    class _V1FieldProxy:
        """Minimal v2 ``FieldInfo``-like view over a v1 ``ModelField``."""

        __slots__ = ("_mf", "_fi")

        def __init__(self, model_field):
            self._mf = model_field
            self._fi = getattr(model_field, "field_info", None)

        @property
        def default(self):
            return self._mf.default

        @property
        def default_factory(self):
            return getattr(self._fi, "default_factory", None)

        @property
        def json_schema_extra(self):
            if self._fi is None:
                return None
            direct = getattr(self._fi, "json_schema_extra", None)
            if direct is not None:
                return direct
            extra = getattr(self._fi, "extra", {}) or {}
            return extra.get("json_schema_extra")

        @property
        def alias(self):
            return self._mf.alias

        @property
        def exclude(self):
            return getattr(self._fi, "exclude", None)

        @property
        def annotation(self):
            return self._mf.outer_type_

        def is_required(self):
            return bool(getattr(self._mf, "required", False))

        def __repr__(self):
            return "<V1FieldProxy {0}>".format(getattr(self._mf, "name", "?"))

    if not hasattr(BaseModel, "model_fields"):
        # v1 treats an un-annotated class attribute like ``model_config =
        # {...}`` as a plain field; v2 reserves it for config.  Filter it out
        # of ``model_fields`` (and thus ``model_dump``) to match v2.
        _V1_FIELD_FILTER = frozenset({"model_config"})

        def _build_model_fields(cls):
            result = {}
            for _name, _mf in getattr(cls, "__fields__", {}).items():
                if _name in _V1_FIELD_FILTER:
                    continue
                result[_name] = _V1FieldProxy(_mf)
            return result

        # v2 exposes ``model_fields`` as a plain per-class dict attribute
        # (``SettingsCls.model_fields.items()``).  A property/classmethod on
        # BaseModel would hand back a descriptor or bound method instead, so
        # inject a real dict into every subclass at class-creation time.
        _orig_model_new = pydantic.main.ModelMetaclass.__new__

        def _model_meta_new(mcs, name, bases, namespace, **kwargs):
            new_cls = _orig_model_new(mcs, name, bases, namespace, **kwargs)
            if "model_fields" not in namespace:
                new_cls.model_fields = _build_model_fields(new_cls)
            return new_cls

        pydantic.main.ModelMetaclass.__new__ = _model_meta_new

    if "pydantic_core" not in sys.modules:
        # pydantic 1.10 names the no-default sentinel ``Undefined`` (there is
        # no ``PydanticUndefined`` alias until later versions); the plugin SDK
        # compares ``field.default is PydanticUndefined`` against the
        # ``pydantic_core`` singleton, so the shim must BE the v1 sentinel.
        try:
            from pydantic.fields import Undefined as _PydanticUndefined
        except Exception:
            _PydanticUndefined = object()
        _pc_mod = _new_module("pydantic_core")
        _pc_mod.PydanticUndefined = _PydanticUndefined
        sys.modules["pydantic_core"] = _pc_mod


def _patch_soxr_resamplestream() -> None:
    """Add ``ResampleStream.clear()`` for soxr 0.3.x (the Android wheel).

    The N.E.K.O. source is written against python-soxr 1.1.0, whose streaming
    resampler exposes ``clear()`` (reset FIR latency/filter history). Chaquopy
    builds soxr 0.3.7 on Android, which lacks that method; calling it blew up
    the voice session with "ResampleStream object has no attribute clear".
    ``clear()`` is only used to discard mid-stream resampler state, so
    recreating the underlying native CySoxr achieves the same effect.
    """
    try:
        import soxr
    except Exception:
        return
    if hasattr(soxr.ResampleStream, "clear"):
        return
    _real_resampler = soxr.ResampleStream

    class _CompatResampler(_real_resampler):
        def __init__(
            self,
            in_rate,
            out_rate,
            num_channels,
            dtype="float32",
            quality="HQ",
        ):
            self._rs_args = (in_rate, out_rate, num_channels)
            self._rs_kwargs = {"dtype": dtype, "quality": quality}
            super().__init__(in_rate, out_rate, num_channels, dtype=dtype, quality=quality)

        def clear(self):
            fresh = _real_resampler(*self._rs_args, **self._rs_kwargs)
            self._cysoxr = fresh._cysoxr
            self._type = fresh._type

    soxr.ResampleStream = _CompatResampler


def _patch_openai_max_completion_tokens() -> None:
    """Translate ``max_completion_tokens`` → ``max_tokens`` for openai < 1.55.

    The N.E.K.O. codebase passes ``max_completion_tokens`` to
    ``chat.completions.create`` on every non-Anthropic LLM call (main dialog,
    memory, proactive chat, API connectivity test…). That parameter only
    entered the openai-python SDK signature in 1.55, but Android cannot pull a
    newer wheel: every version >= 1.44 hard-depends on ``jiter`` (a Rust
    extension with no Android build). So we wrap ``Completions.create`` /
    ``AsyncCompletions.create`` and translate the param at the boundary,
    keeping the SDK we already have. ``max_tokens`` is the legacy equivalent
    (same "max output tokens" cap) accepted by every OpenAI-compatible
    endpoint.
    """
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
    except Exception:
        return

    import functools
    import inspect

    def _wrap(orig):
        @functools.wraps(orig)
        def wrapped(self, *args, **kwargs):
            if "max_completion_tokens" in kwargs:
                mct = kwargs.pop("max_completion_tokens")
                if mct is not None and "max_tokens" not in kwargs:
                    kwargs["max_tokens"] = mct
            return orig(self, *args, **kwargs)

        return wrapped

    for cls in (Completions, AsyncCompletions):
        try:
            sig = inspect.signature(cls.create)
        except Exception:
            continue
        if "max_completion_tokens" in sig.parameters:
            continue
        cls.create = _wrap(cls.create)


def _register_ormsgpack() -> None:
    """ormsgpack is a compiled extension with no Android wheel; the plugin
    message-plane transport only uses ``packb``/``unpackb`` on JSON-like RPC
    payloads. Both ends run the same shim in the Android process, so a stdlib
    json round-trip is wire-compatible for our internal use."""
    if "ormsgpack" in sys.modules:
        return
    shim = _new_module("ormsgpack")

    def _packb(obj, *args, **kwargs):
        return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")

    def _unpackb(raw, *args, **kwargs):
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8")
        return json.loads(raw)

    shim.packb = _packb
    shim.unpackb = _unpackb
    sys.modules["ormsgpack"] = shim


def install() -> None:
    _register_orjson()
    _register_ormsgpack()
    _patch_pydantic_v1()
    _patch_soxr_resamplestream()
    _patch_openai_max_completion_tokens()
    for name in (
        "av",
        "yaml",
        "pyautogui",
        "pygetwindow",
        "pyperclip",
        "msgpack",
        "pyncm_async",
        "steamworks",
        "steamworks.exceptions",
        "steamworks.enums",
        "steamworks.methods",
        "steamworks.steamworks",
        "onnxruntime",
        "tokenizers",
    ):
        _register_unavailable(name)
    # zmq: pyzmq has no android wheel. agent_event_bus already tolerates a
    # missing zmq (it guards the import); registering a shim keeps any other
    # lazy `import zmq` from hard-failing.
    _register_unavailable("zmq")
    _register_unavailable("zmq.asyncio")
    _register_unavailable("zmq.error")
