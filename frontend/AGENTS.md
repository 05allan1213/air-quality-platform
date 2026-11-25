# Frontend instructions — City Air Quality Platform

## Scope and design discovery

- Read the repository [AGENTS.md](../AGENTS.md) and this directory's [DESIGN.md](DESIGN.md) for formal frontend work. Apply confirmed visual and interaction decisions within their documented scope. Seek the user's decision for material changes to visual direction, information architecture, interaction patterns, or product scope.
- This directory currently holds design and engineering guidance. The separately installable [national overview preview](../prototypes/frontend-preview/evidence/national/评审说明.md) is the approved visual reference and uses local F inputs. Verify the semantics, dependencies, asset rights, and rendered behavior of any pattern before reuse. The implementation measurements marked historical in DESIGN.md refer to the retired S08 build.

## Contract and implementation boundaries

- The approved project stack is Vue 3, TypeScript, Vite, Vue Router, Pinia, Axios, Element Plus, and ECharts. Keep a lockfile with the next authorized application. Use project patterns and the component library's public mounting options to share semantic theme values and test nested overlays in the actual DOM.
- Keep safe navigation conditions in the route, transient hover and focus in their component, and coordinated selection and session state with its owner. Use stable server IDs, including site epochs where applicable. Preserve authorized filters and return context without retaining protected readings across sessions.
- Use [v1 examples](../docs/api/v1.md) and [OpenAPI](../docs/api/openapi.v1.json). Bootstrap with `GET /api/v1/auth/csrf/`, submit `X-CSRFToken` with protected POSTs and writes including login, and retrieve a fresh token after login. Use the same-origin HttpOnly session cookie. Handle `403/not_authenticated` by clearing private state and reauthenticating. Refresh the token after `403/csrf_failed` and apply the operation's replay rules. Use server-projected capabilities for navigation. Each endpoint authorizes the actual operation.
- Cancel superseded reads and gate responses against the current applied query, request generation and session. On revocation, logout or user switch, discard protected views, exports and in-flight results. Respect private `no-store` responses. Keep redaction and inaccessible private objects indistinguishable from the views allowed by the contract.
- Consume server product, time, coverage, quality, ranking and version semantics. Preserve the parent query and `input_version` for research details and exports. Restart changed pages at the first cursor and use the original receipt to resolve an uncertain high-impact write. [S08 contract additions](../docs/verification/S08_CONTRACT_DELTA.md)
- Initialize ECharts on a visible, sized host, respond to resizing, and release observers, handlers and instances. Preserve valid selection, zoom and legend state. Provide an accessible equivalent. Package assets locally under applicable rights and attribution for offline delivery.

## Delivery evidence

- Use `frontend-delivery` for implementation. Select design, accessibility and browser checks based on the actual change. For the retained preview, run `npm ci`, `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, and `npm run test:browser` in `prototypes/frontend-preview/`. Use the scenarios in DESIGN.md for relevant viewports, keyboard, zoom, focus, responsive layout, and material fallbacks.
- Integration claims require the actual Django session and authorized responses in an isolated environment. Use the [backend runbook](../docs/operations/S08.md). Label F, licensed H, injected failures and authorized real-source evidence according to their scope. Record `PASS`, `FAIL`, or `NOT RUN` with the actual version, environment, and limits.
