import {useEffect,useState} from 'react';
import {open} from '@tauri-apps/plugin-dialog';
import type {A2AListenerStatus,ChannelIdentityBinding,EmailChannelStatus,EngineClient,TelegramChannelStatus,WebhookChannelStatus} from './api/client.ts';
import {InlineConfirmation} from './InlineConfirmation.tsx';

export function ChannelsPanel({client}:{client:EngineClient}) {
  const [status,setStatus]=useState<TelegramChannelStatus>();
  const [token,setToken]=useState('');
  const [pairing,setPairing]=useState<{code:string;expires_at:string}>();
  const [busy,setBusy]=useState(false);
  const [confirmRevoke,setConfirmRevoke]=useState(false);
  const [reconcileChoice,setReconcileChoice]=useState<{updateId:number;resolution:'delivered'|'retry'}>();
  const [emailReconcileChoice,setEmailReconcileChoice]=useState<{uidValidity:string;uid:string;resolution:'delivered'|'retry'}>();
  const [error,setError]=useState('');
  const [email,setEmail]=useState<EmailChannelStatus>();
  const [webhook,setWebhook]=useState<WebhookChannelStatus>();
  const [a2a,setA2a]=useState<{configured:boolean}>();
  const [a2aListener,setA2aListener]=useState<A2AListenerStatus>();
  const [a2aSecret,setA2aSecret]=useState('');
  const [a2aNetwork,setA2aNetwork]=useState(false);
  const [a2aPort,setA2aPort]=useState('8766');
  const [a2aTlsCertPath,setA2aTlsCertPath]=useState('');
  const [a2aTlsKeyPath,setA2aTlsKeyPath]=useState('');
  const [identityBindings,setIdentityBindings]=useState<ChannelIdentityBinding[]>([]);
  const [identitySource,setIdentitySource]=useState('');
  const [identityTarget,setIdentityTarget]=useState('');
  const [sharedHistoryBinding,setSharedHistoryBinding]=useState('');
  const [identityAddSelections,setIdentityAddSelections]=useState<Record<string,string>>({});
  const [identityMergeSelections,setIdentityMergeSelections]=useState<Record<string,string>>({});
  const [confirmIdentityMerge,setConfirmIdentityMerge]=useState<{targetLinkId:string;sourceLinkId:string}>();
  const [confirmIdentityRevoke,setConfirmIdentityRevoke]=useState('');
  const [webhookSecret,setWebhookSecret]=useState('');
  const [emailAddress,setEmailAddress]=useState('');
  const [emailPassword,setEmailPassword]=useState('');
  const [imapHost,setImapHost]=useState('');
  const [smtpHost,setSmtpHost]=useState('');
  const [allowedSenders,setAllowedSenders]=useState('');
  const [authservId,setAuthservId]=useState('');

  async function refresh() {
    try {setStatus(await client.telegramStatus());setWebhook(await client.webhookStatus());setA2a(await client.a2aStatus());const listener=await client.a2aListenerStatus();setA2aListener(listener);setA2aNetwork(listener.allow_network);setA2aPort(String(listener.port));setA2aTlsCertPath(listener.tls_cert_path??'');setA2aTlsKeyPath(listener.tls_key_path??'');setIdentityBindings(await client.channelIdentityBindings());setError('');}
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

  async function linkChannelIdentities() {
    if(!identitySource||!identityTarget||identitySource===identityTarget||!sharedHistoryBinding)return;
    setBusy(true);setError('');
    try {await client.createChannelIdentityLink([identitySource,identityTarget],sharedHistoryBinding);setIdentitySource('');setIdentityTarget('');setSharedHistoryBinding('');await refresh();}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not link these channel identities.');}
    finally {setBusy(false);}
  }

  async function unlinkChannelIdentities(linkId:string) {
    setBusy(true);setError('');
    try {await client.revokeChannelIdentityLink(linkId);setConfirmIdentityRevoke('');await refresh();}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not unlink these channel identities.');}
    finally {setBusy(false);}
  }

  async function addChannelIdentity(linkId:string) {
    const bindingId=identityAddSelections[linkId];
    if(!bindingId)return;
    setBusy(true);setError('');
    try {await client.addChannelIdentityLinkMember(linkId,bindingId);setIdentityAddSelections(current=>({...current,[linkId]:''}));await refresh();}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not add this account to the linked conversation.');}
    finally {setBusy(false);}
  }

  async function mergeChannelIdentities() {
    if(!confirmIdentityMerge)return;
    setBusy(true);setError('');
    try {await client.mergeChannelIdentityLinks(confirmIdentityMerge.targetLinkId,confirmIdentityMerge.sourceLinkId);setConfirmIdentityMerge(undefined);setIdentityMergeSelections(current=>({...current,[confirmIdentityMerge.targetLinkId]:''}));await refresh();}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not merge these channel identity groups.');}
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

  async function createA2ACredential() {
    setBusy(true);setError('');
    try {const result=await client.createA2ACredential();setA2a({configured:result.configured});setA2aSecret(result.secret);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not create the A2A peer credential.');}
    finally {setBusy(false);}
  }

  async function clearA2ACredential() {
    setBusy(true);setError('');
    try {await client.clearA2ACredential();setA2a({configured:false});setA2aSecret('');await refresh();}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not clear the A2A peer credential.');}
    finally {setBusy(false);}
  }

  async function saveA2AListener(enabled:boolean) {
    const port=Number(a2aPort);
    if(!Number.isInteger(port)||port<1024||port>65535){setError('Choose an A2A port from 1024 through 65535.');return;}
    setBusy(true);setError('');
    try {setA2aListener(await client.updateA2AListener({enabled,bind_address:a2aNetwork?'0.0.0.0':'127.0.0.1',port,allow_network:a2aNetwork,tls_cert_path:a2aTlsCertPath||null,tls_key_path:a2aTlsKeyPath||null}));}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not update the A2A listener.');}
    finally {setBusy(false);}
  }

  async function chooseA2aTlsCert() {
    try {
      const selected=await open({directory:false,multiple:false,title:'Select A2A TLS certificate',filters:[{name:'PEM certificate',extensions:['pem','crt','cer']}]});
      if(typeof selected==='string')setA2aTlsCertPath(selected);
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not select the A2A TLS certificate.');}
  }

  async function chooseA2aTlsKey() {
    try {
      const selected=await open({directory:false,multiple:false,title:'Select A2A TLS private key',filters:[{name:'PEM private key',extensions:['pem','key']}]});
      if(typeof selected==='string')setA2aTlsKeyPath(selected);
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not select the A2A TLS private key.');}
  }

  return <section aria-labelledby="channels-title">
    <h2 id="channels-title">Messaging channels</h2>
    <h3>Cross-channel identity links</h3>
    <p>Link only accounts you control. Linking shares the selected account's saved conversation history and future conversation context with both accounts. The other account's previous history is not merged. Unlinking starts separate empty conversations; the shared history stays available in local session history.</p>
    {(()=>{
      const available=identityBindings.filter(binding=>binding.identity_link_id===null&&binding.authorized);
      const source=available.find(binding=>binding.id===identitySource);
      const targets=available.filter(binding=>binding.id!==identitySource&&binding.channel!==source?.channel);
      const groups=new Map<string,ChannelIdentityBinding[]>();
      for(const binding of identityBindings)if(binding.identity_link_id){groups.set(binding.identity_link_id,[...(groups.get(binding.identity_link_id)??[]),binding]);}
      return <>
        <label htmlFor="identity-link-source">First authorized account</label>
        <select id="identity-link-source" value={identitySource} onChange={event=>{setIdentitySource(event.target.value);setIdentityTarget('');setSharedHistoryBinding(event.target.value);}} disabled={busy}>
          <option value="">Choose an account</option>
          {available.map(binding=><option key={binding.id} value={binding.id}>{binding.channel} · {binding.external_identity} · account {binding.account_id}</option>)}
        </select>
        <label htmlFor="identity-link-target">Second account from another channel</label>
        <select id="identity-link-target" value={identityTarget} onChange={event=>setIdentityTarget(event.target.value)} disabled={busy||!source}>
          <option value="">Choose a different channel account</option>
          {targets.map(binding=><option key={binding.id} value={binding.id}>{binding.channel} · {binding.external_identity} · account {binding.account_id}</option>)}
        </select>
        {identitySource&&identityTarget&&<fieldset><legend>Conversation history to share</legend>
          {[identitySource,identityTarget].map(id=>{const binding=available.find(item=>item.id===id);return binding?<label key={id}><input type="radio" name="shared-history" value={id} checked={sharedHistoryBinding===id} onChange={()=>setSharedHistoryBinding(id)} disabled={busy}/>{binding.channel} · {binding.external_identity} · {binding.title??'saved session'}</label>:null;})}
        </fieldset>}
        <button type="button" onClick={()=>void linkChannelIdentities()} disabled={busy||!identitySource||!identityTarget||!sharedHistoryBinding}>Link accounts and share selected history</button>
        {groups.size>0&&<ul aria-label="Active cross-channel identity links">{[...groups].map(([linkId,members])=>{
          const memberChannels=new Set(members.map(binding=>binding.channel));
          const additions=available.filter(binding=>!memberChannels.has(binding.channel));
          const mergeSources=members.every(binding=>binding.authorized)?[...groups].filter(([sourceId,sourceMembers])=>sourceId!==linkId&&sourceMembers.every(binding=>binding.authorized)&&sourceMembers.every(binding=>!memberChannels.has(binding.channel))):[];
          const addLabel=`Account to add to ${members.map(binding=>binding.external_identity).join(' and ')}`;
          return <li key={linkId}>
            <span>{members.map(binding=>`${binding.channel}: ${binding.external_identity}${binding.authorized?'':' (not currently authorized)'}`).join(' ↔ ')} · shared history from {members[0]?.session_channel}</span>
            {additions.length>0&&<div><label htmlFor={`identity-add-${linkId}`}>{addLabel}</label><select id={`identity-add-${linkId}`} value={identityAddSelections[linkId]??''} onChange={event=>setIdentityAddSelections(current=>({...current,[linkId]:event.target.value}))} disabled={busy}><option value="">Choose another channel account</option>{additions.map(binding=><option key={binding.id} value={binding.id}>{binding.channel} · {binding.external_identity} · account {binding.account_id}</option>)}</select><button type="button" onClick={()=>void addChannelIdentity(linkId)} disabled={busy||!identityAddSelections[linkId]}>Add account to linked conversation</button></div>}
            {mergeSources.length>0&&<div><label htmlFor={`identity-merge-${linkId}`}>Other identity group to add to this group's history</label><select id={`identity-merge-${linkId}`} value={identityMergeSelections[linkId]??''} onChange={event=>setIdentityMergeSelections(current=>({...current,[linkId]:event.target.value}))} disabled={busy}><option value="">Choose another identity group</option>{mergeSources.map(([sourceId,sourceMembers])=><option key={sourceId} value={sourceId}>{sourceMembers.map(binding=>`${binding.channel}: ${binding.external_identity}`).join(' ↔ ')}</option>)}</select><button type="button" onClick={()=>setConfirmIdentityMerge({targetLinkId:linkId,sourceLinkId:identityMergeSelections[linkId]??''})} disabled={busy||!identityMergeSelections[linkId]} aria-expanded={confirmIdentityMerge?.targetLinkId===linkId} aria-controls={`identity-merge-confirmation-${linkId}`}>Merge into this group's history</button></div>}
            <InlineConfirmation id={`identity-merge-confirmation-${linkId}`} open={confirmIdentityMerge?.targetLinkId===linkId} label="Confirm identity group merge"><p>This group’s selected history will stay active. The other group’s members will continue in it, and that group’s earlier session will remain in local session history without being combined. Both groups must still be authorized.</p><button type="button" onClick={()=>void mergeChannelIdentities()} disabled={busy}>Confirm merge into this history</button><button type="button" onClick={()=>setConfirmIdentityMerge(undefined)} disabled={busy}>Cancel</button></InlineConfirmation>
            <button type="button" onClick={()=>setConfirmIdentityRevoke(linkId)} disabled={busy||confirmIdentityRevoke===linkId} aria-expanded={confirmIdentityRevoke===linkId} aria-controls={`identity-unlink-confirmation-${linkId}`}>Unlink these accounts</button>
            <InlineConfirmation id={`identity-unlink-confirmation-${linkId}`} open={confirmIdentityRevoke===linkId} label="Confirm unlink accounts"><p>All linked accounts will get separate new conversations. The shared session remains in local history.</p><button type="button" onClick={()=>void unlinkChannelIdentities(linkId)} disabled={busy}>Confirm unlink</button><button type="button" onClick={()=>setConfirmIdentityRevoke('')} disabled={busy}>Cancel</button></InlineConfirmation>
          </li>;
        })}</ul>}
      </>;
    })()}
    <h3>Agent-to-agent (A2A)</h3>
    <p>A2A text requests use the shared coordinator in Observe mode with no workspace or tool grants. The peer secret is stored in Windows Credential Manager and shown once when created.</p>
    <div className="controls">
      {!a2a?.configured&&<button type="button" onClick={()=>void createA2ACredential()} disabled={busy}>Create A2A peer credential</button>}
      {a2a?.configured&&<button type="button" onClick={()=>void clearA2ACredential()} disabled={busy}>Clear A2A peer credential</button>}
    </div>
    {a2aSecret&&<section aria-label="New A2A peer credential"><p>Copy this secret to the connecting peer now. X.A.A. will not show it again.</p><code>{a2aSecret}</code></section>}
    {a2a?.configured&&<>
      <label htmlFor="a2a-listener-port">A2A listener port</label>
      <input id="a2a-listener-port" type="number" min={1024} max={65535} value={a2aPort} onChange={event=>setA2aPort(event.target.value)} disabled={busy}/>
      <label htmlFor="a2a-tls-cert">A2A TLS certificate (PEM)</label>
      <div className="controls"><input id="a2a-tls-cert" type="text" value={a2aTlsCertPath} onChange={event=>setA2aTlsCertPath(event.target.value)} disabled={busy} /><button type="button" onClick={()=>void chooseA2aTlsCert()} disabled={busy}>Choose certificate</button></div>
      <label htmlFor="a2a-tls-key">A2A TLS private key (PEM)</label>
      <div className="controls"><input id="a2a-tls-key" type="text" value={a2aTlsKeyPath} onChange={event=>setA2aTlsKeyPath(event.target.value)} disabled={busy} /><button type="button" onClick={()=>void chooseA2aTlsKey()} disabled={busy}>Choose private key</button></div>
      <label htmlFor="a2a-network-access"><input id="a2a-network-access" type="checkbox" checked={a2aNetwork} onChange={event=>setA2aNetwork(event.target.checked)} disabled={busy}/> Allow TLS connections from other devices on this network</label>
      <p>Network access requires a matching certificate and private key. Peers must trust the certificate and use a hostname or IP address listed in it. The listener encrypts peer credentials and task contents; only the Agent Card and A2A task endpoint are reachable, while the owner API stays on loopback. Protect the private-key file and keep Windows Firewall rules scoped.</p>
      <div className="controls">
        <button type="button" onClick={()=>void saveA2AListener(true)} disabled={busy}>{a2aListener?.listening?'Apply A2A listener settings':'Enable A2A listener'}</button>
        {a2aListener?.enabled&&<button type="button" onClick={()=>void saveA2AListener(false)} disabled={busy}>Disable A2A listener</button>}
      </div>
      {a2aListener&&<p role="status">A2A listener {a2aListener.listening?'is listening':'is stopped'} · {a2aListener.bind_address}:{a2aListener.port}</p>}
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
        <button type="button" onClick={()=>setConfirmRevoke(true)} disabled={busy||confirmRevoke} aria-expanded={confirmRevoke} aria-controls="telegram-revoke-confirmation">Revoke all linked users</button>
        <InlineConfirmation id="telegram-revoke-confirmation" open={confirmRevoke} label="Confirm revoke Telegram links"><p>This prevents {status.linked_users} linked user(s) from submitting new objectives and cancels queued scheduled messages. A send already in progress cannot be recalled; saved runs remain available.</p><button type="button" onClick={()=>void revokeLinks()} disabled={busy}>Confirm revoke links</button><button type="button" onClick={()=>setConfirmRevoke(false)} disabled={busy}>Keep links</button></InlineConfirmation>
      </div>}
      {pairing&&<div aria-label="Telegram pairing instructions"><p>Within five minutes, send this exact command in a private chat with @{status.bot_username??'your bot'}:</p><code>/pair {pairing.code}</code><p>Expires {new Date(pairing.expires_at).toLocaleString()}.</p></div>}
      {status.deliveries&&Object.keys(status.deliveries).length>0&&<p>Delivery records: {Object.entries(status.deliveries).map(([state,count])=>`${state}: ${count}`).join(' · ')}</p>}
      {(status.unknown_deliveries??[]).length>0&&<section aria-label="Telegram deliveries needing review">
        <h4>Unconfirmed replies</h4>
        <p>Open the listed private chat and check whether X.A.A.'s reply arrived before choosing an action. X.A.A. never retries an unclear send automatically.</p>
        <ul>{(status.unknown_deliveries??[]).map(delivery=><li key={delivery.update_id}>
          <span>Update {delivery.update_id} · Telegram user {delivery.user_id??'unknown'} · run {delivery.run_id??'unavailable'} · {new Date(delivery.updated_at).toLocaleString()}</span>
          <div className="controls">
            <button type="button" onClick={()=>setReconcileChoice({updateId:delivery.update_id,resolution:'delivered'})} disabled={busy} aria-expanded={reconcileChoice?.updateId===delivery.update_id} aria-controls={`telegram-delivery-confirmation-${delivery.update_id}`}>I checked; mark delivered</button>
            <button type="button" onClick={()=>setReconcileChoice({updateId:delivery.update_id,resolution:'retry'})} disabled={busy} aria-expanded={reconcileChoice?.updateId===delivery.update_id} aria-controls={`telegram-delivery-confirmation-${delivery.update_id}`}>I checked; retry reply</button>
          </div>
          <InlineConfirmation id={`telegram-delivery-confirmation-${delivery.update_id}`} open={reconcileChoice?.updateId===delivery.update_id} label="Confirm Telegram delivery reconciliation">
            <p>{reconcileChoice?.resolution==='retry'?'Retrying can send a duplicate if the first reply actually arrived.':'This records that you found the reply in Telegram.'} Confirm that you checked the destination chat.</p>
            <button type="button" onClick={()=>void reconcileTelegramDelivery()} disabled={busy}>Confirm {reconcileChoice?.resolution==='retry'?'retry':'delivered'}</button>
            <button type="button" onClick={()=>setReconcileChoice(undefined)} disabled={busy}>Cancel</button>
          </InlineConfirmation>
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
            <button type="button" onClick={()=>setEmailReconcileChoice({uidValidity:delivery.uid_validity,uid:delivery.uid,resolution:'delivered'})} disabled={busy} aria-expanded={emailReconcileChoice?.uid===delivery.uid&&emailReconcileChoice.uidValidity===delivery.uid_validity} aria-controls={`email-delivery-confirmation-${delivery.uid_validity}-${delivery.uid}`}>I checked; mark email delivered</button>
            <button type="button" onClick={()=>setEmailReconcileChoice({uidValidity:delivery.uid_validity,uid:delivery.uid,resolution:'retry'})} disabled={busy} aria-expanded={emailReconcileChoice?.uid===delivery.uid&&emailReconcileChoice.uidValidity===delivery.uid_validity} aria-controls={`email-delivery-confirmation-${delivery.uid_validity}-${delivery.uid}`}>I checked; retry email</button>
          </div>
          <InlineConfirmation id={`email-delivery-confirmation-${delivery.uid_validity}-${delivery.uid}`} open={emailReconcileChoice?.uid===delivery.uid&&emailReconcileChoice.uidValidity===delivery.uid_validity} label="Confirm email delivery reconciliation">
            <p>{emailReconcileChoice?.resolution==='retry'?'Retrying may send a duplicate if the SMTP server accepted the first reply.':'This records that you found the reply in the Sent folder.'} Confirm that you checked the mailbox and reply destination.</p>
            <button type="button" onClick={()=>void reconcileEmailDelivery()} disabled={busy}>Confirm {emailReconcileChoice?.resolution==='retry'?'retry':'delivered'}</button>
            <button type="button" onClick={()=>setEmailReconcileChoice(undefined)} disabled={busy}>Cancel</button>
          </InlineConfirmation>
        </li>)}</ul>
      </section>}
    </>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
