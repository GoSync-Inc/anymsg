from platform_contracts import ReceiptReference


def receipt_reference(operation, result):
    """Validate a reference without copying transcript data into platform storage."""
    identity = result.get("id") or result.get("call_id")
    return ReceiptReference("calls", operation, identity) if isinstance(identity, str) and identity else None
