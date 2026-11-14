# Diagnosis: "Could not load workflow runs" (2026-11-07)

## Symptom

After registering a repository in the React app, the runs list showed:

> Could not load workflow runs. Could not reach the DevPilot API. Check that the backend is
> running and allows requests from this page.

The backend was running (`/health` returned 200) and the repository registration — a
`POST` with JSON, which needs a CORS preflight — had just succeeded, so neither "backend
down" nor "CORS misconfigured" fits.

## What the message really means

The frontend shows that text whenever `fetch()` itself rejects (`frontend/src/api.ts`), i.e.
when the browser gives the page **no readable response**. That happens for a dead server or
a CORS rejection — but also for any **unhandled 500** from FastAPI:

* Starlette produces the 500 for an unhandled exception in its outermost middleware,
  *outside* `CORSMiddleware`, so the 500 has no `Access-Control-Allow-Origin` header.
* The browser therefore hides the response, and `fetch()` rejects like a network error.

Reproduced in isolation (Starlette + CORSMiddleware, request with `Origin: http://localhost:5173`):

| Endpoint behaviour | Status | `access-control-allow-origin` |
|---|---|---|
| handled error (`ApiError` → 404) | 404 | `http://localhost:5173` |
| unhandled exception | 500 | **missing** |

So the message was masking a **server crash** in `GET /repositories/{id}/runs`.

## Most likely crash: concurrent insert race

* `frontend/src/main.tsx` renders inside `<StrictMode>`. In development React mounts every
  effect twice, so `useResource(api.runs)` sends **two simultaneous** `GET .../runs`
  requests (the first is aborted in the browser, but the server still processes it). The
  dashboard also loads runs for every repository.
* The original `sync_recent_runs` did *SELECT the run → INSERT if missing → COMMIT*. Two
  concurrent requests for a newly registered repository both saw "missing", both inserted,
  and the second commit failed with a unique-key violation on `workflow_runs.id`
  (`psycopg.errors.UniqueViolation` / `sqlalchemy.exc.IntegrityError`) → unhandled 500.
* This matches the timing: it fails right after registering (no rows yet) and typically
  works on a later reload (rows exist, so both requests update instead of insert).

Confirm from the container logs from before the fix:

```powershell
docker compose logs backend --since 2h | Select-String -Pattern "UniqueViolation|IntegrityError|Traceback"
```

If that shows something else, the fixes below still apply and the new error handling will
print the real cause (see "How to see the real error now").

## Fixes

1. **Errors keep CORS headers** — `backend/app/middleware.py` catches unhandled exceptions
   *inside* the CORS middleware, logs the traceback, and returns
   `500 {"detail": "Internal server error. Details were logged on the server."}`. The
   frontend now shows that message instead of "Could not reach the DevPilot API", and the
   cause appears in `docker compose logs backend`.
2. **Race-free run storage** — `backend/app/services/runs.py` now writes each run with one
   `INSERT … ON CONFLICT (id) DO UPDATE`. This builds on the upsert already added locally
   (which used the PostgreSQL-only `insert`); it now picks the dialect so the SQLite test
   suite runs the same code, and reloads rows with `populate_existing` so the response
   never shows stale values.

Regression tests: `test_unhandled_error_keeps_cors_headers_and_contract_shape` and
`test_run_already_stored_by_another_request_is_updated_not_duplicated` in
`backend/tests/test_api.py`.

## Checked and ruled out

* **CORS configuration** — `http://localhost:5173` is allowed and the preflighted `POST`
  worked. `http://127.0.0.1:5173` was *not* allowed; it is now in the default
  `CORS_ORIGINS`, in case the app is opened via that address.
* **GitHub errors** (bad token, missing permission, unknown repo, rate limit, timeout) —
  these are mapped to handled errors (404/502/503/504) that do carry CORS headers, so they
  would have shown GitHub's actual message, not "Could not reach…".
* **Request timeout** — the frontend reports timeouts with a different message.

## How to see the real error now

```powershell
docker compose logs backend --tail 100
```

Look for `ERROR [devpilot] Unhandled error on GET /api/v1/...` followed by a traceback.
