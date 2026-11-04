"""Wire graph: blocks connected block-pin -> block-pin.

A :class:`WireGraph` holds engine blocks as nodes and directed wires from one
block's output pin to another block's input pin as edges. Wires can be added
and removed at any time; :meth:`topological_order` returns a deterministic
execution order and raises :class:`WireCycleError` if the graph contains a
cycle, so the cyclic scan executor can refuse to run a cyclic graph.

The engine layer has no GUI or IO dependencies.
"""

from __future__ import annotations

from bisect import insort
from dataclasses import dataclass

from input_automation.engine.block import Block
from input_automation.engine.pins import Pin


class WireGraphError(ValueError):
    """Base class for wire graph errors."""


class DuplicateBlockError(WireGraphError):
    """Raised when adding a block whose id is already in the graph."""


class UnknownBlockError(WireGraphError):
    """Raised when a referenced block is not in the graph."""


class UnknownPinError(WireGraphError):
    """Raised when a referenced pin does not exist on a block."""


class DuplicateWireError(WireGraphError):
    """Raised when adding a wire that already exists."""


class UnknownWireError(WireGraphError):
    """Raised when removing a wire that does not exist."""


class WireTypeError(WireGraphError):
    """Raised when a wire connects pins of incompatible data types."""


class WireCycleError(WireGraphError):
    """Raised when :meth:`WireGraph.topological_order` finds a cycle."""


@dataclass(frozen=True)
class Wire:
    """One directed connection: ``src_block.src_pin -> dst_block.dst_pin``."""

    src_block: str
    src_pin: str
    dst_block: str
    dst_pin: str


