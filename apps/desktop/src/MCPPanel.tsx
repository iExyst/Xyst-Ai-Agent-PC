import { useEffect, useState } from 'react';
import type { EngineClient, MCPPrompt, MCPPromptPreview, MCPResource, MCPResourceNotification, MCPResourceRead, MCPResourceTemplate, MCPServer, MCPTool } from './api/client.ts';

type Props = {
  client: EngineClient;
  selectedRunWriteTools: string[];
  onRunWriteToolsChange: (names: string[]) => void;
};

export function MCPPanel({ client, selectedRunWriteTools, onRunWriteToolsChange }: Props) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [name, setName] = useState('');
  const [transport, setTransport] = useState<'stdio'|'streamable_http'>('stdio');
  const [endpoint, setEndpoint] = useState('');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('[]');
  const [cwd, setCwd] = useState('');
  const [secretEnv, setSecretEnv] = useState('');
  const [secrets, setSecrets] = useState<Record<string,string>>({});
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
  useEffect(() => { void refresh().catch(e => setError(String(e))); }, [client]);
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
  const create = () => act(async()=>{
    const parsed:unknown = transport==='stdio'?JSON.parse(args):[];
    if (!Array.isArray(parsed) || parsed.some(item=>typeof item!=='string')) throw new Error('Arguments must be a JSON array of strings.');
    await client.createMCPServer({name,transport,endpoint:transport==='streamable_http'?endpoint.trim():null,command:transport==='stdio'?command:'',args:transport==='stdio'?parsed:[],cwd:transport==='stdio'?(cwd.trim()||null):null,secret_env:secretEnv.split(/[\s,]+/).filter(Boolean)});
    setName('');setCommand('');setArgs('[]');setCwd('');setEndpoint('');setSecretEnv('');
  });
  const changePermission = (server:MCPServer,tool:MCPTool,permission:MCPTool['permission']) => act(async()=>{
    await client.setMCPToolPermission(server.id,tool,permission);
    if (permission!=='write') updateSelected(tool.qualified_name,false);
  });

  return <section aria-label="MCP integrations">
    <h3>MCP servers</h3>
    <p>Configure a local stdio server or an HTTPS Streamable HTTP endpoint, then connect to discover tools. HTTP endpoints may use plain HTTP only on loopback. Each discovered tool starts disabled; write tools also need a per-run grant.</p>
    {error&&<p role="alert">{error}</p>}
    <div className="mcp-config">
      <label>Server name<input value={name} onChange={e=>setName(e.target.value)} /></label>
      <label>Transport<select value={transport} onChange={e=>setTransport(e.target.value as 'stdio'|'streamable_http')}><option value="stdio">Local process (stdio)</option><option value="streamable_http">Streamable HTTP</option></select></label>
      {transport==='stdio'?<>
        <label>Command<input value={command} onChange={e=>setCommand(e.target.value)} placeholder="python" /></label>
        <label>Arguments (JSON string array)<input value={args} onChange={e=>setArgs(e.target.value)} /></label>
        <label>Working directory (optional)<input value={cwd} onChange={e=>setCwd(e.target.value)} /></label>
        <label>Secret environment variable names<input value={secretEnv} onChange={e=>setSecretEnv(e.target.value)} placeholder="API_TOKEN" /></label>
      </>:<>
        <label>Streamable HTTP endpoint<input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://mcp.example.com/mcp" /></label>
        <label>Bearer token variable name (optional)<input value={secretEnv} onChange={e=>setSecretEnv(e.target.value)} placeholder="MCP_TOKEN" /></label>
      </>}
      <button type="button" disabled={busy||!name.trim()||(transport==='stdio'?!command.trim():!endpoint.trim())} onClick={()=>void create()}>Add MCP server</button>
    </div>
    {servers.map(server=><article key={server.id} className="mcp-server">
      <h4>{server.name} <small>({server.state})</small></h4>
      {server.error&&<p role="alert">{server.error}</p>}
      <code>{server.transport==='stdio'?`${server.command} ${server.args.join(' ')}`:server.endpoint}</code>
      <div>
        <button type="button" disabled={busy||server.state==='connected'} onClick={()=>void act(()=>client.connectMCPServer(server.id))}>Connect and discover</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{await client.disconnectMCPServer(server.id);setResourceCatalogs(prev=>({...prev,[server.id]:[]}));setResourceTemplateCatalogs(prev=>({...prev,[server.id]:[]}));setResourceTemplateArguments(prev=>{const next={...prev};for(const key of Object.keys(next))if(key.startsWith(`${server.id}:`))delete next[key];return next;});setResourceReads(prev=>{const next={...prev};delete next[server.id];return next;});setResourceSubscriptions(prev=>{const next={...prev};for(const key of Object.keys(next))if(key.startsWith(`${server.id}:`))delete next[key];return next;});setResourceNotifications(prev=>({...prev,[server.id]:[]}));setPromptCatalogs(prev=>({...prev,[server.id]:[]}));setPromptPreviews(prev=>{const next={...prev};for(const key of Object.keys(next))if(key.startsWith(`${server.id}:`))delete next[key];return next;});})}>Disconnect</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const resources=await client.listMCPResources(server.id);setResourceCatalogs(prev=>({...prev,[server.id]:resources}));})}>List resources</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const notifications=await client.mcpResourceNotifications(server.id);setResourceNotifications(prev=>({...prev,[server.id]:[...(prev[server.id]??[]),...notifications].slice(-128)}));})}>Check for server updates</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const templates=await client.listMCPResourceTemplates(server.id);setResourceTemplateCatalogs(prev=>({...prev,[server.id]:templates}));})}>List resource templates</button>
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const prompts=await client.listMCPPrompts(server.id);setPromptCatalogs(prev=>({...prev,[server.id]:prompts}));})}>List prompts</button>
        <button type="button" disabled={busy} onClick={()=>void act(async()=>{await client.deleteMCPServer(server.id);onRunWriteToolsChange(selectedRunWriteTools.filter(tool=>!tool.startsWith(`mcp_${server.id}_`)));})}>Remove server</button>
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
      {(resourceNotifications[server.id]??[]).length>0&&<aside aria-label="Untrusted MCP server update notices"><strong>Untrusted MCP server update notices</strong><ul>{resourceNotifications[server.id]!.map((notice,index)=><li key={`${index}:${notice.kind}`}>{notice.kind==='resync_required'?'Update queue overflowed. Refresh the resource catalog and inspect resources manually.':`Server reports a possible change to ${notice.uri}. Read it explicitly to inspect the new content.`}</li>)}</ul></aside>}
      {(promptCatalogs[server.id]??[]).map(prompt=>{
        const key=`${server.id}:${prompt.name}`;
        return <div key={prompt.name} className="mcp-prompt">
          <strong>{prompt.title||prompt.name}</strong>
          {prompt.description&&<p>{prompt.description}</p>}
          {prompt.arguments.length>0&&<p>Arguments: {prompt.arguments.map(argument=>`${argument.name}${argument.required?' (required)':''}`).join(', ')}</p>}
          <label>Arguments JSON<textarea rows={3} aria-label={`Arguments JSON for ${prompt.name}`} value={promptArguments[key]??'{}'} onChange={event=>setPromptArguments(prev=>({...prev,[key]:event.target.value}))}/></label>
          <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(async()=>{const parsed:unknown=JSON.parse(promptArguments[key]??'{}');if(typeof parsed!=='object'||parsed===null||Array.isArray(parsed)||Object.values(parsed).some(value=>typeof value!=='string'))throw new Error('Prompt arguments must be a JSON object with text values.');const preview=await client.previewMCPPrompt(server.id,prompt.name,parsed as Record<string,string>);setPromptPreviews(prev=>({...prev,[key]:preview}));})}>Preview prompt</button>
          {promptPreviews[key]&&<aside aria-label="Untrusted MCP prompt preview"><strong>Untrusted MCP prompt preview</strong><pre>{JSON.stringify(promptPreviews[key],null,2)}</pre></aside>}
        </div>;
      })}
      {server.secret_env.map(env=>{const key=`${server.id}:${env}`;return <label key={env}>{env} {server.transport==='streamable_http'?'bearer token': 'environment secret'} {server.secret_env_configured[env]?'(saved in OS credential store)':'(not set)'}
        <input type="password" autoComplete="off" value={secrets[key]??''} onChange={e=>setSecrets(prev=>({...prev,[key]:e.target.value}))}/>
        <button type="button" disabled={busy||!secrets[key]} onClick={()=>void act(async()=>{await client.setMCPSecret(server.id,env,secrets[key]!);setSecrets(prev=>({...prev,[key]:''}));})}>Save secret</button>
        <button type="button" disabled={busy||!server.secret_env_configured[env]} onClick={()=>void act(()=>client.clearMCPSecret(server.id,env))}>Clear</button>
      </label>;})}
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
