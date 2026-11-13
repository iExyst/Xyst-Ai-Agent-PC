import { useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { EngineRequestError } from './api/client.ts';
import type { EngineClient, PluginDiscovery } from './api/client.ts';

function declaredRequirementsSummary(value: Record<string, unknown> | undefined): string {
  if (!value) return 'No supported dependency metadata found.';
  const names = (key: string) => {
    const items = value[key];
    return Array.isArray(items) ? items.filter((item): item is string => typeof item === 'string').join(', ') : '';
  };
  const packageNames = (key: string) => {
    const items = value[key];
    return Array.isArray(items) ? items.flatMap(item => {
      if (typeof item !== 'object' || item === null) return [];
      const entry = item as Record<string, unknown>;
      return typeof entry.manager === 'string' && typeof entry.name === 'string'
        ? [`${entry.manager}:${entry.name}`]
        : [];
    }).join(', ') : '';
  };
  const dependencies = value.dependencies_declared === true
    ? `declared (${typeof value.dependency_count === 'number' ? value.dependency_count : 'count unknown'})`
    : 'not declared';
  return [
    `Platforms: ${names('platforms') || 'not declared'}`,
    `Hermes toolsets: ${names('hermes_toolsets') || 'not declared'}`,
    `Hermes tools: ${names('hermes_required_tools') || 'not declared'}`,
    `Fallback toolsets: ${names('hermes_fallback_toolsets') || 'not declared'}`,
    `Fallback tools: ${names('hermes_fallback_tools') || 'not declared'}`,
    `Prerequisite fields: ${names('prerequisite_fields') || 'none'}`,
    `Command names: ${names('required_command_names') || 'none'}`,
    `Environment variable names: ${names('required_environment_names') || 'none'}`,
    `Dependencies: ${dependencies}`,
    `Package names: ${names('dependency_names') || 'none parsed'} · manager keys: ${names('dependency_managers') || 'not declared'} · unparsed entries: ${typeof value.unparsed_dependency_count === 'number' ? value.unparsed_dependency_count : 'unknown'}`,
    `Packages outside the checked Python managers: ${packageNames('unverified_dependency_packages') || 'none identified'}`,
    `Python version constraints: ${names('python_dependency_specs') || 'none parsed'}`,
  ].join(' · ');
}

function requirementReadinessSummary(value: Record<string, unknown> | undefined): string {
  if (!value) return 'No local declaration assessment is available.';
  const names = (key: string) => {
    const items = value[key];
    return Array.isArray(items) ? items.filter((item): item is string => typeof item === 'string').join(', ') : '';
  };
  const packageNames = (key: string) => {
    const items = value[key];
    return Array.isArray(items) ? items.flatMap(item => {
      if (typeof item !== 'object' || item === null) return [];
      const entry = item as Record<string, unknown>;
      return typeof entry.manager === 'string' && typeof entry.name === 'string'
        ? [`${entry.manager}:${entry.name}`]
        : [];
    }).join(', ') : '';
  };
  const platform = typeof value.platform_status === 'string' ? value.platform_status : 'unknown';
  const current = typeof value.current_platform === 'string' ? value.current_platform : 'unknown';
  const dependencies = value.dependencies_declared !== true
    ? 'none declared'
    : value.dependencies_unresolved === true
      ? `${typeof value.dependency_count_unresolved === 'number' ? value.dependency_count_unresolved : 'some'} unresolved`
      : 'declared Python checks passed';
  const fallbackConditions = [names('hermes_fallback_toolsets_unresolved'), names('hermes_fallback_tools_unresolved')]
    .filter(Boolean).join(', ') || 'none';
  return [
    `Current machine (${current}): platform ${platform}`,
    `Commands available: ${names('available_command_names') || 'none'}; missing: ${names('missing_command_names') || 'none'}; not checked: ${names('unchecked_command_names') || 'none'}`,
    `Required environment names present: ${names('present_environment_names') || 'none'}; missing: ${names('missing_environment_names') || 'none'}`,
    `Python package constraints: ${dependencies}; satisfied: ${names('available_python_dependency_specs') || 'none'}; missing: ${names('missing_python_dependency_specs') || 'none'}; version mismatch: ${names('incompatible_python_dependency_specs') || 'none'}; unchecked: ${names('unchecked_python_dependency_specs') || 'none'}`,
    `Other or unverified dependencies: ${typeof value.unverified_dependency_count === 'number' ? value.unverified_dependency_count : 'unknown'}`,
    `Packages not checked by the Python version audit: ${packageNames('unverified_dependency_packages') || 'none identified'}`,
    `Unresolved Hermes toolsets: ${names('hermes_toolsets_unresolved') || 'none'}`,
    `Unresolved Hermes tools: ${names('hermes_tools_unresolved') || 'none'}`,
    `Unresolved fallback conditions: ${fallbackConditions}`,
    `Not assessed: ${names('not_assessed') || 'none'}`,
  ].join(' · ');
}

export function AgentPluginsPanel({client,onImported}:{client:EngineClient;onImported:()=>void}) {
  const [root,setRoot]=useState('');
  const [managedRoot,setManagedRoot]=useState('');
  const [dataRoot,setDataRoot]=useState('');
  const [dataPackages,setDataPackages]=useState<string[]>([]);
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
  async function discoverInstalled() {
    setBusy(true);setError('');setNotice('');
    try {
      const installed=await client.listInstalledAgentPlugins();
      setRoot(installed.root);setManagedRoot(installed.root);setResult(installed);
    } catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  async function installArchive() {
    setError('');setNotice('');
    try {
      const selected=await open({directory:false,multiple:false,title:'Install Agent Plugin package',filters:[{name:'Agent Plugin ZIP',extensions:['zip']}]});
      if(typeof selected!=='string')return;
      setBusy(true);
      const installed=await client.installAgentPluginArchive(selected);
      setRoot(installed.root);setManagedRoot(installed.root);
      setResult(await client.discoverAgentPlugins(installed.root));
      setNotice(`${installed.package.name} was installed for discovery. Its commands were not run; add skills as inactive candidates or MCP servers as disconnected integrations.`);
    } catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  async function removePackage(packageName:string) {
    if(!window.confirm(`Remove installed package “${packageName}”? Its imported skill versions and package data will be kept.`))return;
    setBusy(true);setError('');setNotice('');
    try {
      await client.removeInstalledAgentPlugin(packageName);
      const installed=await client.listInstalledAgentPlugins();
      setRoot(installed.root);setManagedRoot(installed.root);setResult(installed);
      setNotice(`${packageName} was removed. Imported skill versions and package data were kept.`);
    } catch (failure) {setError(failure instanceof EngineRequestError&&failure.status===409?"Remove this package's MCP integrations before uninstalling it.":failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  async function discoverPackageData() {
    setBusy(true);setError('');setNotice('');
    try {
      const listing=await client.listAgentPluginData();
      setDataRoot(listing.root);setDataPackages(listing.package_names);
    } catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  async function removePackageData(packageName:string) {
    if(!window.confirm(`Permanently delete retained data for “${packageName}”? This cannot be undone.`))return;
    setBusy(true);setError('');setNotice('');
    try {
      await client.removeAgentPluginData(packageName);
      const listing=await client.listAgentPluginData();
      setDataRoot(listing.root);setDataPackages(listing.package_names);
      setNotice(`Retained data for ${packageName} was deleted.`);
    } catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
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
  async function importMCPServer(packageName:string,serverName:string) {
    setBusy(true);setError('');setNotice('');
    try {
      const created=await client.importAgentPluginMCPServer({root:result?.root??root.trim(),package_name:packageName,server_name:serverName});
      setNotice(`${created.name} was added as a disconnected MCP server. Review it in MCP servers and connect it when ready.`);
      onImported();
    } catch (failure) {setError(failure instanceof Error?failure.message:String(failure));}
    finally {setBusy(false);}
  }
  return <section aria-label="Agent plugin package discovery">
    <h2>Agent plugin packages</h2>
    <p>Discovery does not run commands, connect servers, or register tools. Importing a skill reads its bounded text into an inactive candidate for review.</p>
    <button disabled={busy} onClick={()=>void installArchive()}>{busy?'Installing…':'Install plugin ZIP'}</button>
    <button disabled={busy} onClick={()=>void discoverInstalled()}>Show installed packages</button>
    <button disabled={busy} onClick={()=>void discoverPackageData()}>Show retained package data</button>
    <label htmlFor="plugin-root">Package folder</label>
    <div><input id="plugin-root" value={root} onChange={event=>{const value=event.target.value;setRoot(value);if(value.trim()!==managedRoot)setManagedRoot('');}} maxLength={2048} placeholder="C:\\path\\to\\plugins"/>
    <button disabled={busy||!root.trim()} onClick={()=>void discover()}>{busy?'Discovering…':'Discover packages'}</button></div>
    {error&&<p role="alert">{error}</p>}
    {notice&&<p role="status">{notice}</p>}
    {dataRoot&&<section aria-label="Retained plugin data"><h3>Retained package data</h3><p>Data root: <code>{dataRoot}</code></p>{dataPackages.length===0?<p>No retained package data found.</p>:<ul>{dataPackages.map(name=><li key={name}>{name} <button disabled={busy} onClick={()=>void removePackageData(name)}>Delete retained data for {name}</button></li>)}</ul>}</section>}
    {result&&<>
      <p>Scanned {result.root}. {result.packages.length} package(s) found.</p>
      {result.packages.map(item=><article key={item.path}>
        <h3>{item.name}{item.version?` · ${item.version}`:''}</h3>{managedRoot&&result.root===managedRoot&&<button disabled={busy} onClick={()=>void removePackage(item.name)}>Remove {item.name}</button>}{item.description&&<p>{item.description}</p>}
        <p>Package: <code>{item.path}</code></p>
        <h4>Skills ({item.skills.length})</h4><ul>{item.skills.map(skill=><li key={skill.path}><strong>{skill.name}</strong>: {skill.description} <code>{skill.path}</code><p>Package-declared tools: {skill.allowed_tools?.join(', ')||'none'}</p><p>Untrusted package requirements (not X.A.A. permissions): {declaredRequirementsSummary(skill.declared_requirements)}</p><p>Current-machine declaration check only (does not establish execution readiness or grant permission): {requirementReadinessSummary(skill.requirement_readiness)}</p><button disabled={busy} onClick={()=>void importSkill(item.name,skill.name)}>Import as candidate</button></li>)}</ul>
        <h4>MCP servers ({item.servers.length})</h4><ul>{item.servers.map(server=><li key={server.name}>{server.name}: {server.transport==='stdio'?`stdio · ${server.command_name} · ${server.argument_count} arguments · environment names: ${server.environment_names.join(', ')||'none'}`:`${server.transport} · ${server.endpoint} · environment names: ${server.environment_names.join(', ')||'none'}`} <button disabled={busy} onClick={()=>void importMCPServer(item.name,server.name)}>Add disconnected server</button></li>)}</ul>
      </article>)}
      {result.diagnostics.length>0&&<><h3>Package diagnostics</h3><ul>{result.diagnostics.map((diagnostic,index)=><li key={`${diagnostic.path}:${index}`}><code>{diagnostic.path}</code>: {diagnostic.message}</li>)}</ul></>}
    </>}
  </section>;
}
