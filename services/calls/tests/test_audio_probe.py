import math
import struct

import pytest

from audio_probe import PCMProbe


def tone(milliseconds=40):
    count=48*milliseconds
    return struct.pack('<'+'h'*count,*(int(4000*math.sin(i*2*math.pi*440/48000)) for i in range(count)))


def test_probe_counts_both_audio_directions_and_measures_response_after_speech():
    probe=PCMProbe()
    probe.output(tone(),at=0)  # Unsolicited greeting is not a response sample.
    for at in (1,1.04,1.08):probe.input(tone(),at=at)
    probe.output(bytes(3840),at=1.5)  # Silence is not audible output.
    probe.output(tone(),at=1.68)
    probe.output(tone(),at=1.72)  # Same output burst, counted only once.
    result=probe.snapshot()
    assert result['input_pcm_ms']==pytest.approx(120)
    assert result['output_pcm_ms']==pytest.approx(160)
    assert result['input_active_ms']==pytest.approx(120)
    assert result['response_gap_ms']==pytest.approx([600])
    assert result['measurement']=='worker_pcm_energy_gap'
    assert result['recipient_playback_verified'] is False


def test_probe_excludes_short_noise_and_overlapping_speech():
    probe=PCMProbe()
    probe.input(tone(10),at=0)
    probe.output(tone(),at=.7)
    for at in (2,2.04,2.08):probe.input(tone(),at=at)
    probe.output(tone(),at=2.1)  # Human still speaking: no valid response gap.
    assert probe.snapshot()['response_gap_ms']==[]
    assert probe.snapshot()['overlapping_onsets']==1


def test_probe_retains_bounded_samples_without_audio_content():
    probe=PCMProbe(max_samples=2)
    for turn in range(3):
        for offset in (0,.04,.08):probe.input(tone(),at=turn*3+offset)
        probe.output(tone(),at=turn*3+.6)
    result=probe.snapshot()
    assert len(result['response_gap_ms'])==2
    assert result['response_samples_total']==3
    assert 'audio' not in result
