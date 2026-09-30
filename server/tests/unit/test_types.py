import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import tree as tree_mod  # noqa: E402
from migration_factory_plugin_mcp.graph import types as types_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.schema import FieldSpec, Type, TypeSet  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Field, FieldOption, Form, Section  # noqa: E402


# ---------------------------------------------------------------------------
# stub client
# ---------------------------------------------------------------------------


class _StubSim:
    """Duck-typed stand-in for SimulatorClient.get_form — not a subclass of
    the real client, just an object with the one method resolve_types uses."""

    def __init__(self, forms: dict, raising: "set[int]" = frozenset()):
        self.forms = forms
        self.raising = set(raising)
        self.calls: list = []

    def get_form(self, form_id: int, filter: str = ""):
        self.calls.append(form_id)
        if form_id in self.raising:
            raise RuntimeError(f"boom {form_id}")
        return self.forms[form_id]


# ---------------------------------------------------------------------------
# (a) _field_slug
# ---------------------------------------------------------------------------


def test_field_slug_verbatim_identifier():
    f = Field(id="inheritForms", title="Inherit forms from parent")
    assert types_mod._field_slug(f) == "inheritForms"


def test_field_slug_item_prefix_falls_back_to_title():
    f = Field(id="item_12345", title="Full Name Field Extra Words Ignored")
    assert types_mod._field_slug(f) == "full_name_field_extra"


def test_field_slug_falls_back_to_slugified_id_when_title_empty():
    f = Field(id="item_999", title="")
    assert types_mod._field_slug(f) == "item_999"


def test_field_slug_non_identifier_id_uses_title():
    f = Field(id="9bad-id", title="Some Title Here")
    assert types_mod._field_slug(f) == "some_title_here"


# ---------------------------------------------------------------------------
# (b) _field_type dispatch
# ---------------------------------------------------------------------------


def test_field_type_bool():
    assert types_mod._field_type(Field(cls="checkbox")) == "bool"
    assert types_mod._field_type(Field(cls="Switch")) == "bool"


def test_field_type_date():
    assert types_mod._field_type(Field(cls="Date")) == "date"


def test_field_type_enum_from_options():
    f = Field(cls="select", options=[FieldOption(value="A"), FieldOption(value="B")])
    assert types_mod._field_type(f) == "enum[A, B]"


def test_field_type_select_without_options_is_string():
    f = Field(cls="select", options=[])
    assert types_mod._field_type(f) == "string"


def test_field_type_ref_placeholder():
    f = Field(cls="actorlink", extra={"formId": 55})
    assert types_mod._field_type(f) == "ref:55"


def test_field_type_ref_without_target_is_string():
    f = Field(cls="link", extra={})
    assert types_mod._field_type(f) == "string"


def test_field_type_int_and_number():
    assert types_mod._field_type(Field(cls="", type="integer")) == "int"
    assert types_mod._field_type(Field(cls="", type="Number")) == "number"


def test_field_type_text_when_multiline():
    f = Field(cls="", type="", extra={"multiline": True})
    assert types_mod._field_type(f) == "text"


def test_field_type_default_string():
    assert types_mod._field_type(Field(cls="", type="")) == "string"


# ---------------------------------------------------------------------------
# (c) _slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert types_mod._slugify("  DEV_DTO_HRS  ") == "dev_dto_hrs"
    assert types_mod._slugify("Hello, World!!") == "hello_world"
    assert types_mod._slugify("___") == ""
    assert types_mod._slugify("A--B") == "a_b"
    assert types_mod._slugify("") == ""


# ---------------------------------------------------------------------------
# (d) full resolve_types run: collision disambiguation + majority prefix
# ---------------------------------------------------------------------------


def _widget_form_701() -> Form:
    return Form(
        id=701,
        title="DEV_DTO_WIDGET",
        sections=[
            Section(
                content=[
                    Field(id="item_1001", cls="", type="string", title="Widget Name"),
                    Field(id="ownerRef", cls="actorlink", extra={"formId": 702}),
                ]
            )
        ],
    )


