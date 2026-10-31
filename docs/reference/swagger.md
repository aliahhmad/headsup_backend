# Interactive API documentation

The running API provides its current schemas and interactive request tools:

| Resource | Local URL |
| --- | --- |
| Swagger UI | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| OpenAPI JSON | <http://localhost:8000/openapi.json> |

For a deployed API, replace `http://localhost:8000` with its public API origin.
The static documentation site does not execute requests against a bundled demo
API or ship a copied schema.

Log in through `POST /login/`, copy the returned `token`, and use Swagger UI's
**Authorize** dialog to supply the bearer token. Interactive operations affect
the selected API's real database and may enqueue work. Use a development account
and monitor when exploring write endpoints.
