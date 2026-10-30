# AI safety and provider state

The provider boundary has a deterministic fake for tests and an optional OpenAI Responses API transport using strict JSON Schema output, `store:false`, no tools, bounded request sizes, timeouts, redacted provider errors, and a protected key file. AI is **NOT_CONFIGURED** in production; no Codex login or credential is used.

The current token budget guard is process-local and suitable only for isolated/test calls. It is not a durable multi-worker billing ledger and does not calculate model cost. Before enabling a paid production model, implement database-backed reservation/settlement, explicit daily/monthly limits and model cost accounting, then provision an owner-controlled API credential. Do not enable AI tasks or use AI as policy authority until that gate is complete.

Job descriptions, company websites, ATS fields, uploaded documents and recruiter email are untrusted. Pass them as bounded data, never as system/developer policy. Do not include credentials, unrelated applications, database contents or hidden prompts in model context. Deterministic eligibility and submission rules remain authoritative. When AI is unavailable, matching falls back to deterministic evidence and uncertain tasks become owner tickets.

The public demo does not connect to OpenAI or any external model service.
