# OpenClaw Dory Shim Contract

- `wake`, `search`, `get`, `write`, `link`, and `status` must map to the current HTTP routes.
- `baseUrl` is required.
- `token` is optional.
- If `token` is present, send `Authorization: Bearer <token>`.
- Use JSON request bodies for POST routes.
- Use query parameters for `GET /v1/get`.
- Treat non-2xx responses as errors.
