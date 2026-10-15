"""Synthetic private-directory admission checks for the durable capture spool."""
import json
import stat

import pytest

from messaging_service.spool import DurableSpool


@pytest.mark.parametrize('mode', [0o755, 0o770, 0o777])
def test_existing_nonprivate_spool_is_rejected_before_publication(tmp_path, mode):
    directory = tmp_path / 'existing-spool'
    directory.mkdir(mode=0o700)
    directory.chmod(mode)
    with pytest.raises(ValueError, match='^private_spool_required$'):
        DurableSpool(directory, reserve_bytes=0, validator=lambda value: value)
    assert list(directory.iterdir()) == []
    assert stat.S_IMODE(directory.stat().st_mode) == mode  # Do not silently repair an existing source.


def test_private_spool_publishes_private_original_and_preserves_payload(tmp_path):
    directory = tmp_path / 'private-spool'
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    spool = DurableSpool(directory, reserve_bytes=0, validator=lambda value: value)
    expected = {'kind': 'synthetic', 'body': 'Fixture message'}
    name = spool.put(expected)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
    assert json.loads((directory / name).read_text()) == expected
