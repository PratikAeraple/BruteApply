# AI safety

AI is optional and has no production credential configured. A provider abstraction may extract structured fields, explain job matches or draft documents, but its output is untrusted and cannot authorize an application or override a deterministic blocker.

Job descriptions, company websites, ATS fields, uploaded content and recruiter email are untrusted. They may include prompt-injection instructions. Pass them as bounded data, never as system/developer policy. Do not include credentials, unrelated applications, database contents, or hidden prompts in a model context. Validate every structured response, track usage against daily/monthly limits, and degrade to deterministic matching/manual tickets when the provider is unavailable.

The public demo does not connect to OpenAI or any external model service.
