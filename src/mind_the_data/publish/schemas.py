"""JSON Schemas for every published file (PLAN 8), and validation against them."""

import json
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# Minor bump for an additive change (a new file or field), major for a breaking one.
SCHEMA_VERSION = "0.9.0"
SCHEMA_NAMES = (
    "league",
    "line",
    "methodology",
    "index",
    "division",
    "season",
    "open_data_manifest",
    "station",
    "station_index",
)


@cache
def load_schema(name: str) -> dict[str, Any]:
    text = (
        resources.files("mind_the_data.publish")
        .joinpath("contract", f"{name}.schema.json")
        .read_text()
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


@cache
def _registry() -> Registry:
    # The schemas refer to each other by file name, e.g. league.schema.json#/$defs/...
    pairs = [
        (f"{name}.schema.json", Resource.from_contents(load_schema(name))) for name in SCHEMA_NAMES
    ]
    return Registry().with_resources(pairs)


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name), registry=_registry())


def validate(document: dict[str, Any], name: str) -> None:
    """Raise jsonschema.ValidationError if the document breaks its contract."""
    validator(name).validate(document)
