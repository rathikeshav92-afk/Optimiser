"""Stores the Anthropic API key, encrypted with Windows DPAPI so only this Windows user can read it."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path

from .platform_utils import IS_WINDOWS, app_data_dir


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("Windows could not encrypt/decrypt the API key")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _path() -> Path:
    return app_data_dir() / "ai_settings.json"


def save_api_key(key: str, path: Path | None = None) -> None:
    raw = key.strip().encode()
    if IS_WINDOWS:
        payload = {"key_dpapi": base64.b64encode(_dpapi(raw, True)).decode()}
    else:
        payload = {"key": raw.decode()}
    (path or _path()).write_text(json.dumps(payload), encoding="utf-8")


def load_api_key(path: Path | None = None) -> str | None:
    """Saved key, falling back to the ANTHROPIC_API_KEY environment variable."""
    try:
        data = json.loads((path or _path()).read_text(encoding="utf-8"))
        if "key_dpapi" in data and IS_WINDOWS:
            return _dpapi(base64.b64decode(data["key_dpapi"]), False).decode()
        if data.get("key"):
            return data["key"]
    except (OSError, ValueError):
        pass
    return os.environ.get("ANTHROPIC_API_KEY") or None


def forget_api_key(path: Path | None = None) -> None:
    try:
        (path or _path()).unlink()
    except FileNotFoundError:
        pass
