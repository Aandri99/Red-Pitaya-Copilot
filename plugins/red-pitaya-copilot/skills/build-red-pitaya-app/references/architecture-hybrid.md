# Hybrid application

## Pattern

Use hybrid when a narrow Nginx/Lua adapter and a persistent service have distinct responsibilities:

```text
browser
  -> same-origin Nginx allow-list
      -> Lua validation/normalization
          -> fixed internal service route
              -> persistent Python/C++ owner
                  -> hardware/storage/protocol
```

The service remains the sole source of mutable state. Lua does not mirror the state in worker globals.

## Division of responsibility

Nginx:

- public path and method restrictions;
- body/connection limits;
- static assets;
- fixed internal upstream routes;
- optional WebSocket proxy.

Lua:

- parse small requests;
- validate schema, types, ranges, and units;
- map public actions to fixed internal calls;
- normalize error envelopes;
- never own timed or durable work.

Service:

- resource arbitration;
- hardware access;
- durable profiles/history;
- restart recovery;
- authoritative counters/status;
- safe shutdown.

Browser:

- render confirmed state;
- handle reconnect;
- never become the authoritative scheduler or counter.

## Avoid split-brain

Give every mutation a command ID and let the service return its accepted/current state. For retried requests:

- make commands idempotent by ID; or
- return a conflict and the current owner/state.

Do not update Lua-local state before service acknowledgement. Do not treat an HTTP timeout as proof that a command did not execute.

## Internal routing

Prefer fixed internal Nginx locations:

```nginx
location = /my_app/internal/service/status {
    internal;
    proxy_pass http://127.0.0.1:9187/status;
}
```

Lua may call that internal location with `ngx.location.capture`. The public request must not control the upstream host, port, URI, or method.

## Streaming

Use:

- polling for low-rate status;
- Server-Sent Events for one-way event/status streams;
- WebSocket for bidirectional low-latency control only when the service implements lifecycle, limits, and reconnect semantics.

Do not stream unlimited histories through Lua. Send bounded updates and fetch bulk data through a separate, bounded endpoint or file.

## Acceptance

- service is the only hardware writer;
- Lua and direct service paths cannot bypass each other’s safety policy;
- upstream unavailable returns `ok:false`;
- duplicate/retried mutations are deterministic;
- Nginx and service limits agree;
- service restart produces an explicit epoch/change visible to the browser;
- WebSocket/poll reconnect cannot create a second profile or reset counters.
