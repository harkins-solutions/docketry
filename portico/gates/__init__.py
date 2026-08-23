"""Gate registry: manifests reference gates by id; plugins register here."""
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


# Built-ins register on import.
from . import builtin  # noqa: E402,F401
