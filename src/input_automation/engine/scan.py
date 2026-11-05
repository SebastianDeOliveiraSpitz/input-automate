"""Cyclic scan executor for the pure-Python logic engine.

One scan cycle runs in three phases:

1. ``read_inputs`` — injects external values into input pins (default: nothing;
   input pins not driven by a wire keep their last value).
2. evaluate every block in
   :meth:`WireGraph.topological_order` (predecessors first, deterministic).
3. ``write_outputs`` — collects output pin values to the outside (default:
   nothing).

The engine layer has no GUI or IO dependencies: IO providers are injected as
callables, which keeps the engine headless-testable (mock providers in tests,
evdev/uinput providers in the IO layer).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from time import monotonic

from input_automation.engine.wires import WireGraph

InputReader = Callable[[WireGraph], None]
OutputWriter = Callable[[WireGraph], None]


class ScanExecutor:
    """Fixed-cycle-time scan executor.

    ``cycle_time_s`` is the target period between cycle starts. If a cycle
    overruns the cycle time, the next cycle starts immediately (no negative
    sleep).
    """

    def __init__(
        self,
        graph: WireGraph,
        cycle_time_s: float = 0.02,
        *,
        read_inputs: InputReader | None = None,
        write_outputs: OutputWriter | None = None,
    ) -> None:
        if cycle_time_s <= 0:
            raise ValueError(f"cycle_time_s must be > 0, got {cycle_time_s!r}")
        self._graph = graph
        self._cycle_time_s = float(cycle_time_s)
        self._read_inputs = read_inputs or (lambda _graph: None)
        self._write_outputs = write_outputs or (lambda _graph: None)

    @property
    def graph(self) -> WireGraph:
        """The wire graph this executor scans."""
        return self._graph

    @property
    def cycle_time_s(self) -> float:
        """Target period between cycle starts, in seconds."""
        return self._cycle_time_s

    def run_once(self) -> float:
        """Execute one scan cycle and return its actual duration in seconds.

        Phases: read inputs → evaluate blocks in topological order (propagating
        each block's outputs along its wires as it goes) → write outputs. Raise
        :exc:`WireCycleError` if the graph contains a cycle; propagate any
        exception raised by a block's :meth:`Block.evaluate`.
        """
        started = monotonic()
        self._read_inputs(self._graph)
        for block_id in self._graph.topological_order():
            block = self._graph.block(block_id)
            block.evaluate()
            self._propagate_outputs(block_id)
        self._write_outputs(self._graph)
        return monotonic() - started

    def _propagate_outputs(self, src_block: str) -> None:
        """Copy ``src_block``'s output pin values to the input pins wired to them.

        Called right after ``src_block`` evaluates; topological order guarantees
        successors have not been evaluated yet in this cycle, so every wire
        delivers freshly computed values.
        """
        src_block_obj = self._graph.block(src_block)
        for wire in self._graph.wires():
            if wire.src_block != src_block:
                continue
            dst_block = self._graph.block(wire.dst_block)
            dst_block.input(wire.dst_pin).set(
                src_block_obj.output(wire.src_pin).value
            )

    def run_forever(self, stop_event: threading.Event) -> None:
        """Run cycles at fixed cycle time until ``stop_event`` is set."""
        while not stop_event.is_set():
            elapsed = self.run_once()
            remaining = self._cycle_time_s - elapsed
            if remaining > 0:
                stop_event.wait(remaining)
