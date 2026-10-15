"""Versioned integration metadata; domain payloads remain owned by their module."""

from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class Capability:
    module: str
    operation: str
    revision: int
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1 or self.module not in {
            "gateway",
            "messaging",
            "calendar",
            "calls",
        }:
            raise ValueError("unsupported_contract")
        if not self.operation or type(self.revision) is not int or self.revision < 1:
            raise ValueError("invalid_capability")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ReceiptReference:
    module: str
    operation: str
    receipt_id: str
    request_id: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        Capability(self.module, self.operation, 1, self.schema_version)
        if not self.receipt_id or (self.request_id is not None and not self.request_id):
            raise ValueError("invalid_receipt_reference")

    def to_dict(self):
        return asdict(self)


def safe_error(code, *, unknown_outcome=False):
    if (
        not isinstance(code, str)
        or not code
        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in code)
    ):
        raise ValueError("invalid_error_code")
    return {
        "schema_version": 1,
        "error": code,
        "outcome": "unknown" if unknown_outcome else "rejected",
        "automatic_retry": False,
    }


def decode(kind, raw):
    if kind not in (Capability, ReceiptReference):
        raise ValueError("unknown_contract")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported_contract")
    return kind(**value)
