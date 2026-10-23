"""Registry access behind a tiny interface so tweaks can be unit-tested off Windows."""

from __future__ import annotations

from typing import Any, Protocol

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

KINDS = ("DWORD", "QWORD", "SZ", "EXPAND_SZ", "BINARY")
MISSING = object()


class Registry(Protocol):
    def get(self, hive: str, key: str, name: str) -> tuple[Any, str] | None: ...
    def set(self, hive: str, key: str, name: str, value: Any, kind: str) -> None: ...
    def delete(self, hive: str, key: str, name: str) -> None: ...
    def values(self, hive: str, key: str) -> dict[str, tuple[Any, str]]: ...


class WinRegistry:
    def __init__(self):
        if winreg is None:
            raise RuntimeError("The Windows registry is only available on Windows")
        self._hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
        self._types = {
            "DWORD": winreg.REG_DWORD,
            "QWORD": winreg.REG_QWORD,
            "SZ": winreg.REG_SZ,
            "EXPAND_SZ": winreg.REG_EXPAND_SZ,
            "BINARY": winreg.REG_BINARY,
        }
        self._names = {v: k for k, v in self._types.items()}

    def get(self, hive, key, name):
        try:
            with winreg.OpenKey(self._hives[hive], key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                value, typ = winreg.QueryValueEx(k, name)
        except OSError:
            return None
        return value, self._names.get(typ, "SZ")

    def set(self, hive, key, name, value, kind):
        with winreg.CreateKeyEx(self._hives[hive], key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            if kind == "BINARY" and isinstance(value, (list, str)):
                value = bytes.fromhex(value) if isinstance(value, str) else bytes(value)
            winreg.SetValueEx(k, name, 0, self._types[kind], value)

    def delete(self, hive, key, name):
        try:
            with winreg.OpenKey(self._hives[hive], key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass

    def values(self, hive, key):
        out = {}
        try:
            with winreg.OpenKey(self._hives[hive], key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                i = 0
                while True:
                    try:
                        name, value, typ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out[name] = (value, self._names.get(typ, "SZ"))
                    i += 1
        except OSError:
            pass
        return out


class FakeRegistry:
    """In-memory registry used by tests and --demo mode."""

    def __init__(self):
        self.data: dict[tuple[str, str], dict[str, tuple[Any, str]]] = {}

    def _k(self, hive, key):
        return hive, key.lower()

    def get(self, hive, key, name):
        return self.data.get(self._k(hive, key), {}).get(name)

    def set(self, hive, key, name, value, kind):
        if kind == "BINARY" and isinstance(value, str):
            value = bytes.fromhex(value)
        self.data.setdefault(self._k(hive, key), {})[name] = (value, kind)

    def delete(self, hive, key, name):
        self.data.get(self._k(hive, key), {}).pop(name, None)

    def values(self, hive, key):
        return dict(self.data.get(self._k(hive, key), {}))


def default_registry() -> Registry:
    return WinRegistry() if winreg is not None else FakeRegistry()
