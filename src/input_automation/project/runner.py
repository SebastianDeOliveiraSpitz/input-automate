"""Headless execution of a :class:`Project` document through the scan engine.

This module is the thin bridge between the project schema layer and the pure
engine layer: it instantiates engine blocks from a project document (via the
block registry), applies schema pin defaults, connects the declared wires, and
runs the cyclic scan executor for a fixed number of cycles.

No GUI or IO dependencies — headless-testable by design.
"""

from __future__ import annotations

from input_automation.engine import (
    Block,
    BlockRegistry,
    ScanExecutor,
    WireCycleError,
    WireGraph,
    WireGraphError,
    default_registry,
)
from input_automation.engine.pins import PinTypeError
from input_automation.engine.scan import InputReader, OutputWriter
from input_automation.project import Block as SchemaBlock
from input_automation.project import Project


class ProjectRunError(ValueError):
    """Raised when a project document cannot be executed headless."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _apply_pin_defaults(block: Block, schema_block: SchemaBlock) -> None:
    """Apply each schema pin's non-``None`` default to the matching engine pin.

    Schema-declared pins the block type doesn't have are skipped; a default
    that violates the engine pin's dtype raises :class:`ProjectRunError`.
    """
    for schema_pin in schema_block.pins:
        if schema_pin.default is None:
            continue
        try:
            pin = block.output(schema_pin.name)
        except KeyError:
            try:
                pin = block.input(schema_pin.name)
            except KeyError:
                continue  # schema declares a pin this block type doesn't have
        try:
            pin.set(schema_pin.default)
        except (PinTypeError, TypeError) as exc:
            raise ProjectRunError(
                f"block {schema_block.id!r}: pin {schema_pin.name!r} default "
                f"{schema_pin.default!r}: {exc}"
            ) from exc


def build_graph(
    project: Project,
    registry: BlockRegistry | None = None,
) -> WireGraph:
    """Instantiate engine blocks for ``project`` and connect its wires.

    Block types are resolved through ``registry`` (defaults to
    :data:`input_automation.engine.default_registry`). Schema pin defaults are
    applied to matching engine pins after construction.
    """
    registry = registry or default_registry
    graph = WireGraph()
    for schema_block in project.blocks:
        try:
            block = registry.create(schema_block.type, schema_block.id)
        except KeyError as exc:
            raise ProjectRunError(
                f"block {schema_block.id!r}: unknown block type "
                f"{schema_block.type!r}"
            ) from exc
        _apply_pin_defaults(block, schema_block)
        graph.add_block(block)
    for wire in project.wires:
        try:
            graph.connect(
                wire.src_block,
                wire.src_pin,
                wire.dst_block,
                wire.dst_pin,
            )
        except WireGraphError as exc:
            raise ProjectRunError(
                f"wire {wire.src_block!r}.{wire.src_pin!r} -> "
                f"{wire.dst_block!r}.{wire.dst_pin!r}: {exc}"
            ) from exc
    return graph


def run_project(
    project: Project,
    cycles: int = 1,
    registry: BlockRegistry | None = None,
    *,
    read_inputs: InputReader | None = None,
    write_outputs: OutputWriter | None = None,
) -> dict[str, dict[str, object]]:
    """Execute ``project`` for ``cycles`` scan cycles.

    Optional ``read_inputs``/``write_outputs`` callables are injected into the
    scan engine (e.g. the evdev and uinput IO providers) so a project with
    INPUT/OUTPUT blocks talks to real devices; when omitted, input pins keep
    their defaults and outputs are discarded. Mock providers keep this
    headless-testable without any hardware.

    Returns a mapping ``block_id -> {output_pin_name: value}`` with the pin
    values after the last cycle. Raises :class:`ProjectRunError` on unknown
    block types, wiring problems, or a cyclic wire graph.
    """
    if cycles < 1:
        raise ProjectRunError(f"cycles must be >= 1, got {cycles!r}")
    graph = build_graph(project, registry)
    executor = ScanExecutor(
        graph,
        read_inputs=read_inputs,
        write_outputs=write_outputs,
    )
    try:
        for _ in range(cycles):
            executor.run_once()
    except WireCycleError as exc:
        raise ProjectRunError(
            "wire graph contains a cycle; refusing to run"
        ) from exc
    return {
        block_id: {
            name: pin.value
            for name, pin in graph.block(block_id).outputs.items()
        }
        for block_id in graph.blocks()
    }


__all__ = ["ProjectRunError", "build_graph", "run_project"]
