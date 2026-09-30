"""The slice of the accounts API needed to record money on an actor:
bootstrap the workspace (account-name, currency) pair so the caller has
access to it, attach the account to the actor, and post transactions
against it.

The shape to know before reading further: one (name, currency) pair on an
actor is TWO rows, each with its own account id — one incomeType "debit",
one "credit". A transaction carries no direction of its own; the side is
decided entirely by which of the two ids it is posted to, and the card
totals the pair as credit minus debit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from .actors import validate_actor_id
from .types import decode_item, decode_list

if TYPE_CHECKING:
    from .client import _ClientCore

INCOME_TYPE_DEBIT = "debit"
INCOME_TYPE_CREDIT = "credit"


@dataclass
class AccountSides:
    """Both ids of one (name, currency) pair on one actor."""

    debit: str = ""
    credit: str = ""

    def complete(self) -> bool:
        return bool(self.debit and self.credit)


@dataclass
class Account:
    """One side of one account on an actor."""

    id: str
    name_id: str
    currency_id: int
    income_type: str

    @classmethod
    def from_json(cls, d: dict) -> "Account":
        return cls(
            id=d.get("id", "") or "",
            name_id=d.get("nameId", "") or "",
            currency_id=d.get("currencyId", 0) or 0,
            income_type=d.get("incomeType", "") or "",
        )


def _sides_of(accounts: list[Account], name_id: str, currency_id: int) -> AccountSides:
    """Picks both rows of a pair out of an actor's accounts. The gateway
    lists the two in no fixed order, so each is matched on its income_type
    rather than on position — taking whichever came first is how half a
    ledger ends up on the wrong side, displayed negated."""
    sides = AccountSides()
    for a in accounts:
        if a.name_id != name_id or a.currency_id != currency_id:
            continue
        if a.income_type == INCOME_TYPE_DEBIT:
            sides.debit = a.id
        elif a.income_type == INCOME_TYPE_CREDIT:
            sides.credit = a.id
    return sides


class FinanceMixin:
    def ensure_actor_account(
        self: "_ClientCore",
        actor_id: str,
        *,
        name_id: str,
        currency_id: int,
        account_type: str = "",
        search: bool = False,
    ) -> AccountSides:
        """Makes sure an actor carries the pair and returns both sides. The
        create is idempotent (ignoreIfExist) and answers with the actor's
        accounts, so the wanted rows are usually among them; the endpoint
        does not always echo an account that already existed, so that case
        reads it back. Safe to call on every visit.
        """
        validate_actor_id(actor_id)
        body: dict[str, Any] = {"nameId": name_id, "currencyId": currency_id}
        if account_type:
            body["accountType"] = account_type
        if search:
            body["search"] = search
        payload = self._call(
            "POST",
            f"/accounts/{self._seg(actor_id)}",
            query={"ignoreIfExist": "true"},
            body=body,
        )
        created = [Account.from_json(a) for a in decode_list(payload)]
        sides = _sides_of(created, name_id, currency_id)
        if sides.complete():
            return sides

        # The create did not echo the accounts (they already existed); read back.
        accounts = self.get_actor_accounts(actor_id)
        sides = _sides_of(accounts, name_id, currency_id)
        if sides.complete():
            return sides

        raise RuntimeError(
            f"simulator: account {name_id}/{currency_id} not present on actor {actor_id} after ensure"
        )

    def get_actor_accounts(self: "_ClientCore", actor_id: str) -> list[Account]:
        """GET /accounts/{actor_id}?limit=100 — the listing is paginated
        (default ~20) and an actor can carry many accounts, so ask for the
        whole page explicitly."""
        validate_actor_id(actor_id)
        payload = self._get(f"/accounts/{self._seg(actor_id)}", {"limit": "100"})
        return [Account.from_json(a) for a in decode_list(payload)]

    def ensure_account_pair(
        self: "_ClientCore", workspace_id: str, account_name: str, currency_name: str
    ) -> tuple[str, int]:
        """Bootstraps the workspace (account-name, currency) pair and grants
        the caller access to it, returning the two ids an account is built
        from. This is the step that makes later transaction calls work:
        access is enforced on the pair, and attaching the account alone never
        seeds it — so without this bootstrap a non-owner key gets 403 on
        every transaction. The name and the currency are created if missing;
        safe to repeat. No pre-check — the route creates-or-returns
        idempotently; currency resolved by exact case-sensitive name match.
        """
        if not workspace_id:
            raise ValueError("simulator: no workspace for the account pair: set SIM_WORKSPACE_ID")
        payload = self._call(
            "POST",
            f"/accounts/pair/{self._seg(workspace_id)}",
            body={"accountName": account_name, "currencyName": currency_name},
        )
        item = decode_item(payload)
        name_id = (item.get("accountName") or {}).get("id", "") or ""
        currency_id = (item.get("currency") or {}).get("id", 0) or 0
        return name_id, currency_id

    def create_transaction(
        self: "_ClientCore",
        account_id: str,
        *,
        amount: float,
        comment: str = "",
        ref: str = "",
        data: Optional[dict] = None,
        original_date: int = 0,
    ) -> int:
        """POST /transactions/{account_id}. Records a value on one side of an
        account; the account's pair access must already be seeded (see
        ensure_account_pair) or the call is denied.

        original_date is when the movement actually happened, in epoch
        MILLISECONDS. Left out, the platform stamps the transaction with the
        moment of the call.
        """
        body: dict[str, Any] = {"amount": amount}
        if comment:
            body["comment"] = comment
        if ref:
            body["ref"] = ref
        if data:
            body["data"] = data
        if original_date:
            body["originalDate"] = original_date
        payload = self._call("POST", f"/transactions/{self._seg(account_id)}", body=body)
        item = decode_item(payload)
        return int(item.get("id", 0) or 0)
