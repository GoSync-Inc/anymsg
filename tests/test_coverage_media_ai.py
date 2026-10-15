"""Integrity gaps from the file-storage requirement review; no model calls."""
from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest

from messaging_service.blobs import BlobWriter


@pytest.mark.parametrize('corruption', ['same-size', 'wrong-size', 'missing', 'symlink'])
def test_blob_read_verification_rejects_corrupt_original_without_replacing_it(tmp_path, corruption):
    writer = BlobWriter(tmp_path / 'cas', reserve_bytes=0)
    blob = writer.write([b'synthetic original'])
    path = writer.root / blob.digest
    assert writer.verify(blob).read_bytes() == b'synthetic original'
    if corruption == 'same-size':
        path.write_bytes(b'X' * blob.bytes)
    elif corruption == 'wrong-size':
        path.write_bytes(b'short')
    elif corruption == 'missing':
        path.unlink()
    else:
        path.unlink()
        target = tmp_path / 'target'; target.write_bytes(b'synthetic original')
        path.symlink_to(target)
    with pytest.raises(RuntimeError, match='blob_corrupt'):
        writer.verify(blob)
    with pytest.raises(ValueError, match='invalid_blob'):
        writer.verify(replace(blob, digest='../untrusted'))
    assert not list(writer.root.glob('.pending-*'))


@pytest.mark.parametrize('reported_duration', [1.25, float('nan'), 31.0])
def test_local_whisper_inference_is_cpu_only_no_download_and_normalizes_complete_segments(tmp_path, monkeypatch, reported_duration):
    from messaging_service import enrichment_providers as models
    model_dir = tmp_path / 'preinstalled-model'
    model_dir.mkdir()
    original = tmp_path / 'synthetic.ogg'
    original.write_bytes(b'synthetic audio; decoder and model replaced in this protocol test')
    before = original.read_bytes()
    calls = []
    class SyntheticWhisper:
        def __init__(self, path, **options):
            assert path == str(model_dir)
            assert options == {'device': 'cpu', 'compute_type': 'int8',
                               'cpu_threads': 2, 'local_files_only': True}
            calls.append('model')
        def transcribe(self, path, **options):
            assert path == str(original)
            assert options == {'beam_size': 3, 'condition_on_previous_text': False}
            calls.append('transcribe')
            return iter([SimpleNamespace(start=0.0, end=0.5, text=' First segment.'),
                         SimpleNamespace(start=0.5, end=1.25, text=' Last segment.')]), SimpleNamespace(duration=reported_duration)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=SyntheticWhisper))
    monkeypatch.setattr(models, 'audio_duration', lambda path, maximum: 1.25)
    profile = models.validate_profile({'provider': 'faster_whisper', 'model': str(model_dir),
                                      'max_audio_seconds': 30}, 'audio')
    if reported_duration != reported_duration or reported_duration > 30:
        with pytest.raises(models.ModelError, match='^audio_duration_limit$'):
            models.infer_audio(profile, original, 'audio/ogg')
    else:
        result = models.infer_audio(profile, original, 'audio/ogg')
        assert result['text'] == ' First segment.\n Last segment.'
        assert result['metadata']['segments'] == [
            {'start': 0.0, 'end': 0.5, 'text': ' First segment.'},
            {'start': 0.5, 'end': 1.25, 'text': ' Last segment.'}]
        assert result['metadata']['duration_seconds'] == 1.25
        assert result['metadata']['transcoded'] is False
        assert result['metadata']['audio_truncated'] is False
        assert result['metadata']['generated_by_model'] is True
    assert calls == ['model', 'transcribe']
    assert original.read_bytes() == before
