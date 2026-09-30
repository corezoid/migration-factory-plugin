"""result.json — the tally a write leaves beside its ops file: how many
placeholder holes were filled, actors updated and created, and (folded in by
post_statement) what was posted. The keys are the Russian phrases the report
is read by, and are a hard on-disk contract — do not change them without
migrating every existing result.json this has ever written.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

RESULT_FILE_NAME = "result.json"


@dataclass
class StatementTurnover:
    currency: str
    debit: float = 0.0
    credit: float = 0.0

    def to_json(self) -> dict:
        return {"валюта": self.currency, "дебет": self.debit, "кредит": self.credit}

    @staticmethod
    def from_json(d: dict) -> "StatementTurnover":
        return StatementTurnover(
            currency=d.get("валюта", ""), debit=d.get("дебет", 0.0), credit=d.get("кредит", 0.0)
        )


@dataclass
class StatementRecord:
    ref: str
    file: str = ""
    actor_id: str = ""
    account: str = ""
    transactions: int = 0
    failed: int = 0
    turnover: list[StatementTurnover] = field(default_factory=list)
    # Set instead of / beside actor_id when the file was posted with
    # actor_field/actor_type: the distinct actors it actually resolved to, so
    # a reader of result.json is not left with actor_id as a placeholder
    # label ("field:client.iban") and nothing that says who was really paid.
    actors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = {
            "ref": self.ref,
            "файл": self.file,
            "актор": self.actor_id,
            "счет": self.account,
            "транзакций": self.transactions,
            "обороты": [t.to_json() for t in self.turnover],
        }
        if self.actors:
            d["акторы"] = self.actors
        if self.failed:
            d["не проведено"] = self.failed
        return d

    @staticmethod
    def from_json(d: dict) -> "StatementRecord":
        return StatementRecord(
            ref=d.get("ref", ""),
            file=d.get("файл", ""),
            actor_id=d.get("актор", ""),
            account=d.get("счет", ""),
            transactions=d.get("транзакций", 0),
            failed=d.get("не проведено", 0),
            turnover=[StatementTurnover.from_json(t) for t in d.get("обороты", []) or []],
            actors=list(d.get("акторы", []) or []),
        )


class AppliedAction(Protocol):
    """What Add() needs from a graph.apply.Action — duck-typed so this module
    never imports apply.py (apply.py imports this one)."""

    actor_id: str
    create: bool
    fills_hole: bool


@dataclass
class RunResult:
    holes_filled: int = 0
    actors_updated: int = 0
    actors_created: int = 0
    transactions_posted: int = 0
    holes: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    statements: list[StatementRecord] = field(default_factory=list)

    def _known_ids(self) -> set[str]:
        return set(self.holes) | set(self.updated) | set(self.created)

    def add(self, applied: Iterable[AppliedAction]) -> int:
        """Folds this run's applied actions into three disjoint id lists,
        precedence Created > Holes(fills_hole) > Updated, deduped across all
        three so replaying an unchanged ops file adds nothing. Returns the
        count of ids newly seen (can be less than len(applied) when a later
        run re-touches a node an earlier run already tallied)."""
        added = 0
        known = self._known_ids()
        for action in applied:
            if action.actor_id in known:
                continue
            if action.create:
                self.created.append(action.actor_id)
            elif action.fills_hole:
                self.holes.append(action.actor_id)
            else:
                self.updated.append(action.actor_id)
            known.add(action.actor_id)
            added += 1
        self._recount()
        return added

    def add_statement(self, rec: StatementRecord) -> bool:
        """Replaces any existing entry with the same ref in place, rather than
        appending a duplicate; returns whether the entry was new."""
        for i, existing in enumerate(self.statements):
            if existing.ref == rec.ref:
                self.statements[i] = rec
                self._recount()
                return False
        self.statements.append(rec)
        self._recount()
        return True

    def _recount(self) -> None:
        self.holes_filled = len(self.holes)
        self.actors_updated = len(self.updated)
        self.actors_created = len(self.created)
        self.transactions_posted = sum(s.transactions for s in self.statements)

    def to_json(self) -> dict:
        self._recount()
        return {
            "количество заполненных дырок": self.holes_filled,
            "количество обновленных акторов": self.actors_updated,
            "количество созданных акторов": self.actors_created,
            "количество проведенных транзакций": self.transactions_posted,
            "заполненные дырки": self.holes,
            "обновленные акторы": self.updated,
            "созданные акторы": self.created,
            "проведенные выписки": [s.to_json() for s in self.statements],
        }

    @staticmethod
    def from_json(d: dict) -> "RunResult":
        res = RunResult(
            holes=list(d.get("заполненные дырки", []) or []),
            updated=list(d.get("обновленные акторы", []) or []),
            created=list(d.get("созданные акторы", []) or []),
            statements=[StatementRecord.from_json(s) for s in d.get("проведенные выписки", []) or []],
        )
        res._recount()
        return res


def load_result(path: str) -> RunResult:
    """Missing file is not an error — the first apply in a fresh directory
    creates it."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return RunResult()
    return RunResult.from_json(data)


def write_result(path: str, res: RunResult) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res.to_json(), f, ensure_ascii=False, indent=4)
        f.write("\n")


def statement_ref(prefix: str, actor_id: str, account: str, file_path: str) -> str:
    """Identity key for a posted statement — a different prefix is a
    deliberate second posting of the same statement, not a duplicate."""
    return "|".join([prefix or "stmt", actor_id, account, os.path.basename(file_path)])


def result_path(ops_path: str = "", from_dir: str = "") -> str:
    if ops_path:
        return os.path.join(os.path.dirname(ops_path), RESULT_FILE_NAME)
    if from_dir:
        return os.path.join(from_dir, RESULT_FILE_NAME)
    return ""
