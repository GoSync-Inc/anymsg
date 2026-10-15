class CalendarError(Exception):
    """Public fixed error code; never include provider payloads or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
