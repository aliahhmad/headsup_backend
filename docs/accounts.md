# Accounts and access

HeadsUp supports registration, email verification, login, access/refresh tokens,
password recovery, profile updates, and API keys. Authenticated feature routes
require a verified, non-banned account. Monitor and notification access is scoped
to the owning user, including for administrators; cross-account run inspection
uses dedicated administrator endpoints.

## First administrator

Run from `api/` using the environment of the intended database:

```sh
uv run --locked api-admin user create --admin
```

On the deployed server, use:

```sh
sudo docker_support/deploy/server/dc.sh exec api api-admin user create --admin
```

CLI-created users are verified. Credentials are entered interactively. The CLI
uses database access directly, so it is an operator tool rather than an authenticated
web session. See available account operations without changing data:

```sh
uv run --locked api-admin user --help
uv run --locked api-admin user list
```

## Registration and recovery

Registration at `/register/` returns `token` and `refresh`, and sends a verification
email. Local email appears in Mailpit; real deployment needs working SMTP.
Open the verification link before accessing protected features. Passwords require
at least eight characters and at most 72 UTF-8 bytes.

Login at `/login/` returns the same token pair. Send the access token as
`Authorization: Bearer <token>`. Its default lifetime is 120 minutes. `/refresh/`
exchanges an unexpired refresh token for an access token; the original refresh
lifetime is 30 days. Refresh tokens are not accepted as access tokens.

The frontend provides login, registration, forgot-password, and reset-password
pages. Backend reset links use `BASE_URL`; `FRONTEND_URL` selects the browser reset
page. Generic recovery responses avoid confirming whether an email has an account.

## Invitation settings

The public registration-settings endpoint tells the browser whether an invitation
code is required. The private code is configured server-side and checked again at
registration. Administrators can manage the stored gate under **Signup settings**;
changing the gate does not stop existing users logging in. See
[configuration](usage/configuration/environment.md#registration-and-administration).

## Profile and security

The frontend profile contains account details and the alert-email opt-in switch.
Security settings contain password and API-key controls. Keys use the `X-API-Key`
header and are returned in full only at creation; store the returned value securely.
Consult the running [API documentation](reference/swagger.md) for exact key fields
and revocation endpoints. Alert-email settings use the verified account address,
with no separate arbitrary recipient.

Administrator user management supports account inspection and permitted role,
ban, and deletion operations. The optional backend SQLAdmin panel is a separate
operator interface and is disabled by the deployment defaults.
