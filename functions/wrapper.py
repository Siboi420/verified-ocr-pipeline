"""Schema-driven dispatcher for the beam calculation tools. Stdlib only.

Loads the matching JSON schema from ../schemas/, validates kwargs (required
fields, no unknown keys, numeric types, exclusiveMinimum/minimum bounds),
then calls the underlying beam_calc function.

Public API:
  call_tool(name, **kwargs) -> {"value": ..., "unit": ..., "basis": ...}
      value is a float for min_shear_reinf, a dict for shear_capacity and
      flex_capacity. Raises ValueError on unknown tool or invalid input.
"""

import json
import math
from pathlib import Path

import beam_calc

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "schemas"

REGISTRY = {
    "design_beam": beam_calc.design_beam,
    "flex_capacity": beam_calc.flex_capacity,
    "min_shear_reinf": beam_calc.min_shear_reinf,
    "shear_capacity": beam_calc.shear_capacity,
}


def _bounds(spec, value):
    """Return (lower_bound, upper_bound) for a numeric spec, or None each."""
    lower = upper = None
    if "exclusiveMinimum" in spec:
        lower = (spec["exclusiveMinimum"], False)
    elif "minimum" in spec:
        lower = (spec["minimum"], True)
    if "exclusiveMaximum" in spec:
        upper = (spec["exclusiveMaximum"], False)
    elif "maximum" in spec:
        upper = (spec["maximum"], True)
    return lower, upper


def _validate(name, schema, kwargs):
    params = schema["parameters"]
    props = params.get("properties", {})

    unknown = set(kwargs) - set(props)
    if unknown:
        raise ValueError(
            f"{name}: unknown parameter(s) {sorted(unknown)}; "
            f"expected {sorted(props)}"
        )

    missing = [p for p in params.get("required", []) if p not in kwargs]
    if missing:
        raise ValueError(f"{name}: missing required parameter(s) {missing}")

    for key, value in kwargs.items():
        spec = props[key]
        ptype = spec.get("type", "number")
        if ptype == "string":
            enum = spec.get("enum")
            if not isinstance(value, str) or (enum and value not in enum):
                raise ValueError(
                    f"{name}: parameter '{key}' must be one of "
                    f"{enum or ['<any string>']}, got {value!r}"
                )
            continue
        if ptype == "array":
            if not isinstance(value, list):
                raise ValueError(
                    f"{name}: parameter '{key}' must be an array of numbers, "
                    f"got {type(value).__name__}"
                )
            for v in value:
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or not math.isfinite(v):
                    raise ValueError(
                        f"{name}: parameter '{key}' must contain only finite "
                        f"numbers"
                    )
            continue
        if ptype == "object":
            if not isinstance(value, dict):
                raise ValueError(
                    f"{name}: parameter '{key}' must be an object with numeric "
                    f"values, got {type(value).__name__}"
                )
            for v in value.values():
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or not math.isfinite(v):
                    raise ValueError(
                        f"{name}: parameter '{key}' must map keys to finite "
                        f"numbers"
                    )
            continue
        if spec.get("type") == "number" and isinstance(value, bool):
            raise ValueError(f"{name}: parameter '{key}' must be a number, got {type(value).__name__}")
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name}: parameter '{key}' must be a number, got {type(value).__name__}")
        if not math.isfinite(value):
            raise ValueError(f"{name}: parameter '{key}' must be finite")

        lower, upper = _bounds(spec, value)
        if lower is not None:
            bound, inclusive = lower
            if value <= bound if not inclusive else value < bound:
                raise ValueError(
                    f"{name}: parameter '{key}' must be "
                    f"{'>' if not inclusive else '>='} {bound}, got {value}"
                )
        if upper is not None:
            bound, inclusive = upper
            if value >= bound if not inclusive else value > bound:
                raise ValueError(
                    f"{name}: parameter '{key}' must be "
                    f"{'<' if not inclusive else '<='} {bound}, got {value}"
                )


def call_tool(name, **kwargs):
    if name not in REGISTRY:
        raise ValueError(f"unknown tool '{name}'; available: {sorted(REGISTRY)}")

    schema_file = SCHEMA_DIR / f"{name}.json"
    if not schema_file.is_file():
        raise ValueError(f"{name}: schema file missing: {schema_file}")

    try:
        with open(schema_file, encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name}: cannot read schema {schema_file}: {exc}") from exc

    _validate(name, schema, kwargs)

    value = REGISTRY[name](**kwargs)
    if isinstance(value, dict):
        value = dict(value)  # copy so caller mutations never touch internals

    return {
        "value": value,
        "unit": schema["output"]["unit"],
        "basis": schema["output"]["basis"],
    }


if __name__ == "__main__":
    # Quick sanity check (full checks live in test_shear_tools.py)
    out = call_tool("min_shear_reinf", b_w=350, f_c=28, f_yt=420)
    print(json.dumps(out))