class WireGraph:
    """Directed graph of blocks connected block-pin -> block-pin.

    Blocks are nodes; wires are directed edges from an output pin to an input
    pin. Disconnected blocks are nodes without edges and still appear in
    :meth:`topological_order`.
    """

    def __init__(self) -> None:
        self._blocks: dict[str, Block] = {}
        self._wires: set[Wire] = set()
        self._successors: dict[str, set[str]] = {}
        self._predecessors: dict[str, set[str]] = {}

    # -- blocks ---------------------------------------------------------

    def add_block(self, block: Block) -> None:
        """Add ``block`` to the graph; raise :class:`DuplicateBlockError` if its id exists."""
        if block.id in self._blocks:
            raise DuplicateBlockError(f"block {block.id!r} already in graph")
        self._blocks[block.id] = block
        self._successors.setdefault(block.id, set())
        self._predecessors.setdefault(block.id, set())

    def remove_block(self, block_id: str) -> None:
        """Remove ``block_id`` and every wire touching it.

        Ra :class:`UnknownBlockError` if the block is not in the graph.
        """
        if block_id not in self._blocks:
            raise UnknownBlockError(f"unknown block {block_id!r}")
        for wire in list(self._wires):
            if wire.src_block == block_id or wire.dst_block == block_id:
                self._wires.discard(wire)
                self._successors[wire.src_block].discard(wire.dst_block)
                self._predecessors[wire.dst_block].discard(wire.src_block)
        del self._blocks[block_id]
        del self._successors[block_id]
        del self._predecessors[block_id]

    def has_block(self, block_id: str) -> bool:
        """Return ``True`` if ``block_id`` is in the graph."""
        return block_id in self._blocks

    def block(self, block_id: str) -> Block:
        """Return the block ``block_id``; raise :class:`UnknownBlockError` if unknown."""
        try:
            return self._blocks[block_id]
        except KeyError:
            raise UnknownBlockError(f"unknown block {block_id!r}") from None

    def blocks(self) -> list[str]:
        """Return all block ids, sorted."""
        return sorted(self._blocks)

    # -- wires ----------------------------------------------------------

    def connect(
        self,
        src_block: str,
        src_pin: str,
        dst_block: str,
        dst_pin: str,
    ) -> None:
        """Connect ``src_block.src_pin`` to ``dst_block.dst_pin``.

        Validate that both blocks exist, the source pin is an output, the
        destination pin is an input, and both pins share a dtype. Raise
        :class:`DuplicateWireError` if the wire already exists.
        """
        wire = Wire(src_block, src_pin, dst_block, dst_pin)
        if wire in self._wires:
            raise DuplicateWireError(
                f"wire {src_block!r}.{src_pin!r} -> {dst_block!r}.{dst_pin!r} already exists"
            )
        src_pin_obj, dst_pin_obj = self._validate_endpoints(
            src_block, src_pin, dst_block, dst_pin
        )
        if src_pin_obj.dtype is not dst_pin_obj.dtype:
            raise WireTypeError(
                f"wire: pin type mismatch {src_block!r}.{src_pin!r} "
                f"({src_pin_obj.dtype.value}) -> {dst_block!r}.{dst_pin!r} "
                f"({dst_pin_obj.dtype.value})"
            )
        self._wires.add(wire)
        self._successors[src_block].add(dst_block)
        self._predecessors[dst_block].add(src_block)

    def disconnect(self, src_block: str, src_pin: str, dst_block: str, dst_pin: str) -> None:
        """Remove the wire ``src_block.src_pin -> dst_block.dst_pin``.

        Raise :class:`UnknownWireError` if no such wire exists.
        """
        wire = Wire(src_block, src_pin, dst_block, dst_pin)
        if wire not in self._wires:
            raise UnknownWireError(
                f"no wire {src_block!r}.{src_pin!r} -> {dst_block!r}.{dst_pin!r}"
            )
        self._wires.discard(wire)
        self._successors[src_block].discard(dst_block)
        self._predecessors[dst_block].discard(src_block)

    def wires(self) -> list[Wire]:
        """Return all wires, sorted by (src_block, src_pin, dst_block, dst_pin)."""
        return sorted(
            self._wires,
            key=lambda w: (w.src_block, w.src_pin, w.dst_block, w.dst_pin),
        )

    def has_wire(self, src_block: str, src_pin: str, dst_block: str, dst_pin: str) -> bool:
        """Return ``True`` if the wire ``src_block.src_pin -> dst_block.dst_pin`` exists."""
        return Wire(src_block, src_pin, dst_block, dst_pin) in self._wires

    def successors(self, block_id: str) -> set[str]:
        """Return the ids of blocks that ``block_id`` drives (copy).

        Raise :class:`UnknownBlockError` if the block is not in the graph.
        """
        self._require_block(block_id)
        return set(self._successors.get(block_id, set()))

    def predecessors(self, block_id: str) -> set[str]:
        """Return the ids of blocks that drive ``block_id`` (copy).

        Raise :class:`UnknownBlockError` if the block is not in the graph.
        """
        self._require_block(block_id)
        return set(self._predecessors.get(block_id, set()))

    # -- cycles / order -------------------------------------------------

    def has_cycle(self) -> bool:
        """Return ``True`` if the wire graph contains a cycle."""
        try:
            self.topological_order()
        except WireCycleError:
            return True
        return False

    def topological_order(self) -> list[str]:
        """Return block ids in execution order (predecessors first).

        Order is deterministic: among ready blocks the lexicographically
        smallest id runs next. Raise :class:`WireCycleError` if the graph
        contains a cycle.
        """
        in_degree = {bid: len(self._predecessors.get(bid, set())) for bid in self._blocks}
        ready: list[str] = sorted(bid for bid, deg in in_degree.items() if deg == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for succ in sorted(self._successors.get(node, set())):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    insort(ready, succ)
        if len(order) != len(self._blocks):
            raise WireCycleError("wire graph contains a cycle")
        return order

    # -- internals ------------------------------------------------------

    def _require_block(self, block_id: str) -> None:
        if block_id not in self._blocks:
            raise UnknownBlockError(f"unknown block {block_id!r}")

    def _validate_endpoints(
        self,
        src_block: str,
        src_pin: str,
        dst_block: str,
        dst_pin: str,
    ) -> tuple[Pin, Pin]:
        src = self._blocks.get(src_block)
        if src is None:
            raise UnknownBlockError(f"unknown block {src_block!r}")
        dst = self._blocks.get(dst_block)
        if dst is None:
            raise UnknownBlockError(f"unknown block {dst_block!r}")
        try:
            src_pin_obj = src.output(src_pin)
        except KeyError:
            raise UnknownPinError(
                f"block {src_block!r} has no output pin {src_pin!r}"
            ) from None
        try:
            dst_pin_obj = dst.input(dst_pin)
        except KeyError:
            raise UnknownPinError(
                f"block {dst_block!r} has no input pin {dst_pin!r}"
            ) from None
        return src_pin_obj, dst_pin_obj
