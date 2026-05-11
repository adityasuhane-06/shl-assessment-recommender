# SHL Assessment Recommender - Approach

I built a stateless FastAPI service with the required `GET /health` and `POST /chat` endpoints. Every `/chat` request carries the full conversation history, and the service reconstructs temporary state only from that payload. No per-conversation memory is stored on the server, which matches the replay-based evaluator.

Code: https://github.com/adityasuhane-06/shl-assessment-recommender  
API: https://adityasuhane01-shl-assessment-recommender.hf.space

The agent uses a lightweight linear workflow:

`retrieve -> generate -> validate`

I kept this LangGraph-inspired structure instead of a full graph runtime because the task flow is single-pass, stateless, and latency-sensitive. This keeps deployment simpler while preserving clear agent boundaries.

## Stack and Retrieval

I used FastAPI and Pydantic for API/schema enforcement, FAISS plus `sentence-transformers/all-MiniLM-L6-v2` for retrieval, and an OpenRouter-hosted LLM for reasoning. The OpenRouter model is configurable and currently defaults to `openai/gpt-oss-120b:free`; the service also supports OpenRouter backup keys and direct Gemini API fallback. This improves reliability without changing the API contract.

The 377 SHL catalog items are converted into searchable text using product name, description, assessment keys, job levels, languages, duration, and metadata. These embeddings are stored in a FAISS index. At runtime, retrieval uses recent user turns and conversation history, with alias expansion for common terms such as OPQ, GSA, DSI, SVAR, AWS, and Verify G+. I also scan previous user/assistant messages for already-mentioned assessments so refinement requests preserve the active shortlist instead of restarting.

## Prompting and Validation

The prompt restricts the assistant to SHL Individual Test assessment selection and supports the required behaviors: clarify vague requests, recommend 1-10 catalog assessments, refine an existing shortlist, compare assessments from catalog context, and refuse off-topic/legal/prompt-injection requests.

The LLM output is treated as a draft. A deterministic validator parses JSON, resolves each recommendation back to the catalog, corrects official names, URLs, and test types, drops non-catalog items, caps results at 10, and returns a schema-compliant fallback if the LLM times out or emits malformed JSON. This protects the hard evaluator from schema drift and hallucinated URLs.

## Evaluation and Iteration

I built local tests around the 10 public conversations plus additional hidden-style probes for unseen roles and behaviors. The checks cover schema compliance, exact catalog URL membership, turn cap, Recall@10, vague-query clarification, refinement, comparison, off-topic refusal, and prompt-injection resistance.

Current results:

- Public traces: 100.0% Mean Recall@10
- Hard checks: 0 schema errors, 0 hallucinated URLs, 0 turn-cap violations
- Behavior probes: 7/7 passed
- Hidden-style checks: 11/11 passed
- Deployed smoke tests: 5/5 passed with catalog-valid URLs

What did not work initially: pure semantic retrieval missed products during refinement turns, and weak URL-prefix checks could still allow hallucinations. I fixed these with history scanning, alias expansion, exact catalog validation, scenario anchors, timeout handling, and provider fallback. I used AI coding assistance for implementation, review, and evaluation-script generation, while manually checking catalog fields, failure cases, and final behavior.
