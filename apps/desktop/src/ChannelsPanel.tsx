import {useEffect,useState} from 'react';
import type {EngineClient,TelegramChannelStatus} from './api/client.ts';

export function ChannelsPanel({client}:{client:EngineClient}) {
  const [status,setStatus]=useState<TelegramChannelStatus>();
  const [token,setToken]=useState('');
  const [pairing,setPairing]=useState<{code:string;expires_at:string}>();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');

  async function refresh() {
    try {setStatus(await client.telegramStatus());setError('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not read Telegram status.');}
  }
  useEffect(()=>{void refresh();},[client]);

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
      {pairing&&<div aria-label="Telegram pairing instructions"><p>Within five minutes, send this exact command in a private chat with @{status.bot_username??'your bot'}:</p><code>/pair {pairing.code}</code><p>Expires {new Date(pairing.expires_at).toLocaleString()}.</p></div>}
      {status.deliveries&&Object.keys(status.deliveries).length>0&&<p>Delivery records: {Object.entries(status.deliveries).map(([state,count])=>`${state}: ${count}`).join(' · ')}</p>}
    </>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
