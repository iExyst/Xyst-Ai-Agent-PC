import {useEffect,useState} from 'react';
import type {EmailChannelStatus,EngineClient,TelegramChannelStatus} from './api/client.ts';

export function ChannelsPanel({client}:{client:EngineClient}) {
  const [status,setStatus]=useState<TelegramChannelStatus>();
  const [token,setToken]=useState('');
  const [pairing,setPairing]=useState<{code:string;expires_at:string}>();
  const [busy,setBusy]=useState(false);
  const [confirmRevoke,setConfirmRevoke]=useState(false);
  const [error,setError]=useState('');
  const [email,setEmail]=useState<EmailChannelStatus>();
  const [emailAddress,setEmailAddress]=useState('');
  const [emailPassword,setEmailPassword]=useState('');
  const [imapHost,setImapHost]=useState('');
  const [smtpHost,setSmtpHost]=useState('');
  const [allowedSenders,setAllowedSenders]=useState('');
  const [authservId,setAuthservId]=useState('');

  async function refresh() {
    try {setStatus(await client.telegramStatus());setError('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not read Telegram status.');}
  }
  useEffect(()=>{void refresh();void client.emailStatus().then(setEmail).catch(failure=>setError(String(failure)));},[client]);

  async function saveToken() {
    setBusy(true);setError('');
    try {setStatus(await client.setTelegramToken(token));setToken('');setPairing(undefined);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not verify or save the Telegram bot token.');}
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

  return <section aria-labelledby="channels-title">
    <h2 id="channels-title">Messaging channels</h2>
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
    </>}
    <h3>Email (authorized senders)</h3>
    <p>Email uses IMAP over verified TLS and SMTP STARTTLS. Only exact allowed senders with a passing DMARC result from the configured receiving mail server can start Observe-mode runs. Replies are text only; ambiguous SMTP outcomes are never retried automatically.</p>
    <label htmlFor="email-address">Mailbox address</label><input id="email-address" type="email" autoComplete="username" value={emailAddress} onChange={event=>setEmailAddress(event.target.value)} disabled={busy}/>
    <label htmlFor="email-password">Mailbox password or app password</label><input id="email-password" type="password" autoComplete="new-password" value={emailPassword} onChange={event=>setEmailPassword(event.target.value)} disabled={busy}/>
    <label htmlFor="email-imap-host">IMAP server</label><input id="email-imap-host" value={imapHost} onChange={event=>setImapHost(event.target.value)} placeholder="imap.example.com" disabled={busy}/>
    <label htmlFor="email-smtp-host">SMTP server</label><input id="email-smtp-host" value={smtpHost} onChange={event=>setSmtpHost(event.target.value)} placeholder="smtp.example.com" disabled={busy}/>
    <label htmlFor="email-allowed-senders">Authorized sender addresses (comma separated)</label><input id="email-allowed-senders" value={allowedSenders} onChange={event=>setAllowedSenders(event.target.value)} placeholder="you@example.com" disabled={busy}/>
    <label htmlFor="email-authserv-id">Receiving mail server authserv-id</label><input id="email-authserv-id" value={authservId} onChange={event=>setAuthservId(event.target.value)} placeholder="mx.example.com" disabled={busy}/>
    <div className="controls"><button type="button" onClick={()=>void saveEmail()} disabled={busy||!emailAddress||!emailPassword||!imapHost||!smtpHost||!allowedSenders.trim()||!authservId.trim()}>Save email settings</button>{email?.configured&&<button type="button" onClick={()=>void clearEmail()} disabled={busy}>Clear email credentials</button>}</div>
    {email&&<p role="status">{email.configured?`Configured mailbox: ${email.address}`:'Email is not configured'} · {email.processed_messages} processed message(s){email.deliveries&&Object.keys(email.deliveries).length?` · ${Object.entries(email.deliveries).map(([state,count])=>`${state}: ${count}`).join(' · ')}`:''}</p>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
