"""Project persistence: Pydantic schema for blocks, pins, wires, documents (YAML/JSON)."""

from input_automation.project.blocks import Block
from input_automation.project.document import Project
from input_automation.project.persistence import detect_format, load_project, save_project
from input_automation.project.pins import Pin, PinDirection, PinDType
from input_automation.project.wires import Wire

__all__ = [
    "Block",
    "Pin",
    "PinDirection",
    "PinDType",
    "Project",
    "Wire",
    "detect_format",
    "load_project",
    "save_project",
]
