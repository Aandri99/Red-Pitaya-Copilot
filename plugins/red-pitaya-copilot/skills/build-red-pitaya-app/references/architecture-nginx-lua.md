# Nginx/Lua application

## Use only for bounded request work

Use Nginx/Lua for status reads and short actions that complete within a request. Keep Lua as an adapter, not a daemon.

Suitable:

- read a bounded status file;
- call a fixed helper with an enumerated action;
- parse and validate a small JSON request;
- return a consistent JSON response;
- proxy to an internal service through a fixed internal location.

Unsuitable:

- acquisition loops;
- sleeps or timed profiles;
- waiting indefinitely for hardware;
- compiling/loading FPGA images;
- unbounded command output;
- durable queues or histories;
- arbitrary file browsing;
- user-controlled shell strings.

## Route contract

Allow-list actions in the Nginx expression:

```nginx
location ~ ^/my_app/api/(status|version|stop)$ {
    limit_except GET POST { deny all; }
    default_type application/json;
    add_header Cache-Control "no-store";
    content_by_lua_file /opt/redpitaya/www/apps/my_app/lua/api.lua;
}
```

The final Nginx behavior depends on how the target image incorporates app fragments. Inspect its top-level configuration. Adding a file to the app folder does not prove the running master loaded it.

## Lua implementation rules

1. Match the exact app prefix and final action.
2. Reject unknown actions with 404.
3. Restrict method by action.
4. Limit body size before parsing.
5. Require `application/json` for JSON mutations.
6. Reject duplicate/unknown fields when ambiguity is unsafe.
7. Validate type, finite numeric value, range, and units.
8. Call only fixed paths/actions.
9. Bound execution time and response size.
10. Return structured error codes.

Prefer `ngx.location.capture` to a fixed internal service location over shell execution. If a helper is unavoidable, pass only enumerated tokens after validation and never append request text to a command.

## State model

Lua status must distinguish:

- adapter reachable;
- worker/service reachable;
- hardware initialized;
- output safe;
- requested and confirmed mode;
- last error.

Do not return `"service": "ready"` merely because Lua executed.

## Concurrency

Assume requests can overlap. Mutations must be idempotent or serialized by the actual resource owner. A global Lua variable is worker-local and is not a reliable cross-worker lock.

## Acceptance

- Route/action sets match between Nginx, Lua, and JavaScript.
- GET status does not mutate state.
- POST stop is idempotent.
- malformed methods/bodies/types/ranges fail closed.
- no request can choose an executable, path, register address, or shell syntax;
- failure to reach the resource owner returns non-2xx and `ok:false`.
