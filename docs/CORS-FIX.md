# Public REST CORS Fix — Runbook

**Status:** code half applied in-repo (`rest_api.py`); reverse-proxy half is a one-time
manual VPS step (below). Track until the live `radiantcore.org/api` returns exactly one
`Access-Control-Allow-Origin` header and answers preflights with `2xx`.

## Symptom

Browser dapps calling `https://radiantcore.org/api/wave/*` (or any public REST route)
fail with a CORS error, while the identical request succeeds in `curl`/Node. Two causes,
both verified 2026-06-30:

1. **Duplicate `Access-Control-Allow-Origin`.** The live response carries **two** ACAO
   headers — `*` (from FastAPI) **and** `https://radiantcore.org` (hardcoded in Caddy).
   Browsers reject any response whose ACAO has more than one value. `curl`/Node don't
   enforce CORS, so the bug is invisible from the shell.
2. **Preflight `OPTIONS` → 401.** `_security_middleware` required an API key for every
   non-GET method, including the CORS preflight `OPTIONS`, so preflighted requests
   (custom headers / POST) 401'd before `CORSMiddleware` could answer.

## Fix — Part A: FastAPI (DONE, in repo)

`electrumx/server/rest_api.py`, in `_security_middleware`, lets the preflight through to
`CORSMiddleware`:

```python
if request.method == 'OPTIONS':
    return await call_next(request)
```

FastAPI already emits the single correct `Access-Control-Allow-Origin: *` via
`CORSMiddleware` (driven by the `ALLOWED_ORIGINS` env var; `*` = fully public read API).
No further app-code change is needed. **This only goes live on the VPS after a redeploy
of the rxindexer container.**

## Fix — Part B: Caddy (MANUAL, on the VPS)

The second ACAO header is injected by the reverse proxy, **not** by this repo, so it must
be removed on the host. The live Caddyfile is on the VPS (not version-controlled here).

1. SSH to the VPS (see `~/Desktop/Misc Ecosystem Documents/VPS.md` for host/key).
2. Find the CORS header injection in the `radiantcore.org` site block:
   ```bash
   grep -rn -i 'access-control-allow-origin' /etc/caddy/ /path/to/Caddyfile
   ```
3. **Delete** the `header Access-Control-Allow-Origin "https://radiantcore.org"` line (and
   any sibling `header Access-Control-*` lines) from the `/api` handler. Leave FastAPI as
   the single CORS authority. Do **not** replace it with `*` in Caddy — that just recreates
   the duplicate.
4. Reload Caddy (zero-downtime): `caddy reload --config <path>` (or `docker compose ... exec caddy caddy reload`).
5. Redeploy/restart the rxindexer container so Part A (the `OPTIONS` bypass) is live.

If you instead want CORS owned by Caddy (not FastAPI): remove FastAPI's by setting
`ALLOWED_ORIGINS=` empty AND `ELECTRUMX_ENV=dev`-style escape — **not recommended**; the
app-level middleware is the right home. Pick exactly one source.

## Verify

```bash
# 1) Exactly one ACAO header (browsers reject 2+):
curl -sD - -o /dev/null -H 'Origin: https://example.com' \
  https://radiantcore.org/api/wave/resolve/satoshi | grep -ci '^access-control-allow-origin'
# expect: 1

# 2) Preflight succeeds (was 401):
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS \
  -H 'Origin: https://example.com' -H 'Access-Control-Request-Method: GET' \
  https://radiantcore.org/api/wave/resolve/satoshi
# expect: 200 or 204
```

**Final check must be a real browser** from a third-party origin (e.g. a dapp page calling
`resolveWaveName('satoshi.rxd')` via `docs/wave-resolver.js`). `curl`/Node passing proves
nothing about CORS.

## Rollback

Part A is a pure pass-through for `OPTIONS` (no auth/data exposure — `OPTIONS` carries no
body/credentials); revert the 2-line block if needed. Part B is a Caddy edit; restore the
removed `header` line and `caddy reload`.

## Related

- Rate limits + the standing single-source-of-truth CORS rule: `docs/REST_API.md` →
  "Public Access: CORS & Rate Limits".
- Proxy-IP trust (so the rate limiter keys on the real client IP, not the proxy):
  `TRUST_PROXY=1` + `TRUSTED_PROXIES`.
