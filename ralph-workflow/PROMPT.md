<!-- ralph:starter-prompt: edit this file before running `ralph` -->

PROMPT.md is the goal and acceptance-criteria document that Ralph Workflow reads as its task input. Replace the example content below with YOUR task description, then remove the sentinel comment at the top before running `ralph`.

# Goal

Add a /health endpoint to the example API that returns HTTP 200 with a JSON body `{"status": "ok"}`.
This endpoint should be unauthenticated and return a Content-Type of application/json.
It is used by load balancers and uptime monitors to verify the service is running.

## Context

- Main API entry point: `src/api/app.py`
- Existing route examples: `src/api/routes/`
- Dependencies and external services: see `README.md`

## Acceptance criteria

- GET /health returns HTTP 200
- Response body is valid JSON with `status` == `ok`
- A new test in `tests/` covers the new endpoint

## Notes

- Keep the prompt scoped — one user-visible outcome per run works best.
- Describe constraints (language, framework, test style) in Context above.

---

**Next steps**

1. Edit the sections above to describe YOUR task and remove the sentinel comment.
2. Run `ralph --diagnose` to verify agents, MCP servers, and config.
3. Run `ralph` to start the planning → development → review → fix pipeline.
