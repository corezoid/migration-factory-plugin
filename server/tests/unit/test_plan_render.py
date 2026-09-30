import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import plan as plan_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.ops import Unrouted  # noqa: E402


def _build_plan() -> plan_mod.Plan:
    plan = plan_mod.Plan(
        layer_id="11111111-1111-4111-8111-111111111111",
        source_doc="invoice_2024.pdf",
        paths_from="graph.ids.json (2 nodes)",
        types_from="types.schema.yaml (1 type)",
    )

    update_action = plan_mod.Action(
        op_index=1,
        actor_id="emp-1",
        form_id=2,
        path="ACME > Finance > Employee #1",
        type="hrs_employee",
        title="Ivan",
        rename="Ivan Petrov",
        changes=[plan_mod.Change(field="role", key="role", value="CFO", old="", new="CFO")],
    )
    create_action = plan_mod.Action(
        op_index=2,
        path="ref:UA-EDRPOU-12345678",
        form_id=701,
        type="hrs_employee",
        create=True,
        ref="UA-EDRPOU-12345678",
        rename="New Counterparty LLC",
    )
    plan.actions = [update_action, create_action]
    plan.satisfied = [plan_mod.Satisfied(op_index=3, path="ACME > Finance > Employee #3")]

    at = "ACME > Bad Path"
    msg = f"no node matches {plan_mod._q(at)}"
    plan.errors.append(f"op #4  {plan_mod._q(at)}\n      {msg}")
    plan.errors.append('op #7\n      field "salary": "abc" is not a number')

    plan.unrouted = [
        Unrouted(
            text="some sentence from the source document",
            guess="Employee #4",
            reason="no matching node found",
        )
    ]
    return plan


EXPECTED = (
    "plan for layer 11111111-1111-4111-8111-111111111111  (source: invoice_2024.pdf)\n"
    "  1 record to create, 1 node to update, 1 rename, 1 op already applied, 2 errors\n"
    "  paths from graph.ids.json (2 nodes)\n"
    "  field schemas from types.schema.yaml (1 type)\n"
    "\n"
    '  ~ ACME > Finance > Employee #1  [hrs_employee]\n'
    '      title: "Ivan" -> "Ivan Petrov"\n'
    "      role: — -> \"CFO\"\n"
    "\n"
    "  * ref:UA-EDRPOU-12345678  [hrs_employee]  (new record of form 701, not on the layer)\n"
    '      title: — -> "New Counterparty LLC"\n'
    "\n"
    "  a new record is created as an actor of its form and is not placed on the\n"
    "  canvas: it will not appear in graph.values.yaml, and `ref:` plus the id stamped\n"
    "  into the ops file are the only handles on it. Nothing here undoes a create.\n"
    "\n"
    "  already applied — the layer holds these values:\n"
    "      ACME > Finance > Employee #3\n"
    "\n"
    "  2 errors:\n"
    '    op #4  "ACME > Bad Path"\n'
    '      no node matches "ACME > Bad Path"\n'
    "    op #7\n"
    '      field "salary": "abc" is not a number\n'
    "\n"
    "  unrouted — 1 fact matched no node; this is where the model has holes:\n"
    '      "some sentence from the source document"\n'
    "        guess: Employee #4 — no matching node found\n"
)


def test_render_matches_pinned_snapshot():
    plan = _build_plan()
    assert plan.render() == EXPECTED


def test_render_reflects_summary_counts():
    plan = _build_plan()
    rendered = plan.render()
    assert "1 record to create" in rendered
    assert "1 node to update" in rendered
    assert "1 rename" in rendered
    assert "1 op already applied" in rendered
    assert "2 errors" in rendered


def test_nothing_to_do_summary():
    plan = plan_mod.Plan(layer_id="L", source_doc="", paths_from="", types_from="")
    rendered = plan.render()
    assert "nothing to do" in rendered
    assert "(source: " not in rendered
