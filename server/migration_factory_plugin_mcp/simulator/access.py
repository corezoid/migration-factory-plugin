"""Grant a group view+modify (not remove) on one actor or one account pair.
Both routes take recursive=false explicitly: the platform's default is true,
and a grant that cascades is the right thing for a graph and the wrong thing
for a record, whose children — if it ever has any — nobody here asked to
share.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .actors import validate_actor_id

if TYPE_CHECKING:
    from .client import _ClientCore


def _share_rule(group_id: int) -> list[dict]:
    return [
        {
            "action": "create",
            "data": {
                "groupId": group_id,
                "privs": {"view": True, "modify": True, "remove": False},
            },
        }
    ]


class AccessMixin:
    def share_with_group(self: "_ClientCore", actor_id: str, group_id: int) -> None:
        """POST /access_rules/actor/{actor_id}?recursive=false."""
        validate_actor_id(actor_id)
        self._call(
            "POST",
            f"/access_rules/actor/{self._seg(actor_id)}",
            query={"recursive": "false"},
            body=_share_rule(group_id),
            decode=False,
        )

    def share_account_pair_with_group(self: "_ClientCore", name_id: str, currency_id: int, group_id: int) -> None:
        """POST /access_rules/account/{name_id}_{currency_id}?recursive=false.

        Access to money is enforced on the PAIR and never on the account row:
        bootstrapping the pair seeds access for the caller alone, and
        attaching the account to an actor seeds nothing at all. So without
        this the run's own key is the only identity that can see what it
        posted. The object is addressed by the pair's own id,
        `<name_id>_<currency_id>`; there is no route that takes the two
        apart.
        """
        if not name_id:
            raise ValueError("simulator: no account-name id to share the pair of")
        if currency_id <= 0:
            raise ValueError("simulator: no currency id to share the pair of")
        if group_id <= 0:
            raise ValueError("simulator: no group to share the pair with")
        self._call(
            "POST",
            f"/access_rules/account/{self._seg(name_id)}_{currency_id}",
            query={"recursive": "false"},
            body=_share_rule(group_id),
            decode=False,
        )
