import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import schema  # noqa: E402


def _sample_types() -> schema.TypeSet:
    t = schema.Type(
        slug="hrs_employee",
        form_id=701,
        form_title="DEV_DTO_HRS_EMPLOYEE",
        description="one line,\ncollapsed",
        fields=[
            schema.FieldSpec(name="full_name", id="full_name", type="string"),
            schema.FieldSpec(name="role", id="item_998877", type="enum[CFO, CTO, CEO]", title="Role"),
            schema.FieldSpec(name="manager", id="manager_ref", type="ref(hrs_employee)"),
            schema.FieldSpec(name="notes", id="notes", type="text", writable=False, title="Internal notes"),
        ],
    )
    return schema.TypeSet(types=[t])


def test_render_then_parse_roundtrip():
    types = _sample_types()
    rendered = schema.render_types_schema("layer-123", types)
    assert b"layer-123" in rendered
    parsed = schema.parse_types_schema(rendered)

    t = parsed.by_slug("hrs_employee")
    assert t is not None
    assert t.form_id == 701
    assert t.form_title == "DEV_DTO_HRS_EMPLOYEE"
    assert t.description == "one line, collapsed"

    # Fields come back sorted by name regardless of source order.
    names = [f.name for f in t.fields]
    assert names == sorted(names)

    role = next(f for f in t.fields if f.name == "role")
    assert role.type == "enum[CFO, CTO, CEO]"
    assert role.title == "Role"
    assert role.writable is True

    notes = next(f for f in t.fields if f.name == "notes")
    assert notes.writable is False
    assert notes.title == "Internal notes"

    manager = next(f for f in t.fields if f.name == "manager")
    assert manager.type == "ref(hrs_employee)"


def test_field_id_defaults_to_name_and_type_defaults_to_string():
    data = b"""
types:
  widget:
    formId: 5
    form: WIDGET
    fields:
      raw_field: {}
"""
    parsed = schema.parse_types_schema(data)
    t = parsed.by_slug("widget")
    f = t.fields[0]
    assert f.id == "raw_field"
    assert f.type == "string"
    assert f.writable is True


def test_missing_form_id_is_an_error():
    data = b"""
types:
  widget:
    form: WIDGET
    fields: {}
"""
    with pytest.raises(ValueError, match="formId"):
        schema.parse_types_schema(data)


def test_duplicate_form_id_across_slugs_is_an_error():
    data = b"""
types:
  widget_a:
    formId: 5
    form: WIDGET
    fields: {}
  widget_b:
    formId: 5
    form: WIDGET
    fields: {}
"""
    with pytest.raises(ValueError, match="both map to formId 5"):
        schema.parse_types_schema(data)


def test_slug_for_form_falls_back_when_unknown():
    types = _sample_types()
    assert types.slug_for_form(701) == "hrs_employee"
    assert types.slug_for_form(999) == "form_999"


def test_load_types_schema_missing_file_is_not_an_error(tmp_path):
    result, found = schema.load_types_schema(str(tmp_path))
    assert found is False
    assert result is None
