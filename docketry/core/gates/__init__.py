"""Gate registry: manifests reference gates by id; plugins register here.

The port ships the plug board and the hygiene gates. Everything else that
gates — the notice parser, the document classifier, and whatever a firm or a
third party writes — arrives the same way: a class with an `id` and a
`check()`, handed to `register()`.

There are three ways in, and a gate knows which one it came through because
`doctor` and `docketry gates` say so out loud:

  built-in        shipped in this package
  file:<path>     a .py file in the firm's own `<home>/gates/` directory
  package:<dist>  an installed package declaring a `docketry.gates` entry point

The middle one is the five-minute path — drop a file next to guardrails.toml
and it is live on the next command. It is also arbitrary code running with the
operator's permissions, which is why it loads from exactly one directory
inside the Docketry home and from nowhere else, and why every listing says
where each gate came from.

Registration refuses a duplicate id. Silently replacing `name-screen` with
something that returns no findings is precisely the failure this whole
project exists to make impossible, and a typo should not be able to do it.
"""
from __future__ import annotations

import re
from pathlib import Path

_REGISTRY: dict[str, type] = {}
_SOURCES: dict[str, str] = {}
# Files already imported in this process, so loading twice is a no-op rather
# than a self-collision. One CLI command loads once, but the review UI and any
# long-running caller load repeatedly, and a gate colliding with its own
# previous import would have been an obscure way to break them.
_LOADED: dict[str, list[str]] = {}

BUILTIN = "built-in"
GATES_DIR = "gates"
ENTRY_POINT_GROUP = "docketry.gates"

# Gate ids appear in every manifest a firm reads. Keeping them to one shape
# means a manifest never has to be squinted at to see which name is which.
ID_SHAPE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


class GateLoadError(ValueError):
    """A gate file or entry point could not be loaded. Never swallowed."""


def register(cls: type, source: str = BUILTIN) -> type:
    gate_id = getattr(cls, "id", None)
    if not gate_id or not isinstance(gate_id, str):
        raise GateLoadError(
            f"{cls.__name__} has no id — a gate needs `id = \"some-name\"`,"
            " which is the name manifests bind it by"
        )
    if not ID_SHAPE.match(gate_id):
        raise GateLoadError(
            f"gate id {gate_id!r} should be lowercase words joined by hyphens"
            " (like 'name-screen'), so manifests stay readable"
        )
    if not callable(getattr(cls, "check", None)):
        raise GateLoadError(
            f"gate '{gate_id}' has no check(envelope, options) method — that is"
            " the one thing a gate has to do"
        )
    if gate_id in _REGISTRY and _REGISTRY[gate_id] is not cls:
        raise GateLoadError(
            f"gate id '{gate_id}' is already registered by {_SOURCES[gate_id]}."
            " Two gates with one id means a manifest cannot say which it means,"
            " and replacing a shipped gate by accident is how a guardrail"
            " quietly stops guarding. Pick another id."
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

    A file that raises, or that registers nothing, is an error rather than a
    quiet no-op: a gate the operator believes is running and is not is worse
    than no gate at all.
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

    The distribution path, for a gate that outgrows one file. Same registry,
    same rules — the only difference is that pip put it there.
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


# The hygiene gates register on import; nothing here needs a tool.
from . import builtin  # noqa: E402,F401
