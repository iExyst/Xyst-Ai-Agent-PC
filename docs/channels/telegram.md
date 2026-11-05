# Telegram channel setup and operation

X.A.A. supports a first, text-only Telegram Bot API adapter. Telegram Bot API requests use HTTPS and the official `getMe`, `getUpdates`, and `sendMessage` methods. See the [Telegram Bot API documentation](https://core.telegram.org/bots/api).

## Configure the bot

1. Create a bot with Telegram's official BotFather and copy its HTTP API token.
2. In X.A.A., open **Messaging channels → Telegram**.
3. Paste the token and choose **Verify and save bot token**. X.A.A. verifies the bot identity, then stores the token in Windows Credential Manager. The token is never returned by the API or placed in run context.
4. Choose **Generate one-time pairing code**. In a private Telegram chat with the displayed bot, send the exact `/pair CODE` command before the five-minute expiry.
5. Refresh channel status and confirm the linked-user count. **Revoke all linked users** is a two-step control that disables every pairing for the currently configured bot; saved sessions and runs remain available. Clearing the bot token only stops polling and does not erase run history.

Only the private account that proves possession of the local one-time code is linked. Group chats and unpaired users do not create runs. A user message creates an Observe-mode run with the currently selected LM Studio model and no workspace grant. It enters the same persisted coordinator and supervisor used by the desktop.

When a run completes, its saved public answer is sent to the paired private chat. Failed, blocked, and cancelled runs send a generic notice directing the user to review the saved run in the desktop app. Delivery status and Telegram message ID are recorded locally.

## Delivery and recovery behavior

Telegram update IDs are stored with the input run in one SQLite transaction before the next polling offset advances. Repeated updates therefore resolve to the same saved event and do not create another run. The adapter uses one offset per bot. It drops an offset after six days without an update because Telegram may randomize the next update ID after a week without updates; Telegram retains incoming updates for at most 24 hours.

Before sending a reply, X.A.A. changes its durable delivery state from pending to sending. A successful response stores the Telegram message ID and sent state. A definitive API rejection becomes failed. A network timeout, invalid response, or server error becomes unknown and is never automatically retried, since Telegram may have accepted a message before the client lost the response. This avoids duplicate sends at the cost of a possibly missing reply when the outcome is unknown. The desktop displays delivery counts.

This first adapter handles text in private chats only. Media, group scopes, retries, commands beyond pairing, cross-channel identity links and other Telegram account modes remain unsupported. Bot status is scoped to the bot account verified from its token. A real configured bot and authorized test chat are required to verify external delivery; fixture tests do not establish live Telegram interoperability.
