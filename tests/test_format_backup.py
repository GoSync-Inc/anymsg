"""Formatting artifacts participate in the existing native backup inventory."""
from messaging_service.backup import database_manifest


class _Cursor:
    def __init__(self, record): self.record = record
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, _): pass
    def __iter__(self): return iter([{'record': self.record}])


class _Connection:
    def __init__(self, artifact): self.artifact = artifact
    def execute(self, _): return self
    def fetchone(self): return {'value': '24'}
    def cursor(self, *, name):
        return _Cursor({'id': 'synthetic', 'render_artifact': self.artifact}
                       if name == 'backup_outgoing_messages' else {})


def test_schema24_backup_hashes_the_persisted_render_artifact():
    before = database_manifest(_Connection({'provider_payload': {'text': '*hello*'}}))
    after = database_manifest(_Connection({'provider_payload': {'text': '*changed*'}}))
    assert len(before) == 45
    assert before['outgoing_messages']['sha256'] != after['outgoing_messages']['sha256']
    assert before['messages'] == after['messages']