def _widget_form_702() -> Form:
    return Form(
        id=702,
        title="DEV_DTO_WIDGET",
        sections=[
            Section(
                content=[
                    Field(
                        id="item_2001",
                        cls="select",
                        title="Status Choice",
                        options=[FieldOption(value="A"), FieldOption(value="B")],
                    ),
                    Field(id="outRef", cls="link", extra={"formId": 9999}),
                ]
            )
        ],
    )


def _platform_form_703() -> Form:
    return Form(
        id=703,
        title="PLATFORM_THING",
        sections=[
            Section(
                content=[
                    Field(id="item_3001", cls="checkbox", title="Active Flag"),
                    Field(id="item_4001", cls="", type="string", title="Same Name"),
                    Field(id="item_4002", cls="", type="string", title="Same Name"),
                    Field(id="", cls="", type="string", title="No Id Field"),
                ]
            )
        ],
    )


def _actors_for_full_run() -> list:
    return [
        tree_mod.Actor(id="a-701", title="Widget One", form_id=701, form_title="DEV_DTO_WIDGET"),
        tree_mod.Actor(id="a-702", title="Widget Two", form_id=702, form_title="DEV_DTO_WIDGET"),
        tree_mod.Actor(id="a-703", title="Thing One", form_id=703, form_title="PLATFORM_THING"),
        tree_mod.Actor(id="a-704", title="Broken One", form_id=704, form_title="Broken Form"),
        # form_id 0 must be skipped entirely.
        tree_mod.Actor(id="a-0", title="No Form", form_id=0, form_title=""),
    ]


def test_resolve_types_full_run():
    sim = _StubSim(
        forms={701: _widget_form_701(), 702: _widget_form_702(), 703: _platform_form_703()},
        raising={704},
    )
    ts, warnings = types_mod.resolve_types(sim, _actors_for_full_run(), concurrency=2)

    assert sorted(sim.calls) == [701, 702, 703, 704]

    # Common majority prefix "DEV_DTO_" stripped for the two widget forms,
    # collision disambiguated by form id (701 first, 702 gets the suffix).
    widget = ts.by_slug("widget")
    assert widget is not None
    assert widget.form_id == 701

    widget_702 = ts.by_slug("widget_702")
    assert widget_702 is not None
    assert widget_702.form_id == 702

    # PLATFORM_THING does not start with the shared prefix, so it keeps its
    # own full title.
    platform = ts.by_slug("platform_thing")
    assert platform is not None
    assert platform.form_id == 703

    # A form whose get_form call raised still gets a Type with zero fields.
    broken = ts.by_slug("broken_form")
    assert broken is not None
    assert broken.form_id == 704
    assert broken.fields == []
    assert any("form 704" in w and "Broken Form" in w for w in warnings)

    # ref placeholder resolved in-set.
    owner_ref = next(f for f in widget.fields if f.name == "ownerRef")
    assert owner_ref.type == "ref(widget_702)"

    # ref placeholder demoted to string when the target form is off the layer.
    out_ref = next(f for f in widget_702.fields if f.name == "outRef")
    assert out_ref.type == "string"

    # Fields with an empty id are dropped; duplicate slugs are disambiguated.
    assert len(platform.fields) == 3
    names = [f.name for f in platform.fields]
    assert "same_name" in names
    assert "same_name_2" in names


# ---------------------------------------------------------------------------
# (e) _resolve_refs in isolation
# ---------------------------------------------------------------------------


def test_resolve_refs_in_set_and_out_of_set():
    in_set = FieldSpec(name="mgr", id="mgr", type="ref:5")
    out_of_set = FieldSpec(name="other", id="other", type="ref:999")
    t1 = Type(slug="employee", form_id=5, fields=[in_set, out_of_set])
    t2 = Type(slug="dept", form_id=6, fields=[])
    ts = TypeSet(types=[t1, t2])

    types_mod._resolve_refs(ts)

    assert in_set.type == "ref(employee)"
    assert out_of_set.type == "string"
