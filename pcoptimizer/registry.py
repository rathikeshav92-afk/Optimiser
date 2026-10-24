"""Registry access behind a tiny interface so tweaks can be unit-tested off Windows."""

from __future__ import annotations

from typing import Any, Protocol

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

KINDS = ("DWORD", "QWORD", "SZ", "EXPAND_SZ", "MULTI_SZ", "BINARY")
HIVES = ("HKCU", "HKLM", "HKCR", "HKU")
OTHER = "OTHER"  # a value type we can show but not edit (REG_NONE, REG_LINK, ...)
MISSING = object()


class Registry(Protocol):
    def get(self, hive: str, key: str, name: str) -> tuple[Any, str] | None: ...
    def set(self, hive: str, key: str, name: str, value: Any, kind: str) -> None: ...
    def delete(self, hive: str, key: str, name: str) -> None: ...
    def values(self, hive: str, key: str) -> dict[str, tuple[Any, str]]: ...
    def subkeys(self, hive: str, key: str) -> list[str]: ...
    def key_exists(self, hive: str, key: str) -> bool: ...
    def delete_key(self, hive: str, key: str) -> None: ...


class WinRegistry:
    def __init__(self):
        if winreg is None:
            raise RuntimeError("The Windows registry is only available on Windows")
        self._hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE,
                       "HKCR": winreg.HKEY_CLASSES_ROOT, "HKU": winreg.HKEY_USERS}
        self._types = {
            "DWORD": winreg.REG_DWORD,
            "QWORD": winreg.REG_QWORD,
            "SZ": winreg.REG_SZ,
            "EXPAND_SZ": winreg.REG_EXPAND_SZ,
            "MULTI_SZ": winreg.REG_MULTI_SZ,
            "BINARY": winreg.REG_BINARY,
        }
        self._names = {v: k for k, v in self._types.items()}

    def get(self, hive, key, name):
        try:
            with winreg.OpenKey(self._hives[hive], key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                value, typ = winreg.QueryValueEx(k, name)
        except OSError:
            return None
        return value, self._names.get(typ, OTHER)

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
                    out[name] = (value, self._names.get(typ, OTHER))
                    i += 1
        except OSError:
            pass
        return out

    def subkeys(self, hive, key):
        out = []
        try:
            with winreg.OpenKey(self._hives[hive], key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                i = 0
                while True:
                    try:
                        out.append(winreg.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
        return sorted(out, key=str.lower)

    def key_exists(self, hive, key):
        try:
            with winreg.OpenKey(self._hives[hive], key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                return True
        except OSError:
            return False

    def delete_key(self, hive, key):
        """Delete a key and everything under it (winreg can only delete empty keys)."""
        for sub in self.subkeys(hive, key):
            self.delete_key(hive, f"{key}\\{sub}")
        try:
            winreg.DeleteKeyEx(self._hives[hive], key, winreg.KEY_WOW64_64KEY, 0)
        except FileNotFoundError:
            pass


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

    def key_exists(self, hive, key):
        k = key.lower()
        return any(h == hive and (kk == k or kk.startswith(k + "\\")) for h, kk in self.data)

    def set_empty_key(self, hive, key):
        self.data.setdefault(self._k(hive, key), {})

    def delete_key(self, hive, key):
        k = key.lower()
        for h, kk in list(self.data):
            if h == hive and (kk == k or kk.startswith(k + "\\")):
                del self.data[(h, kk)]

    def subkeys(self, hive, key):
        prefix = (key.lower().rstrip("\\") + "\\") if key else ""
        names = set()
        for h, k in self.data:
            if h == hive and k.startswith(prefix) and k != prefix.rstrip("\\"):
                names.add(k[len(prefix):].split("\\")[0])
        return sorted(names)


def default_registry() -> Registry:
    return WinRegistry() if winreg is not None else FakeRegistry()
