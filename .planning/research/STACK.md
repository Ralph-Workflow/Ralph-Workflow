# Stack Research

**Domain:** Agentic planning workflow systems for GLM coding agents
**Researched:** 2026-03-05
**Confidence:** MEDIUM-HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| Python | 3.12.x | Runtime for orchestration services and workers | Most mature choice for 2025-era agent stacks; broad SDK support across LangGraph, LiteLLM, and observability tooling while avoiding bleeding-edge runtime breakage. | HIGH |
| LangGraph | 1.0.10 | Stateful agent workflow orchestration | Purpose-built for long-running, stateful agent graphs with checkpointing, durable execution, and human-in-the-loop recovery. This maps directly to planning workflows that must pause/resume cleanly. | HIGH |
| LiteLLM (Proxy + SDK) | 1.81.x stable (or 1.82.x after qualification) | Unified model gateway for GLM and fallback models | Gives one OpenAI-compatible surface for routing, retries, model fallback, and provider abstraction. Critical to avoid hard-coding a single model vendor into planner logic. | HIGH |
| FastAPI | 0.12x (0.128+ line) | Control-plane API for plans, runs, and approvals | Standard Python API layer for async orchestration systems; integrates cleanly with Pydantic schemas and worker backends. | HIGH |
| PostgreSQL + pgvector | PostgreSQL 17 + pgvector 0.8.2 | Durable run state, plan artifacts, embeddings/hybrid retrieval | PostgreSQL remains the default transactional backbone; pgvector keeps retrieval colocated with planning metadata and audit state (no premature split brain between DBs). | HIGH |

### Supporting Libraries

| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| zhipuai | 2.1.5.20250825 | Native GLM SDK for provider-specific features | Use when GLM-native features are needed beyond OpenAI-compatible routing (streaming quirks, provider-specific tool semantics). | MEDIUM |
| Pydantic | 2.12.5 | Typed contracts for planner state, tool I/O, and artifacts | Use everywhere schema boundaries exist (API, persistence, queue payloads). Avoid ad-hoc dicts for run state. | HIGH |
| langfuse | 3.14.5 | LLM tracing, prompt/version tracking, evals | Use from day one for trace-level debugging and regression visibility in planning quality. | HIGH |
| redis (server) + redis-py | Redis 7.x + redis-py 6.4.0 | Ephemeral queueing, rate-limit counters, short-lived caches | Use for hot-path coordination only; keep source-of-truth run state in PostgreSQL. | MEDIUM |
| Celery (optional) | 5.4.x | Background fan-out for non-graph tasks | Use only if you need high-volume async jobs outside LangGraph's own execution flow. | LOW |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv (Astral) | Python env + dependency management + lockfiles | Use `uv` as the default package/workflow manager for reproducible local + CI installs. |
| Ruff | Lint + format | Fast enough to run on every commit; enforce import/order/style consistency for agent codebases. |
| pytest | Tests (unit + integration) | Keep deterministic tests for reducers/planner transitions; add provider-recorded fixtures for model calls. |
| Docker Compose | Local infra parity | Run PostgreSQL, Redis, LiteLLM proxy, and observability stack locally with pinned tags. |

## Installation

```bash
# Python and environment
uv python install 3.12
uv venv --python 3.12

# Core runtime
uv add langgraph==1.0.10 litellm==1.81.14 fastapi==0.128.8 pydantic==2.12.5

# GLM + storage + observability
uv add zhipuai==2.1.5.20250825 langfuse==3.14.5 redis==6.4.0 psycopg[binary]==3.2.6

# Dev dependencies
uv add --dev pytest ruff
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| LangGraph | OpenAI Agents SDK | Use OpenAI Agents SDK when your workflow is tightly bound to OpenAI-native primitives and you do not need graph-level state-machine control. |
| LiteLLM gateway | Direct vendor SDK only (zhipuai-only) | Use direct SDK only for small single-provider prototypes where portability and multi-model routing are explicitly out of scope. |
| PostgreSQL + pgvector | Dedicated vector DB first | Use a dedicated vector DB only when retrieval scale or ANN latency requirements exceed what tuned pgvector can support. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Pydantic v1 in new code | V1 compatibility is brittle with modern Python (including 3.14-related caveats) and increases migration risk. | Pydantic v2.12.x+ |
| Redis as primary run-state store | In-memory-first state increases data-loss/replay risk and weakens auditability for planning workflows. | PostgreSQL as source of truth + Redis as cache/queue |
| Single-vendor orchestration abstractions | Hard vendor coupling blocks GLM fallback/routing strategy and future model shifts. | LangGraph + LiteLLM abstraction boundary |
| “No-checkpoint” agent loops | Stateless loops fail poorly on retries, restarts, and human approval gates. | LangGraph checkpointers + durable execution |

## Stack Patterns by Variant

**If you are GLM-primary with occasional non-GLM fallback:**
- Use LiteLLM as the single model interface and route GLM as default.
- Because planner logic stays provider-agnostic while preserving emergency fallback paths.

**If you need strict auditability for enterprise planning workflows:**
- Use PostgreSQL event/run tables + Langfuse traces + immutable artifact snapshots.
- Because reproducibility and postmortem debugging matter more than minimal infra.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| langgraph 1.0.x | Python 3.10+ environments | Prefer Python 3.12 for ecosystem stability and package availability. |
| pydantic 2.12.x | fastapi 0.12x line | Stable modern baseline; avoid mixing Pydantic v1 in the same service. |
| langfuse-python 3.14.x | langfuse server v3.151+ | Keep SDK/server aligned; v4 SDK is currently beta. |
| pgvector 0.8.2 | PostgreSQL 13+ (recommended 17) | Use PostgreSQL 17 for better performance and smoother upgrade path. |

## Sources

- Context7 `/langchain-ai/langgraph/1.0.8` - durable execution, memory, checkpointer model
- Context7 `/berriai/litellm` - OpenAI-compatible routing and proxy configuration patterns
- Context7 `/langfuse/langfuse-docs` - tracing/evals/prompt-management capabilities
- FastAPI release notes: https://fastapi.tiangolo.com/release-notes/
- LangGraph releases: https://github.com/langchain-ai/langgraph/releases
- LiteLLM releases: https://github.com/BerriAI/litellm/releases
- PostgreSQL 17 release announcement: https://www.postgresql.org/about/news/postgresql-17-released-2936/
- pgvector README (install branch/version and Postgres compatibility): https://raw.githubusercontent.com/pgvector/pgvector/master/README.md
- zhipuai package metadata and release history: https://pypi.org/project/zhipuai/
- Pydantic docs/version marker + releases: https://docs.pydantic.dev/latest/ and https://github.com/pydantic/pydantic/releases
- Langfuse Python releases (stable vs beta signal): https://github.com/langfuse/langfuse-python/releases
- uv releases: https://github.com/astral-sh/uv/releases

---
*Stack research for: GLM-agent planning workflow systems*
*Researched: 2026-03-05*
