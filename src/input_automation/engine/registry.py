"""Block registry: maps block type names to :class:`Block` classes.

Blocks register themselves under a type name (e.g. ``"AND"``). The registry
validates, looks up, and instantiates blocks by id. There is no implicit
global state: create a :class:`BlockRegistry` where needed (a module-level
``default_registry`` is provided for convenience).
"""

from __future__ import annotations

from input_automation.engine.block import Block


class BlockRegistry:
    """Registry of block types keyed by name.

    Duplicate registrations of the same name are rejected; looking up or
    creating an unknown name raises :exc:`KeyError`.
    """

    def __init__(self) -> None:
        self._types: dict[str, type[Block]] = {}

    def register(self, type_name: str, block_class: type[Block]) -> None:
        """Register ``block_class`` under ``type_name``.

        Raise :exc:`TypeError` if ``block_class`` is not a :class:`Block`
        subclass, :exc:`ValueError` if ``type_name`` is already registered.
        """
        if not isinstance(type_name, str) or not type_name:
            raise ValueError("type_name must be a non-empty string")
        if not (isinstance(block_class, type) and issubclass(block_class, Block)):
            raise TypeError(
                f"{block_class!r} is not a subclass of "
                "input_automation.engine.block.Block"
            )
        if type_name in self._types:
            raise ValueError(f"block type {type_name!r} already registered")
        self._types[type_name] = block_class

    def deregister(self, type_name: str) -> None:
        """Remove ``type_name`` from the registry; raise :exc:`KeyError` if unknown."""
        if type_name not in self._types:
            raise KeyError(f"unknown block type {type_name!r}")
        del self._types[type_name]

    def get(self, type_name: str) -> type[Block]:
        """Return the registered class for ``type_name``; :exc:`KeyError` if unknown."""
        try:
            return self._types[type_name]
        except KeyError:
            raise KeyError(f"unknown block type {type_name!r}") from None

    def has(self, type_name: str) -> bool:
        """Return ``True`` if ``type_name`` is registered."""
        return type_name in self._types

    def create(self, type_name: str, block_id: str) -> Block:
        """Instantiate a registered block type as ``block_id``."""
        return self.get(type_name)(block_id)

    def names(self) -> list[str]:
        """Return all registered type names, sorted."""
        return sorted(self._types)


default_registry = BlockRegistry()

__all__ = ["BlockRegistry", "default_registry"]
