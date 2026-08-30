"""Gate registry. Manifests bind gates by id; this is where ids resolve.

A gate is any class with an `id` and a `check(envelope, options)` method,
passed to `register()`. Three load paths, all reported by `docketry gates`
and `docketry doctor`:

  built-in        registered on import from docketry/core/gates/
  file:<path>     a .py file in <home>/gates/, loaded by load_home()
  package:<name>  an installed package's `docketry.gates` entry point

Files in <home>/gates/ are imported and run with the operator's permissions.
Docketry loads from that directory and no other. A file that raises, or
registers nothing, raises GateLoadError rather than being skipped, so a gate
is never silently absent.

register() refuses a duplicate id: two gates with one id leave a manifest
unable to say which it means, and a home file could otherwise replace a
shipped gate.

See GATES.md for the authoring interface.
"""
from __future__ import annotations

import re
from pathlib import Path

_REGISTRY: dict[str, type] = {}
_SOURCES: dict[str, str] = {}
# Files already imported in this process. Re-importing would build a second
# class object with the same id and trip the duplicate check, which matters
# for the review UI and any other long-running caller that loads more than
# once.
_LOADED: dict[str, list[str]] = {}

BUILTIN = "built-in"
GATES_DIR = "gates"
ENTRY_POINT_GROUP = "docketry.gates"

# One shape for ids, so manifests stay consistent: lowercase words, hyphens.
ID_SHAPE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


class GateLoadError(ValueError):
    """A gate file or entry point could not be loaded. Never swallowed."""


def register(cls: type, source: str = BUILTIN) -> type:
    gate_id = getattr(cls, "id", None)
    if not gate_id or not isinstance(gate_id, str):
        raise GateLoadError(
            f"{cls.__name__} has no id — a gate needs `id = \"some-name\"`,"
            " the name manifests bind it by"
        )
    if not ID_SHAPE.match(gate_id):
        raise GateLoadError(
            f"gate id {gate_id!r} should be lowercase words joined by hyphens"
            " (like 'name-screen'), so manifests stay readable"
        )
    if not callable(getattr(cls, "check", None)):
        raise GateLoadError(
            f"gate '{gate_id}' has no check(envelope, options) method"
        )
    if gate_id in _REGISTRY and _REGISTRY[gate_id] is not cls:
        raise GateLoadError(
            f"gate id '{gate_id}' is already registered by"
            f" {_SOURCES[gate_id]}. Ids must be unique: a manifest binding"
            " this id could not say which gate it meant, and registering it"
            " twice would replace the existing gate. Pick another id."
        )
    _REGISTRY[gate_id] = cls
    _SOURCES.setdefault(gate_id, source)
    return cls


def get(gate_id: str) -> type:
    if gate_id not in _REGISTRY:
        raise KeyError(
            f"unknown gate '{gate_id}' (registered: {', '.join(sorted(_REGISTRY)) or 'none'})"
        )
    return _REGISTRY[gate_id]


def all_ids() -> list[str]:
    return sorted(_REGISTRY)


def source_of(gate_id: str) -> str:
    """Where this gate came from: built-in, a file, or an installed package."""
    return _SOURCES.get(gate_id, "unknown")


def described() -> list[tuple[str, str, str, object]]:
    """(id, source, first line of the docstring, allowed_stages) for listings."""
    out = []
    for gate_id in all_ids():
        cls = _REGISTRY[gate_id]
        doc = (cls.__doc__ or "").strip().splitlines()
        out.append((gate_id, source_of(gate_id), doc[0] if doc else "",
                    getattr(cls, "allowed_stages", None)))
    return out


def _adopt(before: set[str], source: str) -> list[str]:
    """Attribute whatever registered itself since `before` to `source`."""
    fresh = sorted(set(_REGISTRY) - before)
    for gate_id in fresh:
        _SOURCES[gate_id] = source
    return fresh


def load_file(path: str | Path) -> list[str]:
    """Import one gate file and return the ids it registered.

    Raises GateLoadError if the file fails to import or registers nothing,
    so a gate that is configured but not running is never silent. Importing
    the same path twice in one process returns the first result.
    """
    import importlib.util

    path = Path(path).resolve()
    already = _LOADED.get(str(path))
    if already is not None:
        return already
    before = set(_REGISTRY)
    spec = importlib.util.spec_from_file_location(
        f"docketry_gate_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise GateLoadError(f"{path} is not importable as Python")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except GateLoadError:
        raise
    except Exception as e:
        raise GateLoadError(f"{path.name} failed to load: {e.__class__.__name__}: {e}") from None
    fresh = _adopt(before, f"file:{path}")
    _LOADED[str(path)] = fresh
    if not fresh:
        _LOADED.pop(str(path), None)
        raise GateLoadError(
            f"{path.name} registered no gates — a gate file needs"
            " `@register` (or `register(YourGate)`) on a class with an id."
            " See GATES.md."
        )
    return fresh


def load_dir(directory: str | Path) -> list[str]:
    """Load every gate file in a directory, in name order. Missing dir is fine."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    loaded = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        loaded.extend(load_file(path))
    return loaded


def load_home(home: str | Path) -> list[str]:
    """The firm's own gates, from `<home>/gates/`."""
    return load_dir(Path(home) / GATES_DIR)


def load_installed() -> list[str]:
    """Gates from installed packages declaring a `docketry.gates` entry point.

    The entry point may name the gate class or a module that registers on
    import. Both are accepted.
    """
    from importlib.metadata import entry_points

    loaded = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        before = set(_REGISTRY)
        try:
            obj = ep.load()
        except Exception as e:
            raise GateLoadError(
                f"entry point '{ep.name}' ({ep.value}) failed to load:"
                f" {e.__class__.__name__}: {e}") from None
        # An entry point may point at a module that registers on import, or
        # straight at the gate class. Both read naturally in pyproject.toml.
        if isinstance(obj, type):
            register(obj, source=f"package:{ep.name}")
        _adopt(before, f"package:{ep.name}")
        loaded.extend(sorted(set(_REGISTRY) - before))
    return loaded


# The four hygiene gates register on import. notice-parser and doc-classifier
# live in docketry/tools and register through the same public path a
# third-party gate uses.
from . import builtin  # noqa: E402,F401
