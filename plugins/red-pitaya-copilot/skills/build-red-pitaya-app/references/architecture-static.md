# Static application

## Purpose

Use a static application for documentation, diagrams, configuration calculators, demonstrations using supplied data, or visualization that does not read private board state and does not mutate the board.

## Required tree

```text
<slug>/
  redpitaya-project.json
  index.html
  css/app.css
  js/app.js
  info/info.json
  info/icon.png
  info/icon/128.png
  info/icon/256.png
  info/icon/512.png
  fpga.sh
```

For `fpga.enabled: false`, omit `fpga.conf` and keep `fpga.sh` as an
executable no-op. The verified OS 2.00-35 launcher treats `none` as a literal
bitstream path and rejects the app. Confirm the exact image’s discovery rules
before deployment.

## Browser rules

- Use relative static-asset paths.
- Include a version query on changed CSS/JS.
- Do not use `file://` assumptions.
- Escape user-provided text with DOM text APIs; do not inject it through `innerHTML`.
- Avoid third-party CDNs on an instrument that may operate offline.
- Label sample, simulated, cached, and live values distinctly.
- Use accessible buttons, status text, and keyboard navigation.

## Forbidden behavior

A static app must not:

- contain credentials;
- call undocumented administrator endpoints;
- claim hardware state;
- use CORS as an authentication substitute;
- embed a board IP that will become stale;
- rely on a browser interval for monitoring continuity.

If live board state becomes necessary, migrate deliberately to `nginx-lua`, a service, or hybrid and update the manifest.

## Acceptance

- Launcher metadata parses.
- Page and all assets load with the network disconnected after first load.
- No console errors occur.
- No fetch/XHR/WebSocket is made unless documented as an external, read-only dependency.
- Values derived from local input are reproducible and unit-tested.
