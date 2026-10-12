"""Pydantic JSON Schema -> the subset accepted by Gemini's `response_json_schema`.

Gemini accepts only a subset of JSON Schema (type, format, title, description, enum,
items, prefixItems, minItems, maxItems, minimum, maximum, anyOf, properties,
additionalProperties, required, propertyOrdering, $ref/$defs). Pydantic emits more
(`default`, `maxLength`, `$ref` with sibling keys...). We inline references and drop
unsupported keywords; the full Pydantic model still validates the answer locally.
"""

from typing import Any

_ALLOWED = {
    "type",
    "format",
    "title",
    "description",
    "enum",
    "items",
    "prefixItems",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "anyOf",
    "properties",
    "additionalProperties",
    "required",
    "propertyOrdering",
}
_MAX_DEPTH = 32


def _resolve(node: Any, defs: dict[str, Any], depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise ValueError("schema too deep or recursive")
    if isinstance(node, list):
        return [_resolve(item, defs, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
        return _resolve(merged, defs, depth + 1)
    if "oneOf" in node and "anyOf" not in node:
        node = {**node, "anyOf": node["oneOf"]}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED:
            continue
        if key == "properties":
            out[key] = {name: _resolve(sub, defs, depth + 1) for name, sub in value.items()}
        elif key in ("items", "anyOf", "prefixItems", "additionalProperties"):
            out[key] = _resolve(value, defs, depth + 1)
        else:
            out[key] = value
    if out.get("type") == "object" and "properties" in out:
        out.setdefault("propertyOrdering", list(out["properties"]))
    return out


def gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})
    result: dict[str, Any] = _resolve({k: v for k, v in schema.items() if k != "$defs"}, defs, 0)
    return result
