import { useState } from 'react';
import type { EngineClient, PluginDiscovery } from './api/client.ts';

function declaredRequirementsSummary(value: Record<string, unknown> | undefined): string {
  if (!value) return 'No supported dependency metadata found.';
  const names = (key: string) => {
    const items = value[key];
    return Array.isArray(items) ? items.filter((item): item is string => typeof item === 'string').join(', ') : '';
  };
  const dependencies = value.dependencies_declared === true
    ? `declared (${typeof value.dependency_count === 'number' ? value.dependency_count : 'count unknown'})`
    : 'not declared';
  return [
    `Platforms: ${names('platforms') || 'not declared'}`,
    `Hermes toolsets: ${names('hermes_toolsets') || 'not declared'}`,
    `Prerequisite fields: ${names('prerequisite_fields') || 'none'}`,
    `Command names: ${names('required_command_names') || 'none'}`,
    `Environment variable names: ${names('required_environment_names') || 'none'}`,
    `Dependencies: ${dependencies}`,
  ].join(' · ');
}

export function AgentPluginsPanel({client,onImported}:{client:EngineClient;onImported:()=>void}) {
  const [root,setRoot]=useState('');
  const [result,setResult]=useState<PluginDiscovery>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  async function discover() {
    setBusy(true);setError('');
    try {setResult(await client.discoverAgentPlugins(root.trim()));}
    catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  async function importSkill(packageName:string,skillName:string) {
    setBusy(true);setError('');setNotice('');
    try {
      const created=await client.importAgentPluginSkill({root:result?.root??root.trim(),package_name:packageName,skill_name:skillName});
      setNotice(`${created.name} was imported as an inactive candidate. Review it in Reusable procedures before trials or activation.`);
      onImported();
    } catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  return <section aria-label="Agent plugin package discovery">
    <h2>Agent plugin packages</h2>
    <p>Discovery does not run commands, connect servers, or register tools. Importing a skill reads its bounded text into an inactive candidate for review.</p>
    <label htmlFor="plugin-root">Package folder</label>
    <div><input id="plugin-root" value={root} onChange={event=>setRoot(event.target.value)} maxLength={2048} placeholder="C:\\path\\to\\plugins"/>
    <button disabled={busy||!root.trim()} onClick={()=>void discover()}>{busy?'Discovering…':'Discover packages'}</button></div>
    {error&&<p role="alert">{error}</p>}
    {notice&&<p role="status">{notice}</p>}
    {result&&<>
      <p>Scanned {result.root}. {result.packages.length} package(s) found.</p>
      {result.packages.map(item=><article key={item.path}>
        <h3>{item.name}{item.version?` · ${item.version}`:''}</h3>{item.description&&<p>{item.description}</p>}
        <p>Package: <code>{item.path}</code></p>
        <h4>Skills ({item.skills.length})</h4><ul>{item.skills.map(skill=><li key={skill.path}><strong>{skill.name}</strong>: {skill.description} <code>{skill.path}</code><p>Package-declared tools: {skill.allowed_tools?.join(', ')||'none'}</p><p>Untrusted package requirements (not X.A.A. permissions): {declaredRequirementsSummary(skill.declared_requirements)}</p><button disabled={busy} onClick={()=>void importSkill(item.name,skill.name)}>Import as candidate</button></li>)}</ul>
        <h4>MCP servers ({item.servers.length})</h4><ul>{item.servers.map(server=><li key={server.name}>{server.name}: {server.transport==='stdio'?`stdio · ${server.command_name} · ${server.argument_count} arguments · environment names: ${server.environment_names.join(', ')||'none'}`:`${server.transport} · ${server.endpoint}`}</li>)}</ul>
      </article>)}
      {result.diagnostics.length>0&&<><h3>Package diagnostics</h3><ul>{result.diagnostics.map((diagnostic,index)=><li key={`${diagnostic.path}:${index}`}><code>{diagnostic.path}</code>: {diagnostic.message}</li>)}</ul></>}
    </>}
  </section>;
}
