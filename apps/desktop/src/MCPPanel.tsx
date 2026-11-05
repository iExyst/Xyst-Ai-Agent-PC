import { useEffect, useState } from 'react';
import type { EngineClient, MCPServer, MCPTool } from './api/client.ts';

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
        <button type="button" disabled={busy||server.state!=='connected'} onClick={()=>void act(()=>client.disconnectMCPServer(server.id))}>Disconnect</button>
        <button type="button" disabled={busy} onClick={()=>void act(async()=>{await client.deleteMCPServer(server.id);onRunWriteToolsChange(selectedRunWriteTools.filter(tool=>!tool.startsWith(`mcp_${server.id}_`)));})}>Remove server</button>
      </div>
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
