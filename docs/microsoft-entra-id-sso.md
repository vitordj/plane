# Microsoft Entra ID (Azure AD) sign-in

Adds Entra ID as an OAuth provider alongside Plane's own Google, GitHub, GitLab
and Gitea options, so people sign in with their Microsoft work account.

It follows the shape every other provider in the fork uses — a subclass of
`OauthAdapter`, a pair of endpoints per surface, an instance-configuration
category and a God Mode screen — so no core authentication logic is rewritten,
per [FORK.md](../FORK.md).

## Why the tenant matters more than anything else here

Entra will happily issue tokens to any application from any directory. What
decides who may sign into *this* instance is the tenant the app is pinned to.

`AZUREAD_TENANT_ID` is that pin, and it works in two layers:

1. It goes into the authority URL, so the authorization and token requests are
   made against that directory alone.
2. The `tid` claim on the returned `id_token` is checked against it. A GUID
   must match exactly; a domain (which never appears in `tid`) relies on the
   authority pin, so the check only confirms the token names a directory and
   agrees with its own issuer.

Setting the tenant to `common`, `organizations` or `consumers` disables layer 2
by definition: those values mean "any directory", so accounts from any Entra
tenant — including one an attacker created ten minutes ago — can complete a
sign-in. Use them only for a deliberately multi-tenant instance. The God Mode
field says so at the point of configuration.

The `id_token`'s signature is not verified against JWKS. It arrives on the back
channel — a direct TLS POST to the tenant's own token endpoint, authenticated
with the client secret — which is the case OIDC Core 3.1.3.7 explicitly allows
a client to skip signature verification for. The claims that decide identity
(`aud`, `tid`, `iss`, `exp`) are still enforced.

## Which address the person is signed in as

In order: Microsoft Graph `mail`, then `userPrincipalName`, then the
`email`/`preferred_username` claims from the `id_token`.

A guest (B2B) account is refused. Its UPN looks like
`someone_outside.com#EXT#@contoso.onmicrosoft.com` — it encodes a foreign home
tenant and is not a deliverable address, so matching an existing Plane account
on it would be matching on the wrong thing.

The Account row is keyed on the directory object id (`id` from Graph, `oid` in
the token), not the address, so a rename or a mailbox change does not strand
the person's account.

## Avatars

None are imported. Entra's photo lives at an authenticated Graph endpoint, and
the shared avatar fetcher replays its request headers across every redirect
hop — which would hand the access token to whichever host Graph redirects the
blob to. Nothing is worth that, so the avatar is left to the person.

For the same reason `ENABLE_AZUREAD_SYNC` syncs only first and last name. The
shared implementation would also delete the person's uploaded avatar and
rewrite their display name from their email on every login; the directory is
not authoritative about either.

## Setting it up

In [Entra ID → App registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade):

1. Register an application, single-tenant.
2. Under **Authentication**, add a **Web** redirect URI:
   `https://<your-plane-host>/auth/azuread/callback/`
3. Under **API permissions**, add the delegated Microsoft Graph permission
   `User.Read`.
4. Under **Certificates & secrets**, create a client secret and copy its
   *value* (not its ID). Note the expiry — Entra secrets expire.

Then in God Mode → Authentication → Microsoft Entra ID, fill in the directory
(tenant) ID, application (client) ID and client secret, save, and turn the
method on.

## Configuration

| Key | Default | Effect |
| --- | --- | --- |
| `IS_AZUREAD_ENABLED` | `0` | Shows the Microsoft button on the sign-in screens. |
| `AZUREAD_TENANT_ID` | — | Directory GUID or verified domain. See above. |
| `AZUREAD_CLIENT_ID` | — | Application (client) ID. |
| `AZUREAD_CLIENT_SECRET` | — | Client secret value; stored encrypted. |
| `ENABLE_AZUREAD_SYNC` | `0` | Re-sync first/last name from the directory on each login. |

Each can be seeded from the environment and is then editable in God Mode, the
same as every other provider's configuration.

## Routes

| Path | Purpose |
| --- | --- |
| `/auth/azuread/` | Start sign-in (app) |
| `/auth/azuread/callback/` | Entra redirect target (app) |
| `/auth/spaces/azuread/` | Start sign-in (Spaces) |
| `/auth/spaces/azuread/callback/` | Entra redirect target (Spaces) |

## Error codes

| Code | Meaning |
| --- | --- |
| `5113` | `AZUREAD_NOT_CONFIGURED` — missing or unusable configuration. |
| `5126` | `AZUREAD_OAUTH_PROVIDER_ERROR` — bad state, missing code, or an `id_token` that fails `aud`/`exp`. |
| `5127` | `AZUREAD_TENANT_MISMATCH` — the account belongs to another directory. |
| `5128` | `AZUREAD_NO_EMAIL` — no usable address on the account (a guest, typically). |

## Tests

```bash
cd apps/api
pytest plane/tests/unit/authentication/
```

They cover the tenant pin (GUID, domain and multi-tenant configurations), the
audience and expiry checks, tenant values that could escape the authority URL,
the address resolution order including the guest refusal, the object-id keying,
and the sync override leaving avatar and display name alone.

## Not covered yet

- **Group-to-role mapping.** Entra group membership is not read, so workspace
  and project roles stay whatever Plane's own invitation flow assigned. This is
  the natural next step, and it pairs with the fork's organizational-unit layer
  ([organizational-units.md](./organizational-units.md)): an Entra group is the
  obvious source for a unit's membership.
- **Provisioning and deprovisioning.** There is no SCIM endpoint. A person
  removed from the directory keeps their Plane account until someone
  deactivates it.
- **Avatars**, for the reason above. Importing them means fetching the Graph
  photo directly rather than through the shared avatar path.
