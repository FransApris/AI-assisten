"""
features/plugin_manager.py — Plugin System APRIS
=================================================
Load & eksekusi fitur secara modular berdasarkan konfigurasi .env.

Konfigurasi di .env:
    ENABLED_PLUGINS=calendar,gmail,weather,memory,medical,tasks,contacts,notion,drive_ingest

Cara pakai di chat_server.py:
    from features.plugin_manager import PluginManager
    pm = PluginManager()
    apris_reply = pm.process_intercepts(apris_reply)
"""
import os
from typing import Callable


# Registry semua plugin yang tersedia
# Format: "nama_plugin": callable yang menerima (apris_reply) dan return apris_reply
_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    """Decorator untuk mendaftarkan plugin intercept handler."""
    def decorator(fn: Callable):
        _REGISTRY[name] = fn
        return fn
    return decorator


class PluginManager:
    def __init__(self):
        raw = os.getenv(
            "ENABLED_PLUGINS",
            "calendar,gmail,weather,memory,medical,tasks,contacts,notion"
        )
        self.enabled = {p.strip().lower() for p in raw.split(",") if p.strip()}
        print(f"[PluginManager] Plugin aktif: {sorted(self.enabled)}", flush=True)

    def is_enabled(self, name: str) -> bool:
        return name.lower() in self.enabled

    def process_intercepts(self, apris_reply: str) -> str:
        """
        Jalankan semua intercept handler yang terdaftar dan aktif,
        dalam urutan yang sudah ditentukan.
        """
        for name, handler in _REGISTRY.items():
            if self.is_enabled(name):
                try:
                    apris_reply = handler(apris_reply)
                except Exception as e:
                    print(f"[Plugin:{name}] Error: {e}", flush=True)
        return apris_reply

    def status(self) -> dict:
        return {
            "enabled" : sorted(self.enabled),
            "registered": sorted(_REGISTRY.keys()),
            "active"  : sorted(k for k in _REGISTRY if k in self.enabled),
        }
