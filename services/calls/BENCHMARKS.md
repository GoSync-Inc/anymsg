# Speed of the ordinary STT → LLM → TTS chain

Checked on 14 October 2026. In Pipecat, speed depends on the chosen services, the end of the speech turn, streaming synthesis and the network. Measurements of the full chain and of the components are given separately below.

**Full chain.** In Modal's publication of 4 November 2025, Pipecat + Parakeet-tdt-0.6b-v3 + Qwen3-4B-Instruct-2507/vLLM + Kokoro TTS, with documentation search, reached a median of about **1 second** from the end of the person's speech to an audible reply. The client, bot and models were close to each other in the Bay Area; WebRTC and Modal Tunnels were used. The measurement was made from a recording of the conversation and included hardware. This is the result of a specific installation, not an SLA and not a check of our three languages. [Modal's method and result](https://modal.com/blog/low-latency-voice-bot).

**Recognition.** Pipecat benchmark: 24 models, the same 1000 audio fragments. TTFS measures the time from the actual end of speech to the final transcription, minus VAD's own wait. [Speech-to-text benchmark](https://www.pipecat.ai/benchmarks/speech-to-text).

| STT | p50 | p95 |
|---|---:|---:|
| Deepgram nova-3-general | 247 ms | 298 ms |
| Soniox stt-rt-v4 | 249 ms | 281 ms |

**Text model.** Voice Readiness checks 46 configurations on 30 turns with tools, instruction following and grounding in knowledge. TTFAT is from the start of the request to the first response token or tool call output, without separately transmitted reasoning; the first response of a conversation is excluded. [Voice Readiness](https://www.pipecat.ai/benchmarks/voice-readiness).

| LLM / service | p50 TTFAT | p95 | Successful turns |
|---|---:|---:|---:|
| Qwen3.8-27B FP8 / Baseten | 649 ms | 801 ms | 98.2% |
| Claude Haiku 4.5 / Anthropic | 637 ms | 1615 ms | 98.0% |
| Gemini 3.6 Flash, minimal / AI Studio | 798 ms | 984 ms | 97.1% |

The authors give a guideline: an optimized voice chain adds about 500 ms on top of the LLM. This is an estimate, not a measurement of any combination. Medians of different experiments cannot be added and passed off as a measured delay of a full conversation; TTS waits for enough text, and parts of the processing can run in parallel.

**Conversations with tools.** In [PhoneBench Alpha 1](https://www.pipecat.ai/benchmarks/phonebench-alpha-1) 15 text models are assessed separately on phone-service scenarios. PhoneLLM 30B Alpha 1: p50 of the first token 331 ms, p95 about 600 ms, quality score 72.3%; Gemini 3.6 Flash: 1168/1468 ms, quality 78.6%. This is one more LLM test, not the delay of the whole audio chain; quality percentages cannot be compared directly with Voice Readiness. The full method is promised to be published as the benchmark develops.

For our comparison we need one browser, the same audio utterances, the same tools and hosting regions. The measure should be end of speech → first received sound, p50/p95 and the stopping of sound on interruption; separately assess tool accuracy and the preservation of an unfinished story. The public results above do not replace checking Russian, English and Indonesian in our scenario.
