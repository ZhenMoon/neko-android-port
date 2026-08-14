from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_module(name: str, filename: str, *, package: bool = False):
    root = Path(__file__).resolve().parents[1]
    path = root / filename
    if package:
        # Modules like _win32_input use `from ._key_map import ...`, so they
        # must be loaded under the plugin package name.
        pkg_name = "keyboard_controller"
        if pkg_name not in sys.modules:
            pkg = type(sys)(pkg_name)
            pkg.__path__ = [str(root)]
            sys.modules[pkg_name] = pkg
        full = f"{pkg_name}.{name}"
        spec = importlib.util.spec_from_file_location(full, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_unicode_units_split_surrogate_pairs() -> None:
    _win32 = _load_module("_win32_input", "_win32_input.py", package=True)
    units = _win32._unicode_units("Hi" + chr(0x1F600) + "a")
    assert units == [0x48, 0x69, 0xD83D, 0xDE00, 0x61]


def test_unicode_units_bmp_single_unit() -> None:
    _win32 = _load_module("_win32_input2", "_win32_input.py", package=True)
    assert _win32._unicode_units("中") == [0x4E2D]
    assert _win32._unicode_units("a") == [0x61]


def test_powershell_uses_encoded_command() -> None:
    ce = _load_module("_command_exec_test", "_command_exec.py")
    argv = ce._build_command("Get-Process", "powershell")
    assert argv
    exe = "powershell.exe" if ce.is_windows() else "pwsh"
    assert argv[0] == exe
    assert "-EncodedCommand" in argv
    assert len(argv) == argv.index("-EncodedCommand") + 2
    # No bare -Command fallback for powershell.
    assert "-Command" not in argv


def test_powershell_encode_nonempty() -> None:
    ce = _load_module("_command_exec_test2", "_command_exec.py")
    encoded = ce._encode_powershell_command("Get-Date; Write-Host hi")
    assert encoded


def test_file_ops_rejects_path_traversal() -> None:
    fo = _load_module("_file_ops_test", "_file_ops.py")
    root = str(Path(__file__).resolve().parents[2])
    # 相对穿越(跨平台)
    assert fo.resolve_path(root, ".." + os.sep * 5 + "etc" + os.sep + "hosts") is None
    assert fo.resolve_path(root, ".." + os.sep + ".." + os.sep + ".." + os.sep + ".." + os.sep) is None
    # 绝对路径逃逸(Windows 盘符语义在 Windows 上才会被 os.path 识别)
    if os.name == "nt":
        assert fo.resolve_path(root, r"C:\Windows\System32\drivers\etc\hosts") is None
        assert fo.resolve_path(root, "..\\..\\..\\..\\Windows\\win.ini") is None
    # 正常相对路径
    inner = fo.resolve_path(root, ".")
    assert inner is not None and Path(inner).is_dir()
