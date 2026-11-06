import { useState } from 'react';
import type { EngineClient, PluginDiscovery } from './api/client.ts';

export function AgentPluginsPanel({client}:{client:EngineClient}) {
  const [root,setRoot]=useState('');
  const [result,setResult]=useState<PluginDiscovery>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  async function discover() {
    setBusy(true);setError('');
    try {setResult(await client.discoverAgentPlugins(root.trim()));}
    catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  return <section aria-label="Agent plugin package discovery">
    <h2>Agent plugin packages</h2>
    <p>Read-only package inspection. Discovery does not run commands, connect servers, import skill content, or register tools.</p>
    <label htmlFor="plugin-root">Package folder</label>
    <div><input id="plugin-root" value={root} onChange={event=>setRoot(event.target.value)} maxLength={2048} placeholder="C:\\path\\to\\plugins"/>
    <button disabled={busy||!root.trim()} onClick={()=>void discover()}>{busy?'Discovering…':'Discover packages'}</button></div>
    {error&&<p role="alert">{error}</p>}
    {result&&<>
      <p>Scanned {result.root}. {result.packages.length} package(s) found.</p>
      {result.packages.map(item=><article key={item.path}>
        <h3>{item.name}{item.version?` · ${item.version}`:''}</h3>{item.description&&<p>{item.description}</p>}
        <p>Package: <code>{item.path}</code></p>
        <h4>Skills ({item.skills.length})</h4><ul>{item.skills.map(skill=><li key={skill.path}>{skill.name} <code>{skill.path}</code></li>)}</ul>
        <h4>MCP servers ({item.servers.length})</h4><ul>{item.servers.map(server=><li key={server.name}>{server.name}: {server.transport==='stdio'?`stdio · ${server.command_name} · ${server.argument_count} arguments · environment names: ${server.environment_names.join(', ')||'none'}`:`${server.transport} · ${server.endpoint}`}</li>)}</ul>
      </article>)}
      {result.diagnostics.length>0&&<><h3>Package diagnostics</h3><ul>{result.diagnostics.map((diagnostic,index)=><li key={`${diagnostic.path}:${index}`}><code>{diagnostic.path}</code>: {diagnostic.message}</li>)}</ul></>}
    </>}
  </section>;
}
