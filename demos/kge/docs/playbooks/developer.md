# Developers and agent builders: dev team playbook

This repository-only recipe verifies the same integration journey as Context’s developer guide. It is for Local, Compose and Google Cloud test systems. It grants no product permission; every API and MCP call acts as the person whose token it carries.

## 1. Open Context’s Docs

Sign in to the portal, open Context, then Docs and **Developers and agent builders**. Read the API and MCP addresses displayed by that system. Use its Developer API reference for operations and request formats. Apps use the API directly; assistants use the HTTP MCP address. The portal’s Context backend is for its own pages, not an integration gateway.

## 2. Exchange the person’s sign-in credentials

Use secure sign-in inputs for the person’s email and password. Send `POST /session/token` to the displayed API address with Content-Type `application/json` and a JSON body containing `username` and `password`. Password sign-in must be enabled. Never use demo token generation as a substitute for this acceptance check.

Check that the response contains `access_token`, `token_type` and `expires_in`. Keep both credentials and token private. Send the token in the Authorization header as `Bearer <access_token>`. The token exchange issues no refresh token.

## 3. Connect an assistant and verify its scope

In the assistant’s connection settings, add an HTTP MCP connection using the displayed MCP address and the person’s Authorization header. Use the configuration shown by Docs if the assistant takes JSON settings. Ask the assistant to use `map_term` for a readable term or `get_context` for a question within a readable business area. `/mcp` also offers `retrieve`; it does not expose every page action.

Repeat with a question requiring an area the person may not read. Confirm the assistant receives no protected content from that area. Use two test identities with different readable areas to verify isolation, retaining only redacted results. The app or agent sees no more than the person it acts for; a shared guide does not grant any role or permission.

The offline API/MCP integration checks in `tests/integration/test_password_bearer_api.py` verify bearer authentication, calls as the signed-in person and read restrictions. Use the repository’s test instructions to prepare the build before running integration checks. A socket-free test does not establish that a live assistant’s connector is configured correctly.

## 4. Renew and replace the token

Before `expires_in` elapses, repeat `POST /session/token` with that person’s credentials and replace the token in the app or assistant. Check refused calls against the person’s current permissions. Never recover by using another person’s token.
