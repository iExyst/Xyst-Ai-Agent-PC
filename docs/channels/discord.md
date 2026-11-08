# Discord channel

X.A.A. has an initial **inbound-only, private-DM** Discord adapter. It verifies and stores a bot token in Windows Credential Manager, listens to Discord's Gateway through `discord.py`, and ignores guild messages. The adapter requests only the `DM_MESSAGES` intent and does not request the privileged Message Content intent. Discord documents that message content remains available in DMs without that privileged intent ([Gateway intents](https://discord.com/developers/docs/topics/gateway#message-content-intent)).

## Configure

1. Create a bot application in the Discord Developer Portal and copy its bot token.
2. In X.A.A., open **Messaging channels → Discord (private DMs)**.
3. Paste the bot token and choose **Verify and save Discord bot token**. X.A.A. verifies the bot identity and stores the token in Windows Credential Manager.
4. Choose **Generate one-time Discord pairing code**. Within five minutes, send `/pair CODE` to the bot in a private DM.
5. Refresh Discord status. The linked-user count should increase after the bot consumes the code.

Guild messages are ignored. An unpaired DM cannot create a run. A paired DM creates an Observe-mode run without a workspace, command grant, browser grant, search grant, or MCP grant. Discord message IDs are durably deduplicated per bot account before coordinator submission. Revoking links disables all currently paired users for that bot. Clearing or rotating the token is observed by the running gateway service, which closes the old connection.

This slice does not yet send run answers back to Discord, reconcile ambiguous outbound sends, support guilds, threads, commands, media, voice, or cross-channel identity links. No live Discord bot acceptance has been performed. Fixture coverage verifies pairing, private-message boundaries, duplicate suppression, Observe-mode permissions, token management API behavior, and the desktop settings workflow.
