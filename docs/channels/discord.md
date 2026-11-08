# Discord channel

X.A.A. supports text-only, private-DM Discord ingress and replies. It verifies and stores a bot token in Windows Credential Manager, listens to Discord's Gateway through `discord.py`, and ignores guild messages. The adapter requests only the `DM_MESSAGES` intent. Discord documents that DM message content is available without the privileged Message Content intent ([Gateway intents](https://discord.com/developers/docs/topics/gateway#message-content-intent)).

## Configure

1. Create a bot application in the Discord Developer Portal and copy its bot token.
2. In X.A.A., open **Messaging channels → Discord (private DMs)**.
3. Paste the bot token and choose **Verify and save Discord bot token**. X.A.A. verifies the bot identity and stores the token in Windows Credential Manager.
4. Choose **Generate one-time Discord pairing code**. Within five minutes, send `/pair CODE` to the bot in a private DM.
5. Refresh Discord status. The linked-user count should increase after the bot consumes the code.

Guild messages are ignored. An unpaired DM cannot create a run. A paired DM creates an Observe-mode run without a workspace or tool grants. Discord message IDs are durably deduplicated per bot account before coordinator submission. Revoking links disables paired users and cancels their pending replies. Clearing or rotating the token is observed by the running gateway service.

## Reply delivery and recovery

When a run reaches a terminal state, its saved assistant answer is queued for the same private DM. A successful send stores Discord's message ID. Content is limited to 2,000 characters, and `allowed_mentions` disables parsed mentions so model output cannot ping users or roles. Discord's `enforce_nonce` option suppresses a duplicate nonce only for the past few minutes ([Create Message API](https://discord.com/developers/docs/resources/message#create-message)); it is a short-window guard, not the durable recovery record.

Before the REST request, the outbox records `sending`. A successful response becomes `sent`; a definite rejection becomes `failed`; a timeout, malformed response, or server error becomes `unknown`. Process restart converts any interrupted `sending` row to `unknown` instead of assuming that Discord did not accept it. The authenticated channel panel lists unknown replies. The owner must check the private destination and explicitly mark the reply delivered or request a retry. Retrying may duplicate a message if Discord accepted the first send outside its nonce window; retries are capped at two owner-authorized retries per inbound message.

Fixture coverage verifies pairing, private-message boundaries, duplicate suppression, Observe-mode permissions, outbox state transitions, migration from the ingress-only schema, crash recovery, bounded retries, API authorization/confirmation, token management, and the desktop recovery workflow. No live Discord bot acceptance has been performed. Guilds, threads, commands, media, voice, and cross-channel identity links remain unsupported.
