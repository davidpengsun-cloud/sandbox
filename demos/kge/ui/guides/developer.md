# Developers and agent builders

Connect apps and AI agents as the signed-in person. This guide is available to people with different roles; it grants no additional permissions. An app or agent sees no more than the person it acts for, and each operation still requires that person's permissions.

## 1. Find this system's addresses

Use the API and MCP addresses displayed below. Open the Developer API reference for the operations and request formats. Your app calls the API directly; your assistant connects to the MCP address.

## 2. Get your own token

Send a POST request to `/session/token` at the API address with Content-Type `application/json` and a JSON body containing `username` (your sign-in email) and `password` (your own password). Use your app's secure sign-in input; do not put a password in shared configuration.

The response contains `access_token`, `token_type` and `expires_in` in seconds. Keep the token private. Send it in the Authorization header as `Bearer <access_token>`. A password sign-in must be enabled for the token exchange; if it is unavailable, ask your access administrator for help.

## 3. Connect your app or assistant

For an app, use the API reference to select an operation your role may perform. For an AI assistant, add an HTTP MCP connection in its connection settings, using the displayed MCP address and the Authorization header with your own token. If your assistant uses JSON settings, use the configuration displayed below and replace `<access_token>` with your token.

Ask the assistant to use `map_term` for a term in a business area you may read, or `get_context` for a question about that area. The assistant's reads and actions remain limited by your permissions; an unavailable area must not become readable through the assistant. MCP does not expose every action available on the pages.

## 4. Renew the token

Before `expires_in` elapses, repeat the same sign-in token exchange with your own credentials and replace the token in your app or assistant. There is no refresh token in this response. If access changes or a request is refused, check your current permissions and sign in again as needed; do not use another person's token.
