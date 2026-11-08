import {useEffect,useState} from 'react';
import type {DiscordChannelStatus,EmailChannelStatus,EngineClient,TelegramChannelStatus,WebhookChannelStatus} from './api/client.ts';

export function ChannelsPanel({client}:{client:EngineClient}) {
  const [status,setStatus]=useState<TelegramChannelStatus>();
  const [discord,setDiscord]=useState<DiscordChannelStatus>();
  const [token,setToken]=useState('');
  const [discordToken,setDiscordToken]=useState('');
  const [pairing,setPairing]=useState<{code:string;expires_at:string}>();
  const [discordPairing,setDiscordPairing]=useState<{code:string;expires_at:string}>();
  const [busy,setBusy]=useState(false);
  const [confirmRevoke,setConfirmRevoke]=useState(false);
  const [confirmDiscordRevoke,setConfirmDiscordRevoke]=useState(false);
  const [discordReconcileChoice,setDiscordReconcileChoice]=useState<{messageId:string;resolution:'delivered'|'retry'}>();
  const [reconcileChoice,setReconcileChoice]=useState<{updateId:number;resolution:'delivered'|'retry'}>();
  const [emailReconcileChoice,setEmailReconcileChoice]=useState<{uidValidity:string;uid:string;resolution:'delivered'|'retry'}>();
  const [error,setError]=useState('');
  const [email,setEmail]=useState<EmailChannelStatus>();
  const [webhook,setWebhook]=useState<WebhookChannelStatus>();
  const [webhookSecret,setWebhookSecret]=useState('');
  const [emailAddress,setEmailAddress]=useState('');
  const [emailPassword,setEmailPassword]=useState('');
  const [imapHost,setImapHost]=useState('');
  const [smtpHost,setSmtpHost]=useState('');
  const [allowedSenders,setAllowedSenders]=useState('');
  const [authservId,setAuthservId]=useState('');

  async function refresh() {
    try {setStatus(await client.telegramStatus());setDiscord(await client.discordStatus());setWebhook(await client.webhookStatus());setError('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not read Telegram status.');}
  }
  useEffect(()=>{void refresh();void client.emailStatus().then(setEmail).catch(failure=>setError(String(failure)));},[client]);

  async function saveToken() {
    setBusy(true);setError('');
    try {setStatus(await client.setTelegramToken(token));setToken('');setPairing(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not verify or save the Telegram bot token.');}
    finally {setBusy(false);}
  }

  async function saveDiscordToken() {
    setBusy(true);setError('');
    try {setDiscord(await client.setDiscordToken(discordToken));setDiscordToken('');setDiscordPairing(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not verify or save the Discord bot token.');}
    finally {setBusy(false);}
  }

  async function clearDiscordToken() {
    setBusy(true);setError('');
    try {setDiscord(await client.clearDiscordToken());setDiscordPairing(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not clear the Discord bot token.');}
    finally {setBusy(false);}
  }

  async function makeDiscordPairing() {
    setBusy(true);setError('');
    try {setDiscordPairing(await client.discordPairing());}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not create a Discord pairing code.');}
    finally {setBusy(false);}
  }

  async function revokeDiscordLinks() {
    setBusy(true);setError('');
    try {await client.revokeDiscordLinks();setDiscord(await client.discordStatus());setDiscordPairing(undefined);setConfirmDiscordRevoke(false);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not revoke Discord links.');}
    finally {setBusy(false);}
  }

  async function reconcileDiscordDelivery() {
    if(!discordReconcileChoice)return;
    setBusy(true);setError('');
    try {setDiscord(await client.reconcileDiscordDelivery(discordReconcileChoice.messageId,discordReconcileChoice.resolution));setDiscordReconcileChoice(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not reconcile the Discord reply.');}
    finally {setBusy(false);}
  }
  async function clearToken() {
    setBusy(true);setError('');
    try {setStatus(await client.clearTelegramToken());setPairing(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not clear the Telegram bot token.');}
    finally {setBusy(false);}
  }
  async function makePairing() {
    setBusy(true);setError('');
    try {setPairing(await client.telegramPairing());}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not create a pairing code.');}
    finally {setBusy(false);}
  }

  async function revokeLinks() {
    setBusy(true);setError('');
    try {await client.revokeTelegramLinks();setStatus(await client.telegramStatus());setPairing(undefined);setConfirmRevoke(false);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not revoke Telegram links.');}
    finally {setBusy(false);}
  }

  async function reconcileTelegramDelivery() {
    if(!reconcileChoice)return;
    setBusy(true);setError('');
    try {setStatus(await client.reconcileTelegramDelivery(reconcileChoice.updateId,reconcileChoice.resolution));setReconcileChoice(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not reconcile the Telegram delivery.');}
    finally {setBusy(false);}
  }

  async function reconcileEmailDelivery() {
    if(!emailReconcileChoice)return;
    setBusy(true);setError('');
    try {setEmail(await client.reconcileEmailDelivery(emailReconcileChoice.uidValidity,emailReconcileChoice.uid,emailReconcileChoice.resolution));setEmailReconcileChoice(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not reconcile the email reply.');}
    finally {setBusy(false);}
  }

  async function saveEmail() {
    setBusy(true);setError('');
    try {setEmail(await client.setEmailAccount({address:emailAddress,password:emailPassword,imap_host:imapHost,smtp_host:smtpHost,allowed_senders:allowedSenders.split(',').map(value=>value.trim()).filter(Boolean),imap_port:993,smtp_port:587,smtp_security:'starttls',authserv_id:authservId}));setEmailPassword('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not save email settings.');}
    finally {setBusy(false);}
  }

  async function clearEmail() {
    setBusy(true);setError('');
    try {setEmail(await client.clearEmailAccount());setEmailPassword('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not clear email settings.');}
    finally {setBusy(false);}
  }

  async function rotateWebhookSecret() {
    setBusy(true);setError('');
    try {const result=await client.createWebhookCredential();setWebhook({configured:result.configured});setWebhookSecret(result.secret);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not create the webhook secret.');}
    finally {setBusy(false);}
  }

  async function clearWebhook() {
    setBusy(true);setError('');
    try {setWebhook(await client.clearWebhookCredential());setWebhookSecret('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not disable webhook ingress.');}
    finally {setBusy(false);}
  }

  return <section aria-labelledby="channels-title">
    <h2 id="channels-title">Messaging channels</h2>
    <h3>Discord (private DMs)</h3>
    <p>Connect a Discord bot token. X.A.A. stores it in Windows Credential Manager and listens only for direct messages. Guild messages are ignored; message content intent is not requested. A paired user can submit Observe-mode runs.</p>
    <label htmlFor="discord-token">Discord bot token</label>
    <input id="discord-token" type="password" autoComplete="new-password" maxLength={512} value={discordToken} onChange={event=>setDiscordToken(event.target.value)} disabled={busy}/>
    <div className="controls">
      <button type="button" onClick={()=>void saveDiscordToken()} disabled={busy||!discordToken.trim()}>Verify and save Discord bot token</button>
      {discord?.configured&&<button type="button" onClick={()=>void clearDiscordToken()} disabled={busy}>Clear saved Discord token</button>}
      <button type="button" onClick={()=>void client.discordStatus().then(setDiscord).catch(failure=>setError(String(failure)))} disabled={busy}>Refresh Discord status</button>
    </div>
    {discord&&<p role="status">{discord.configured?<>Connected bot: {discord.bot_username??discord.bot_id}</>:'Discord bot is not configured'} · {discord.linked_users} linked user(s) · {discord.processed_messages} processed message(s)</p>}
    {discord?.configured&&<>
      <button type="button" onClick={()=>void makeDiscordPairing()} disabled={busy}>Generate one-time Discord pairing code</button>
      {discord.deliveries&&Object.keys(discord.deliveries).length>0&&<p>Reply deliveries: {Object.entries(discord.deliveries).map(([state,count])=>`${state}: ${count}`).join(' · ')}</p>}
      {(discord.unknown_deliveries??[]).length>0&&<section aria-label="Discord replies needing review">
        <h4>Unconfirmed Discord replies</h4>
        <p>Check the private Discord chat before choosing. A retry may duplicate a reply if Discord accepted the first send outside its short deduplication window.</p>
        <ul>{(discord.unknown_deliveries??[]).map(delivery=><li key={delivery.message_id}>
          <span>Message {delivery.message_id} · Discord user {delivery.user_id??'unknown'} · run {delivery.run_id??'unavailable'} · {new Date(delivery.updated_at).toLocaleString()}</span>
          <div className="controls">
            <button type="button" onClick={()=>setDiscordReconcileChoice({messageId:delivery.message_id,resolution:'delivered'})} disabled={busy}>I checked; mark Discord reply delivered</button>
            <button type="button" onClick={()=>setDiscordReconcileChoice({messageId:delivery.message_id,resolution:'retry'})} disabled={busy}>I checked; retry Discord reply</button>
          </div>
          {discordReconcileChoice?.messageId===delivery.message_id&&<div role="alertdialog" aria-label="Confirm Discord delivery reconciliation">
            <p>{discordReconcileChoice.resolution==='retry'?'Retrying can send a duplicate if Discord accepted the first reply.':'This records that you found the reply in the private chat.'} Confirm that you checked the destination.</p>
            <button type="button" onClick={()=>void reconcileDiscordDelivery()} disabled={busy}>Confirm Discord {discordReconcileChoice.resolution==='retry'?'retry':'delivered'}</button>
            <button type="button" onClick={()=>setDiscordReconcileChoice(undefined)} disabled={busy}>Cancel</button>
          </div>}
        </li>)}</ul>
      </section>}
      {discord.linked_users>0&&<div className="controls">
        {confirmDiscordRevoke?<><span>This prevents linked Discord users from submitting new objectives. Saved runs remain available.</span><button type="button" onClick={()=>void revokeDiscordLinks()} disabled={busy}>Confirm revoke Discord links</button><button type="button" onClick={()=>setConfirmDiscordRevoke(false)} disabled={busy}>Keep links</button></>:<button type="button" onClick={()=>setConfirmDiscordRevoke(true)} disabled={busy}>Revoke all Discord users</button>}
      </div>}
      {discordPairing&&<div aria-label="Discord pairing instructions"><p>Within five minutes, send this exact command in a private DM to the bot:</p><code>/pair {discordPairing.code}</code><p>Expires {new Date(discordPairing.expires_at).toLocaleString()}.</p></div>}
    </>}
    <h3>Telegram (private chats)</h3>
    <p>Connect a bot token from BotFather. X.A.A. stores it in Windows Credential Manager. A paired private Telegram account can submit objectives as Observe-mode runs and receive their final answers.</p>
    <p>Pairing is required before messages can create runs. Group chats and unpaired users are ignored. An unclear send result is recorded without automatic retry.</p>
    <label htmlFor="telegram-token">Telegram bot token</label>
    <input id="telegram-token" type="password" autoComplete="new-password" maxLength={512} value={token} onChange={event=>setToken(event.target.value)} disabled={busy}/>
    <div className="controls">
      <button type="button" onClick={()=>void saveToken()} disabled={busy||!token.trim()}>Verify and save bot token</button>
      {status?.configured&&<button type="button" onClick={()=>void clearToken()} disabled={busy}>Clear saved token</button>}
      <button type="button" onClick={()=>void refresh()} disabled={busy}>Refresh channel status</button>
    </div>
    {status&&<p role="status">{status.configured?<>Connected bot: @{status.bot_username??'username unavailable'}</>:'Telegram bot is not configured'} · {status.linked_users} linked user(s) · {status.processed_updates} processed update(s)</p>}
    {status?.configured&&<>
      <button type="button" onClick={()=>void makePairing()} disabled={busy}>Generate one-time pairing code</button>
      {status.linked_users>0&&<div className="controls">
        {confirmRevoke?<><span>This prevents {status.linked_users} linked user(s) from submitting new objectives and cancels queued scheduled messages. A send already in progress cannot be recalled; saved runs remain available.</span><button type="button" onClick={()=>void revokeLinks()} disabled={busy}>Confirm revoke links</button><button type="button" onClick={()=>setConfirmRevoke(false)} disabled={busy}>Keep links</button></>:<button type="button" onClick={()=>setConfirmRevoke(true)} disabled={busy}>Revoke all linked users</button>}
      </div>}
      {pairing&&<div aria-label="Telegram pairing instructions"><p>Within five minutes, send this exact command in a private chat with @{status.bot_username??'your bot'}:</p><code>/pair {pairing.code}</code><p>Expires {new Date(pairing.expires_at).toLocaleString()}.</p></div>}
      {status.deliveries&&Object.keys(status.deliveries).length>0&&<p>Delivery records: {Object.entries(status.deliveries).map(([state,count])=>`${state}: ${count}`).join(' · ')}</p>}
      {(status.unknown_deliveries??[]).length>0&&<section aria-label="Telegram deliveries needing review">
        <h4>Unconfirmed replies</h4>
        <p>Open the listed private chat and check whether X.A.A.'s reply arrived before choosing an action. X.A.A. never retries an unclear send automatically.</p>
        <ul>{(status.unknown_deliveries??[]).map(delivery=><li key={delivery.update_id}>
          <span>Update {delivery.update_id} · Telegram user {delivery.user_id??'unknown'} · run {delivery.run_id??'unavailable'} · {new Date(delivery.updated_at).toLocaleString()}</span>
          <div className="controls">
            <button type="button" onClick={()=>setReconcileChoice({updateId:delivery.update_id,resolution:'delivered'})} disabled={busy}>I checked; mark delivered</button>
            <button type="button" onClick={()=>setReconcileChoice({updateId:delivery.update_id,resolution:'retry'})} disabled={busy}>I checked; retry reply</button>
          </div>
          {reconcileChoice?.updateId===delivery.update_id&&<div role="alertdialog" aria-label="Confirm Telegram delivery reconciliation">
            <p>{reconcileChoice.resolution==='retry'?'Retrying can send a duplicate if the first reply actually arrived.':'This records that you found the reply in Telegram.'} Confirm that you checked the destination chat.</p>
            <button type="button" onClick={()=>void reconcileTelegramDelivery()} disabled={busy}>Confirm {reconcileChoice.resolution==='retry'?'retry':'delivered'}</button>
            <button type="button" onClick={()=>setReconcileChoice(undefined)} disabled={busy}>Cancel</button>
          </div>}
        </li>)}</ul>
      </section>}
    </>}
    <h3>Generic webhook (signed text ingress)</h3>
    <p>Creates an inbound-only callback key in Windows Credential Manager. Any system holding the shared key can submit text events; X.A.A. treats message text as untrusted and starts an Observe-mode run with no workspace, command, browser, search or MCP grants. Sign the exact request body with HMAC-SHA256 over <code>timestamp.event-id.body</code>. The local API remains loopback-only unless remote access is deliberately configured.</p>
    <div className="controls"><button type="button" onClick={()=>void rotateWebhookSecret()} disabled={busy}>{webhook?.configured?'Rotate webhook secret':'Create webhook secret'}</button>{webhook?.configured&&<button type="button" onClick={()=>void clearWebhook()} disabled={busy}>Disable webhook ingress</button>}</div>
    {webhook&&<p role="status">{webhook.configured?'Signed webhook ingress is configured.':'Signed webhook ingress is disabled.'}</p>}
    {webhookSecret&&<div aria-label="New webhook secret"><p>Copy this secret to the configured sender now. It is shown only once; rotate it if lost.</p><code>{webhookSecret}</code><button type="button" onClick={()=>void navigator.clipboard.writeText(webhookSecret).catch(()=>setError('Clipboard access failed; copy the displayed secret manually.'))} disabled={busy}>Copy secret</button></div>}
    <h3>Email (authorized senders)</h3>
    <p>Email uses IMAP over verified TLS and SMTP STARTTLS. Only exact allowed senders with a passing DMARC result from the configured receiving mail server can start Observe-mode runs. Replies are text only; ambiguous SMTP outcomes are never retried automatically.</p>
    <label htmlFor="email-address">Mailbox address</label><input id="email-address" type="email" autoComplete="username" value={emailAddress} onChange={event=>setEmailAddress(event.target.value)} disabled={busy}/>
    <label htmlFor="email-password">Mailbox password or app password</label><input id="email-password" type="password" autoComplete="new-password" value={emailPassword} onChange={event=>setEmailPassword(event.target.value)} disabled={busy}/>
    <label htmlFor="email-imap-host">IMAP server</label><input id="email-imap-host" value={imapHost} onChange={event=>setImapHost(event.target.value)} placeholder="imap.example.com" disabled={busy}/>
    <label htmlFor="email-smtp-host">SMTP server</label><input id="email-smtp-host" value={smtpHost} onChange={event=>setSmtpHost(event.target.value)} placeholder="smtp.example.com" disabled={busy}/>
    <label htmlFor="email-allowed-senders">Authorized sender addresses (comma separated)</label><input id="email-allowed-senders" value={allowedSenders} onChange={event=>setAllowedSenders(event.target.value)} placeholder="you@example.com" disabled={busy}/>
    <label htmlFor="email-authserv-id">Receiving mail server authserv-id</label><input id="email-authserv-id" value={authservId} onChange={event=>setAuthservId(event.target.value)} placeholder="mx.example.com" disabled={busy}/>
    <div className="controls"><button type="button" onClick={()=>void saveEmail()} disabled={busy||!emailAddress||!emailPassword||!imapHost||!smtpHost||!allowedSenders.trim()||!authservId.trim()}>Save email settings</button>{email?.configured&&<button type="button" onClick={()=>void clearEmail()} disabled={busy}>Clear email credentials</button>}</div>
    {email&&<>
      <p role="status">{email.configured?`Configured mailbox: ${email.address}`:'Email is not configured'} · {email.processed_messages} processed message(s){email.deliveries&&Object.keys(email.deliveries).length?` · ${Object.entries(email.deliveries).map(([state,count])=>`${state}: ${count}`).join(' · ')}`:''}</p>
      {email.configured&&(email.unknown_deliveries??[]).length>0&&<section aria-label="Email replies needing review">
        <h4>Unconfirmed email replies</h4>
        <p>Check the configured mailbox's Sent folder and the authorized sender's reply before choosing an action. A retry can send a duplicate if SMTP accepted the first message.</p>
        <ul>{(email.unknown_deliveries??[]).map(delivery=><li key={`${delivery.uid_validity}:${delivery.uid}`}>
          <span>Message UID {delivery.uid} · sender {delivery.sender??'unknown'} · run {delivery.run_id??'unavailable'} · {new Date(delivery.updated_at).toLocaleString()}</span>
          <div className="controls">
            <button type="button" onClick={()=>setEmailReconcileChoice({uidValidity:delivery.uid_validity,uid:delivery.uid,resolution:'delivered'})} disabled={busy}>I checked; mark email delivered</button>
            <button type="button" onClick={()=>setEmailReconcileChoice({uidValidity:delivery.uid_validity,uid:delivery.uid,resolution:'retry'})} disabled={busy}>I checked; retry email</button>
          </div>
          {emailReconcileChoice?.uid===delivery.uid&&emailReconcileChoice.uidValidity===delivery.uid_validity&&<div role="alertdialog" aria-label="Confirm email delivery reconciliation">
            <p>{emailReconcileChoice.resolution==='retry'?'Retrying may send a duplicate if the SMTP server accepted the first reply.':'This records that you found the reply in the Sent folder.'} Confirm that you checked the mailbox and reply destination.</p>
            <button type="button" onClick={()=>void reconcileEmailDelivery()} disabled={busy}>Confirm {emailReconcileChoice.resolution==='retry'?'retry':'delivered'}</button>
            <button type="button" onClick={()=>setEmailReconcileChoice(undefined)} disabled={busy}>Cancel</button>
          </div>}
        </li>)}</ul>
      </section>}
    </>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
