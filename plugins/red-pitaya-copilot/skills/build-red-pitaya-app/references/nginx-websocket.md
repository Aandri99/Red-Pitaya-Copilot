# Nginx, HTTP, and WebSocket transport

## Contents

1. Discover the target configuration
2. HTTP route design
3. Reverse proxy
4. WebSocket
5. Browser reconnect
6. Operational checks

## Discover the target configuration

Before changing routes, inspect:

- Nginx executable and compile modules;
- master command line and prefix;
- top-level config and app-fragment inclusion;
- PID/log paths;
- worker user;
- existing app route conventions;
- TLS/authentication boundary;
- body/time limits.

Do not assume desktop Nginx/OpenResty modules exist on both OS images.
In particular, an OS 2.07-48 image can ship the Red Pitaya Nginx binary
without the standard HTTP access and Basic-auth modules. In that case,
`deny`, `allow`, and `auth_basic` are not available, and a configuration that
uses them can fail its target-side syntax test. Keep method enforcement in the
service as the authoritative check, use only directives proven by `nginx -V`,
and provide an application-level authentication boundary when the platform
does not have one.

## HTTP route design

- Keep routes under `/<slug>/api/`.
- Use an explicit action allow-list.
- Separate GET status from POST mutations.
- Reject other methods.
- use `Cache-Control: no-store` for status;
- limit request bodies;
- use same-origin requests by default;
- do not add `Access-Control-Allow-Origin: *` to state-changing APIs.

CORS controls browser origins; it is not authentication. A permissive CORS header expands who can invoke an unauthenticated API.

## Reverse proxy

Bind the application service to loopback and proxy through a fixed upstream:

```nginx
location ~ ^/my_app/api/(status|version|stop)$ {
    limit_except GET POST { deny all; }
    rewrite ^/my_app/api/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:9187;
    proxy_connect_timeout 1s;
    proxy_read_timeout 5s;
    proxy_send_timeout 5s;
    proxy_request_buffering on;
    proxy_buffering off;
}
```

Use separate exact locations if methods differ. Do not let request parameters select the upstream host, port, or path.
The example assumes the target includes the HTTP access module. If it does
not, omit the unavailable `deny` directive, split read and mutation routes,
and require the upstream service to return `405` for every wrong method.

## WebSocket

Use a dedicated path and verify the service actually implements the protocol:

```nginx
location = /my_app/ws {
    proxy_pass http://127.0.0.1:9187/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_connect_timeout 1s;
    proxy_read_timeout 35s;
    proxy_send_timeout 10s;
    proxy_buffering off;
}
```

Define:

- maximum frame/message size;
- allowed message schemas and rate;
- authentication/authorization;
- heartbeat/idle timeout;
- server restart epoch;
- reconnect/backoff;
- subscription limits;
- backpressure and dropped-update counters;
- whether commands are idempotent.

Never assume WebSocket delivery is durable. Journal important events before broadcasting and provide a sequence/cursor for recovery.

For a new Python-backed status stream, prefer the scaffolder's
`--websocket` option. It creates a bounded reference implementation and
`protocols/websocket-v1.json`. Treat that contract as one indivisible unit with
the Nginx route, service handler and browser code. The generated reference
stream is deliberately status-only: it rejects state-changing commands,
fragmented frames, binary messages, oversized messages and excessive client
message rates. Slow or idle clients are disconnected rather than silently
accumulating unbounded data. Its origin check requires the browser `Origin`
hostname and effective port to match the forwarded `Host`; keep
`proxy_set_header Host $http_host`. Its sequence is scoped to one connection
and starts again at 1 after reconnect. `service_epoch` remains stable for the
life of the service process.

## Browser reconnect

Use exponential backoff with jitter and a cap. On reconnect:

1. authenticate if applicable;
2. fetch status and service epoch;
3. reset a connection-scoped sequence, or compare a separate durable cursor;
4. request missed durable records only when the custom protocol supports them;
5. update UI only from the new epoch/state.

Do not automatically resend a non-idempotent Start/Enable command.

## Operational checks

- Test configuration with the exact target executable/prefix.
- Reload only after config test succeeds.
- Confirm master and workers remain alive.
- Test static, status, invalid method, invalid action, and upstream-down behavior.
- Confirm newly added routes are active; file presence alone is insufficient.
- Inspect logs without exposing secrets or unbounded request bodies.
