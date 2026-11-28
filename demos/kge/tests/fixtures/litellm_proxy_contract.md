# Offline LiteLLM-shaped proxy fixture

`tests.litellm_proxy_fixture` is a local HTTP stand-in for future connector tests. It does not implement LiteLLM or a KGE connector. It never contacts a deployment or model and adds no provider SDK or credentials to the product.

A test scripts responses for `POST /v1/chat/completions` and `POST /utils/token_counter`; each accepted call consumes the next matching response. Requests require a test-only bearer. The fixture retains decoded bodies in memory for request-equality assertions but does not log the bearer or request bodies. It can return structured JSON, an arbitrary status, byte-split SSE frames, or an interrupted stream. Unknown or unauthenticated calls do not consume a scripted response.

Counter values and their `fixture-scripted` source are invented by a test. They demonstrate enforcement and re-counting scenarios; they do not assert that a LiteLLM deployment has provider-exact counting. A future connector must establish and document its actual supported endpoint, version and count provenance, and refuse or use a documented conservative policy where that cannot be established.

The fixture can express interleaved OpenAI-style tool-call argument fragments and opaque identifiers, optional signature metadata, usage-only events and omitted usage. Its own tests verify those bytes and the KGE port data types. They do not prove that the connector parses them, that an actual LiteLLM version supports them, or that live Gemini signatures, pricing, routing or answers work. Connector acceptance must exercise the factory and model port against this stand-in, then the owner can validate a real deployment separately.
