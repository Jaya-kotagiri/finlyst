"""Non-mutating structural validation, separate from matching and financial calculations."""

import json

from pydantic import ValidationError


def validate_schema(schema, value, stage):
    """Validate the original object without dumping/coercing/defaulting its contents."""
    try:
        if hasattr(schema, 'model_validate'):
            schema.model_validate(value, strict=True)
        else:
            schema.validate_python(value, strict=True)
    except ValidationError as exc:
        # Field paths are actionable; input values can contain private notes or account data.
        details = '; '.join(
            f"{'.'.join(map(str, error['loc'])) or '<document>'}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise ValueError(f'{stage} schema validation failed: {details}') from None
    return value


def parse_document(content, schema, stage):
    """Decode a loaded JSON document, reporting its stage/path without private values."""
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        location = f'line {exc.lineno}, column {exc.colno}' if isinstance(exc, json.JSONDecodeError) else 'invalid text encoding'
        raise ValueError(f'{stage}: invalid JSON ({location})') from None
    return validate_schema(schema, value, stage)
