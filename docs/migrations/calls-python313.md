# Calls runtime compatibility

The imported Python 3.12 manifest is preserved in the source import commit. A separate
3.13 resolution retained Pipecat 1.12.0, NTgCalls 3.0.3 and the Google/WebRTC/Silero
features; `audioop-lts` is selected for Python 3.13. Runtime state is not part of this
change. MCP 2.3.0, FastAPI 0.143.0, Starlette 1.8.0 and Uvicorn 0.54.0 match the gateway.

On macOS arm64, all 197 imported offline tests passed with the candidate environment.
`test_runtime_imports.py` additionally imports the real native modules in a fresh
process and checks that the local AnyMsg adapter has not been replaced by the SDK.
CI repeats the imports and full suite on Linux amd64. Neither package resolution nor
these synthetic tests claim a successful live conversation or enabled provider route.
