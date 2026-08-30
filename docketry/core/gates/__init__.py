"""Gate registry: manifests reference gates by id; plugins register here.

The port ships the plug board and the hygiene gates. The notice parser and
the document classifier are tools that plug in — they live in docketry/tools
and register themselves when the package is imported, which is the same
route a third-party gate takes.
"""
from __future__ import annotations

_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    gate_id = getattr(cls, "id", None)
    if not gate_id:
        raise ValueError(f"{cls.__name__} has no id")
    _REGISTRY[gate_id] = cls
    return cls


def get(gate_id: str) -> type:
    if gate_id not in _REGISTRY:
        raise KeyError(
            f"unknown gate '{gate_id}' (registered: {', '.join(sorted(_REGISTRY)) or 'none'})"
        )
    return _REGISTRY[gate_id]


def all_ids() -> list[str]:
    return sorted(_REGISTRY)


# The hygiene gates register on import; nothing here needs a tool.
from . import builtin  # noqa: E402,F401
