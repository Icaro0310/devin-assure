"""confkit — small settings package.

See CONVENTIONS.md: public API is re-exported here and listed in
``__all__``; users only ever ``import confkit``.
"""

from confkit.settings import dump_settings, load_settings

__all__ = ["dump_settings", "load_settings"]
