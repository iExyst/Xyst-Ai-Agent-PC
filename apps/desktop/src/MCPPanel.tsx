import { useEffect, useState } from 'react';
import type { EngineClient, MCPPrompt, MCPPromptPreview, MCPResource, MCPResourceNotification, MCPResourceRead, MCPResourceTemplate, MCPServer, MCPTool } from './api/client.ts';

type Props = {
  client: EngineClient;
  selectedRunWriteTools: string[];
  onRunWriteToolsChange: (names: string[]) => void;
  selectedSamplingServers: string[];
  onSamplingServersChange: (serverIds: string[]) => void;
  onUsePromptText: (context: string) => void;
  refreshKey?: number;
};

type ResourceLimitDraft = { memory: string; cpu: string };

export function MCPPanel({ client, selectedRunWriteTools, onRunWriteToolsChange, selectedSamplingServers, onSamplingServersChange, onUsePromptText, refreshKey=0 }: Props) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [name, setName] = useState('');
  const [transport, setTransport] = useState<'stdio'|'streamable_http'>('stdio');
  const [protocolMode, setProtocolMode] = useState<'auto'|'legacy'>('auto');
  const [endpoint, setEndpoint] = useState('');
  const [trustEnvironment, setUseSystemProxy] = useState(false);
  const [networkIsolation, setNetworkIsolation] = useState(false);
  const [memoryLimitMb, setMemoryLimitMb] = useState('2048');
  const [cpuLimitPercent, setCpuLimitPercent] = useState('80');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('[]');
  const [cwd, setCwd] = useState('');
  const [secretEnv, setSecretEnv] = useState('');
  const [secrets, setSecrets] = useState<Record<string,string>>({});
  const [resourceLimitDrafts, setResourceLimitDrafts] = useState<Record<string,ResourceLimitDraft>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resourceCatalogs, setResourceCatalogs] = useState<Record<string,MCPResource[]>>({});
  const [resourceReads, setResourceReads] = useState<Record<string,MCPResourceRead>>({});
  const [resourceTemplateCatalogs, setResourceTemplateCatalogs] = useState<Record<string,MCPResourceTemplate[]>>({});
  const [resourceTemplateArguments, setResourceTemplateArguments] = useState<Record<string,string>>({});
  const [promptCatalogs, setPromptCatalogs] = useState<Record<string,MCPPrompt[]>>({});
  const [promptArguments, setPromptArguments] = useState<Record<string,string>>({});
  const [promptPreviews, setPromptPreviews] = useState<Record<string,MCPPromptPreview>>({});
  const [resourceSubscriptions, setResourceSubscriptions] = useState<Record<string,boolean>>({});
  const [resourceNotifications, setResourceNotifications] = useState<Record<string,MCPResourceNotification[]>>({});

  const refresh = async () => {
    const response: unknown = await client.mcpServers();
    if (!Array.isArray(response)) throw new Error('MCP server list response was invalid.');
    setServers(response as MCPServer[]);
  };
  useEffect(() => { void refresh().catch(e => setError(String(e))); }, [client, refreshKey]);
  const act = async (work:()=>Promise<unknown>) => {
    setBusy(true); setError('');
    try { await work(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const updateSelected = (tool:string, checked:boolean) => {
    const next = checked
      ? [...new Set([...selectedRunWriteTools, tool])]
      : selectedRunWriteTools.filter(item=>item!==tool);
    onRunWriteToolsChange(next);
  };
  const updateSamplingServer = (serverId:string, checked:boolean) => {
    const next = checked
      ? [...new Set([...selectedSamplingServers, serverId])]
      : selectedSamplingServers.filter(item=>item!==serverId);
    onSamplingServersChange(next);
  };
  const create = () => act(async()=>{
    const parsed:unknown = transport==='stdio'?JSON.parse(args):[];
    if (!Array.isArray(parsed) || parsed.some(item=>typeof item!=='string')) throw new Error('Arguments must be a JSON array of strings.');
    const memoryLimit = memoryLimitMb.trim()===''?null:Number(memoryLimitMb);
    const cpuLimit = cpuLimitPercent.trim()===''?null:Number(cpuLimitPercent);
    if (transport==='stdio'&&memoryLimit!==null&&(!Number.isInteger(memoryLimit)||memoryLimit<128||memoryLimit>65536)) throw new Error('Memory limit must be between 128 and 65,536 MiB.');
    if (transport==='stdio'&&cpuLimit!==null&&(!Number.isInteger(cpuLimit)||cpuLimit<1||cpuLimit>100)) throw new Error('CPU limit must be between 1 and 100 percent.');
    await client.createMCPServer({name,transport,protocol_mode:protocolMode,trust_environment:transport==='streamable_http'&&trustEnvironment,network_isolation:transport==='stdio'&&networkIsolation,memory_limit_mb:transport==='stdio'?memoryLimit:null,cpu_limit_percent:transport==='stdio'?cpuLimit:null,endpoint:transport==='streamable_http'?endpoint.trim():null,command:transport==='stdio'?command:'',args:transport==='stdio'?parsed:[],cwd:transport==='stdio'?(cwd.trim()||null):null,secret_env:secretEnv.split(/[\s,]+/).filter(Boolean)});
    setName('');setCommand('');setArgs('[]');setCwd('');setEndpoint('');setSecretEnv('');setProtocolMode('auto');setUseSystemProxy(false);setNetworkIsolation(false);setMemoryLimitMb('2048');setCpuLimitPercent('80');
  });
  const changePermission = (server:MCPServer,tool:MCPTool,permission:MCPTool['permission']) => act(async()=>{
    await client.setMCPToolPermission(server.id,tool,permission);
    if (permission!=='write') updateSelected(tool.qualified_name,false);
  });
  const saveResourceLimits = (server:MCPServer) => act(async()=>{
    const draft=resourceLimitDrafts[server.id]??{memory:server.memory_limit_mb?.toString()??'',cpu:server.cpu_limit_percent?.toString()??''};
    const memory=draft.memory.trim()===''?null:Number(draft.memory);
    const cpu=draft.cpu.trim()===''?null:Number(draft.cpu);
    if(memory!==null&&(!Number.isInteger(memory)||memory<128||memory>65536))throw new Error('Memory limit must be between 128 and 65,536 MiB.');
    if(cpu!==null&&(!Number.isInteger(cpu)||cpu<1||cpu>100))throw new Error('CPU limit must be between 1 and 100 percent.');
    await client.setMCPServerProcessLimits(server.id,{memory_limit_mb:memory,cpu_limit_percent:cpu});
    setResourceLimitDrafts(current=>({...current,[server.id]:{memory:memory?.toString()??'',cpu:cpu?.toString()??''}}));
  });

  return <section aria-label="MCP integrations">
    <h3>MCP servers</h3>
    <p>Configure a local stdio server or an HTTPS Streamable HTTP endpoint, then connect to discover tools. HTTP endpoints may use plain HTTP only on loopback. Each discovered tool starts disabled; write tools also need a per-run grant.</p>
    {error&&<p role="alert">{error}</p>}
    <div className="mcp-config">
      <label>Server name<input value={name} onChange={e=>setName(e.target.value)} /></label>
      <label>Transport<select value={transport} onChange={e=>setTransport(e.target.value as 'stdio'|'streamable_http')}><option value="stdio">Local process (stdio)</option><option value="streamable_http">Streamable HTTP</option></select></label>
      <label>MCP protocol<select value={protocolMode} onChange={e=>setProtocolMode(e.target.value as 'auto'|'legacy')}><option value="auto">Automatic (recommended)</option><option value="legacy">Legacy compatibility (sampling)</option></select></label>
      {protocolMode==='legacy'&&<p>Legacy mode is required for MCP sampling/createMessage. Only choose it when this server requires the older protocol; modern MCP features may be unavailable.</p>}
      {transport==='stdio'?<>
        <label>Command<input value={command} onChange={e=>setCommand(e.target.value)} placeholder="python" /></label>
        <label>Arguments (JSON string array)<input value={args} onChange={e=>setArgs(e.target.value)} /></label>
        <label>Working directory (optional)<input value={cwd} onChange={e=>setCwd(e.target.value)} /></label>
        <label>Process memory limit (MiB, 128–65,536; blank for unlimited)<input type="number" min={128} max={65536} step={128} value={memoryLimitMb} onChange={e=>setMemoryLimitMb(e.target.value)} /></label>
        <label>Process CPU limit (percent, 1–100; blank for unlimited)<input type="number" min={1} max={100} value={cpuLimitPercent} onChange={e=>setCpuLimitPercent(e.target.value)} /></label>
        <label><input type="checkbox" checked={networkIsolation} onChange={event=>setNetworkIsolation(event.target.checked)}/>Block network access for this MCP process (Windows)</label>
        <p>Uses Windows AppContainer with no network capabilities. The server can read its executable and package, and can access its configured working directory and package data. Some servers may not work in isolation.</p>
        <label>Secret environment variable names<input value={secretEnv} onChange={e=>setSecretEnv(e.target.value)} placeholder="API_TOKEN" /></label>
      </>:<>
        <label>Streamable HTTP endpoint<input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://mcp.example.com/mcp" /></label>
        <label>Bearer token variable name (optional)<input value={secretEnv} onChange={e=>setSecretEnv(e.target.value)} placeholder="MCP_TOKEN" /></label>
        <label><input type="checkbox" checked={trustEnvironment} onChange={event=>setUseSystemProxy(event.target.checked)}/>Use HTTP proxy and CA environment variables</label>
      </>}
      <button type="button" disabled={busy||!name.trim()||(transport==='stdio'?!command.trim():!endpoint.trim())} onClick={()=>void create()}>Add MCP server</button>
    </div>
    {servers.map(server=><article key={server.id} className="mcp-server">
      <h4>{server.name} <small>({server.state})</small></h4>
      <label><input type="checkbox" disabled={server.protocol_mode!=='legacy'} checked={selectedSamplingServers.includes(server.id)} onChange={event=>updateSamplingServer(server.id,event.target.checked)}/>Allow this server to request text-only model sampling during this run</label>
      <p>Sampling uses the selected run model, shares its iteration and token limits, and receives no tools, workspace, or conversation history. Server prompts remain untrusted. Set this server to Legacy compatibility when adding it; automatic negotiation cannot receive sampling/createMessage requests.</p>
      {server.error&&<p role="alert">{server.error}</p>}
      <code>{server.transport==='stdio'?`${server.command} ${server.args.join(' ')}`:server.endpoint}</code>
      {server.transport==='stdio'&&<div aria-label={`Process limits for ${server.name}`}>
        <label>Memory limit for {server.name} (MiB; blank is unlimited)<input type="number" min={128} max={65536} step={128} value={(resourceLimitDrafts[server.id]??{memory:server.memory_limit_mb?.toString()??'',cpu:server.cpu_limit_percent?.toString()??''}).memory} onChange={event=>setResourceLimitDrafts(current=>({...current,[server.id]:{...(current[server.id]??{memory:server.memory_limit_mb?.toString()??'',cpu:server.cpu_limit_percent?.toString()??''}),memory:event.target.value}}))}/></label>
        <label>CPU limit for {server.name} (percent; blank is unlimited)<input type="number" min={1} max={100} value={(resourceLimitDrafts[server.id]??{memory:server.memory_limit_mb?.toString()??'',cpu:server.cpu_limit_percent?.toString()??''}).cpu} onChange={event=>setResourceLimitDrafts(current=>({...current,[server.id]:{...(current[server.id]??{memory:server.memory_limit_mb?.toString()??'',cpu:server.cpu_limit_percent?.toString()??''}),cpu:event.target.value}}))}/></label>
        <button type="button" disabled={busy||server.state==='connected'} onClick={()=>void saveResourceLimits(server)}>Save process limits for {server.name}</button>
      </div>}
      {server.transport==='streamable_http'&&<p>HTTP proxy and CA environment variables: {server.trust_environment?'enabled':'disabled'}</p>}
      {server.transport==='stdio'&&<p>Network access: {server.network_isolation?'blocked by Windows AppContainer':'not isolated'}</p>}
      <div>
        <button type="button" disabled={busy||server.state==='connected'} onClick={()=>void act(()=>client.connectMCPServer(server.id))}>Connect and discover</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{await client.disconnectMCPServer(server.id);setResourceCatalogs(prev=>({...prev,[server.id]:[]}));setResourceTemplateCatalogs(prev=>({...prev,[server.id]:[]}));setResourceTemplateArguments(prev=>{const next={...prev};for(const key of Object.keys(next))if(key.startsWith(`${server.id}:`))delete next[key];return next;});setResourceReads(prev=>{const next={...prev};delete next[server.id];return next;});setResourceSubscriptions(prev=>{const next={...prev};for(const key of Object.keys(next))if(key.startsWith(`${server.id}:`))delete next[key];return next;});setResourceNotifications(prev=>({...prev,[server.id]:[]}));setPromptCatalogs(prev=>({...prev,[server.id]:[]}));setPromptPreviews(prev=>{const next={...prev};for(const key of Object.keys(next))if(key.startsWith(`${server.id}:`))delete next[key];return next;});})}>Disconnect</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const resources=await client.listMCPResources(server.id);setResourceCatalogs(prev=>({...prev,[server.id]:resources}));})}>List resources</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const notifications=await client.mcpResourceNotifications(server.id);setResourceNotifications(prev=>({...prev,[server.id]:[...(prev[server.id]??[]),...notifications].slice(-128)}));})}>Check for server updates</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const templates=await client.listMCPResourceTemplates(server.id);setResourceTemplateCatalogs(prev=>({...prev,[server.id]:templates}));})}>List resource templates</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const prompts=await client.listMCPPrompts(server.id);setPromptCatalogs(prev=>({...prev,[server.id]:prompts}));})}>List prompts</button>
        <button type="button" disabled={busy} onClick={()=>void act(async()=>{await client.deleteMCPServer(server.id);onRunWriteToolsChange(selectedRunWriteTools.filter(tool=>!tool.startsWith(`mcp_${server.id}_`)));onSamplingServersChange(selectedSamplingServers.filter(id=>id!==server.id));})}>Remove server</button>
      </div>
      {(resourceCatalogs[server.id]??[]).map(resource=><div key={resource.uri} className="mcp-resource">
        <strong>{resource.title||resource.name}</strong> <code>{resource.uri}</code>
        {resource.description&&<p>{resource.description}</p>}
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const key=`${server.id}:${resource.uri}`;const subscribed=!(resourceSubscriptions[key]??false);await client.setMCPResourceSubscription(server.id,resource.uri,subscribed);setResourceSubscriptions(prev=>({...prev,[key]:subscribed}));})}>{resourceSubscriptions[`${server.id}:${resource.uri}`]?'Unsubscribe from updates':'Subscribe to updates'}</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const result=await client.readMCPResource(server.id,resource.uri);setResourceReads(prev=>({...prev,[server.id]:result}));})}>Read resource</button>
      </div>)}
      {(resourceTemplateCatalogs[server.id]??[]).map(template=><div key={template.uri_template} className="mcp-resource-template">
        <strong>{template.title||template.name}</strong> <code>{template.uri_template}</code>
        {template.description&&<p>{template.description}</p>}
        <p>Variables: {template.variables.join(', ')||'none'}</p>
        <label>Arguments JSON<textarea rows={3} aria-label={`Arguments JSON for resource template ${template.name}`} value={resourceTemplateArguments[`${server.id}:${template.name}`]??'{}'} onChange={event=>setResourceTemplateArguments(prev=>({...prev,[`${server.id}:${template.name}`]:event.target.value}))}/></label>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const parsed:unknown=JSON.parse(resourceTemplateArguments[`${server.id}:${template.name}`]??'{}');if(typeof parsed!=='object'||parsed===null||Array.isArray(parsed)||Object.values(parsed).some(value=>typeof value!=='string'))throw new Error('Resource template arguments must be a JSON object with text values.');const result=await client.readMCPResourceTemplate(server.id,template.name,parsed as Record<string,string>);setResourceReads(prev=>({...prev,[server.id]:result}));})}>Read template resource</button>
      </div>)}
      {resourceReads[server.id]&&<aside aria-label="Untrusted MCP resource data">
        <strong>Untrusted MCP resource data</strong>
        <pre>{JSON.stringify(resourceReads[server.id],null,2)}</pre>
      </aside>}
      {(resourceNotifications[server.id]??[]).length>0&&<aside aria-label="Untrusted MCP server update notices"><strong>Untrusted MCP server update notices</strong><ul>{resourceNotifications[server.id]!.map((notice,index)=><li key={`${index}:${notice.kind}`}>{notice.kind==='resync_required'?'Update queue overflowed. Refresh the server catalog and inspect its tools and resources manually.':notice.kind==='tools_list_changed'?'The server tool catalog changed. Review newly discovered tools; each remains disabled until you grant permission.':`Server reports a possible change to ${notice.uri}. Read it explicitly to inspect the new content.`}</li>)}</ul></aside>}
      {(promptCatalogs[server.id]??[]).map(prompt=>{
        const key=`${server.id}:${prompt.name}`;
        return <div key={prompt.name} className="mcp-prompt">
          <strong>{prompt.title||prompt.name}</strong>
          {prompt.description&&<p>{prompt.description}</p>}
          {prompt.arguments.length>0&&<p>Arguments: {prompt.arguments.map(argument=>`${argument.name}${argument.required?' (required)':''}`).join(', ')}</p>}
          <label>Arguments JSON<textarea rows={3} aria-label={`Arguments JSON for ${prompt.name}`} value={promptArguments[key]??'{}'} onChange={event=>setPromptArguments(prev=>({...prev,[key]:event.target.value}))}/></label>
          <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const parsed:unknown=JSON.parse(promptArguments[key]??'{}');if(typeof parsed!=='object'||parsed===null||Array.isArray(parsed)||Object.values(parsed).some(value=>typeof value!=='string'))throw new Error('Prompt arguments must be a JSON object with text values.');const preview=await client.previewMCPPrompt(server.id,prompt.name,parsed as Record<string,string>);setPromptPreviews(prev=>({...prev,[key]:preview}));})}>Preview prompt</button>
          {promptPreviews[key]&&<aside aria-label="Untrusted MCP prompt preview"><strong>Untrusted MCP prompt preview</strong><pre>{JSON.stringify(promptPreviews[key],null,2)}</pre>
            {promptPreviews[key]!.messages.length>0&&promptPreviews[key]!.messages.every(message=>message.content.kind==='text'&&typeof message.content.text==='string')
              ? <button type="button" disabled={busy} onClick={()=>onUsePromptText(JSON.stringify({source:'MCP',server:server.name,prompt:prompt.name,messages:promptPreviews[key]!.messages.map(message=>({role:message.role,text:message.content.text}))},null,2))}>Add text to objective for review</button>
              : <p>Only text-only prompt previews can be added to an objective. Other content types stay available for inspection here.</p>}
          </aside>}
        </div>;
      })}
      {server.secret_env.map(env=>{const key=`${server.id}:${env}`;return <label key={env}>{env} {server.transport==='streamable_http'?'bearer token': 'environment secret'} {server.secret_env_configured[env]?'(saved in OS credential store)':'(not set)'}
        <input type="password" autoComplete="off" value={secrets[key]??''} onChange={e=>setSecrets(prev=>({...prev,[key]:e.target.value}))}/>
        <button type="button" disabled={busy||!secrets[key]} onClick={()=>void act(async()=>{await client.setMCPSecret(server.id,env,secrets[key]!);setSecrets(prev=>({...prev,[key]:''}));})}>Save secret</button>
        <button type="button" disabled={busy||!server.secret_env_configured[env]} onClick={()=>void act(()=>client.clearMCPSecret(server.id,env))}>Clear</button>
      </label>;})}
      {server.transport==='streamable_http'&&server.secret_env.length===0&&<p>MCP OAuth {server.oauth_configured?'authorization is saved in the OS credential store.':'will open a browser if this server requires sign-in.'} {server.oauth_configured&&<button type="button" disabled={busy} onClick={()=>void act(()=>client.clearMCPOAuth(server.id))}>Clear sign-in</button>}</p>}
      {server.tools.map(tool=><div key={tool.name} className="mcp-tool">
        <strong>{tool.title||tool.name}</strong><p>{tool.description||'No description supplied.'}</p>
        <label>Permission<select value={tool.permission} disabled={busy} onChange={e=>void changePermission(server,tool,e.target.value as MCPTool['permission'])}>
          <option value="none">Disabled</option><option value="read">Read</option><option value="write">Write</option>
        </select></label>
        {tool.permission==='write'&&<label><input type="checkbox" checked={selectedRunWriteTools.includes(tool.qualified_name)} disabled={busy} onChange={e=>updateSelected(tool.qualified_name,e.target.checked)}/> Allow this write tool for the next run</label>}
      </div>)}
    </article>)}
  </section>;
}
