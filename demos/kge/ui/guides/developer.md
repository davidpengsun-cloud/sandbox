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

## 4. Build an agent that asks questions

The MCP addresses for context, answers and search send your assistant short instructions when it connects. They describe the pattern below; follow it in your own code too.

1. Call `get_context` with the person's question and `open_items: true`. The reply's `bindings` say how each part of the question was read, and `open_items` list what is still open: a term with several meanings, a period with no data, a grouping word that names several dimensions, or something the person may not read.
2. Settle each open item your own way: ask the person, or take the default the item names and say so. No option is marked as the one to choose; your agent decides. An `access` item is a refusal: pass it on, and do not ask.
3. Call `ask` with the settled question in the person's own words, adding only what the person chose, and the conversation as `history`. `ask` keeps no state between calls.
4. Show figures and definitions as `ask` returns them, and pass on a refusal or "no data" as given.

Use `map_term` for what a term or an abbreviation means (an abbreviation lists every meaning it can have), and `search` with `kinds` for products, terms and filter values. Cerebro, the portal's assistant, is the reference implementation of this pattern.

## 5. Renew the token

Before `expires_in` elapses, repeat the same sign-in token exchange with your own credentials and replace the token in your app or assistant. There is no refresh token in this response. If access changes or a request is refused, check your current permissions and sign in again as needed; do not use another person's token.
