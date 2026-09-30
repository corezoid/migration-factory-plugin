"""Python port of the Go `internal/simulator` client: a small typed client
for the Simulator.Company public API. See client.py for the entry point.
"""
from __future__ import annotations

from .access import AccessMixin
from .actors import (
    ACTOR_LIST_FILTER,
    ACTOR_PAGE_LIMIT,
    ACTOR_SUMMARY_FILTER,
    FORM_WITH_FIELDS_FILTER,
    ActorsMixin,
    validate_actor_id,
)
from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    SCHEME_BEARER,
    SimulatorClient,
    normalize_base_url,
    seg,
)
from .errors import (
    NoCredentialError,
    SimulatorError,
    is_bad_request,
    is_conflict,
    is_duplicate_ref,
    is_not_found,
    parse_error,
    status_code,
)
from .finance import (
    INCOME_TYPE_CREDIT,
    INCOME_TYPE_DEBIT,
    Account,
    AccountSides,
    FinanceMixin,
)
from .layers import (
    LAYER_PAGE_LIMIT,
    MAX_LAYER_PAGES,
    LayerActor,
    LayerEdge,
    LayerPosition,
    LayersMixin,
)
from .storage import UPLOAD_STATUS_CLEAN, StorageMixin, Upload
from .types import (
    Actor,
    Field,
    FieldOption,
    Form,
    Section,
    decode_item,
    decode_list,
    resolved_form_id,
)

__all__ = [
    # client
    "SimulatorClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "SCHEME_BEARER",
    "normalize_base_url",
    "seg",
    # errors
    "NoCredentialError",
    "SimulatorError",
    "parse_error",
    "status_code",
    "is_not_found",
    "is_bad_request",
    "is_conflict",
    "is_duplicate_ref",
    # actors
    "ACTOR_LIST_FILTER",
    "ACTOR_SUMMARY_FILTER",
    "FORM_WITH_FIELDS_FILTER",
    "ACTOR_PAGE_LIMIT",
    "validate_actor_id",
    "ActorsMixin",
    # access
    "AccessMixin",
    # finance
    "INCOME_TYPE_DEBIT",
    "INCOME_TYPE_CREDIT",
    "Account",
    "AccountSides",
    "FinanceMixin",
    # layers
    "LAYER_PAGE_LIMIT",
    "MAX_LAYER_PAGES",
    "LayerActor",
    "LayerEdge",
    "LayerPosition",
    "LayersMixin",
    # storage
    "UPLOAD_STATUS_CLEAN",
    "Upload",
    "StorageMixin",
    # types
    "Actor",
    "Form",
    "Section",
    "Field",
    "FieldOption",
    "decode_item",
    "decode_list",
    "resolved_form_id",
]
