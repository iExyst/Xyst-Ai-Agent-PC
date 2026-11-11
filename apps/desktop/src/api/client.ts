import type { components } from '../generated/api.js';
import { decodeRunEvents, EventStreamDisconnected } from './events.ts';

function reconnectDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal?.reason ?? new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, milliseconds);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
  });
}

export type Run = components['schemas']['RunRecord'];
export type CreateRun = components['schemas']['CreateRunRequest'];
export type Artifact = components['schemas']['ArtifactRecord'];
export type Memory = components['schemas']['MemoryRecord'];
export type Plan = components['schemas']['PlanRecord'];
export type Candidate = components['schemas']['TaskCandidate'];
export type ReviewDecision = components['schemas']['TaskReviewDecision'];
export type RunReviewDecision = components['schemas']['RunReviewDecision'];
export type ProjectValidation = components['schemas']['ProjectValidationSummary'];
export type RunHistory = components['schemas']['RunHistory'];
export type Schedule = components['schemas']['ScheduleRecord'];
export type TelegramChannelStatus = components['schemas']['TelegramCredentialStatus'];
export type DiscordChannelStatus = components['schemas']['DiscordCredentialStatus'];
export type EmailChannelStatus = components['schemas']['EmailCredentialStatus'];
export type WebhookChannelStatus = components['schemas']['WebhookChannelStatus'];
export type WebhookCredential = components['schemas']['WebhookCredentialRecord'];
export type ChannelIdentityBinding = components['schemas']['ChannelIdentityBinding'];
export type CredentialStatus = components['schemas']['LMStudioCredentialStatus'];
export type MCPTool = components['schemas']['MCPToolResponse'];
export type MCPServer = components['schemas']['MCPServerResponse'];
export type MCPConnect = components['schemas']['MCPConnectResponse'];
export type MCPResource = {uri:string;name:string;title:string|null;description:string|null;mime_type:string|null;size:number|null};
export type MCPResourceTemplate = {name:string;title:string|null;uri_template:string;variables:string[];description:string|null;mime_type:string|null};
export type MCPResourceRead = {trust:'untrusted_mcp_output';contents:Array<{uri:string;mime_type:string|null;kind:'text';text:string}|{uri:string;mime_type:string|null;kind:'blob';base64:string}>};
export type MCPResourceNotification = {kind:'resource_updated';uri:string;trust:'untrusted_mcp_output'}|{kind:'resync_required';trust:'untrusted_mcp_output'};
export type MCPPrompt = {name:string;title:string|null;description:string|null;arguments:Array<{name:string;description:string|null;required:boolean}>};
export type MCPPromptPreview = {trust:'untrusted_mcp_output';description:string|null;messages:Array<{role:'user'|'assistant';content:Record<string,unknown>}>};
export type MCPReconciliationItem = components['schemas']['MCPReconciliationItem'];
export type WorkspaceWriteReconciliationItem = components['schemas']['WorkspaceWriteReconciliationItem'];
export type PluginDiscovery = components['schemas']['PluginDiscovery'];
export type PluginInstallResult = components['schemas']['PluginInstallResult'];
export type PluginRemovalResult = components['schemas']['PluginRemovalResult'];
export type PluginDataListing = components['schemas']['PluginDataListing'];
export type PluginDataRemovalResult = components['schemas']['PluginDataRemovalResult'];
export type SpecialistProfile = components['schemas']['SpecialistProfileResponse'];
export type ChatSession = components['schemas']['CLIChatSessionRecord'];
export type ChatSessionSummary = components['schemas']['CLIChatSessionSummary'];
export type ChatSessionSettings = components['schemas']['CLIChatSessionSettings'];

export class EngineRequestError extends Error {
  readonly status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export class EngineClient {
  #token: string;
  #base: URL;
  #fetch: typeof fetch;

  constructor(baseUrl: string, token: string, fetcher: typeof fetch = fetch) {
    const base = new URL(baseUrl);
    if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) || base.username || base.password) {
      throw new Error('The engine address must be an HTTP loopback address.');
    }
    if (token.length < 32) throw new Error('Engine authentication token is missing or too short.');
    this.#base = base;
    this.#token = token;
    this.#fetch = (input, init) => fetcher(input, init);
  }

  async #request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${this.#token}`);
    if (init.body) headers.set('Content-Type', 'application/json');
    const response = await this.#fetch(new URL(path, this.#base), { ...init, headers, redirect: 'error' });
    if (!response.ok) {
      throw new EngineRequestError(response.status, `Engine request failed (${response.status}).`);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  createRun(input: CreateRun, signal?: AbortSignal): Promise<Run> {
    return this.#request('/runs', { method: 'POST', body: JSON.stringify(input), ...(signal ? { signal } : {}) });
  }

  chatSessions(): Promise<ChatSessionSummary[]> { return this.#request('/chat/sessions'); }
  chatSession(sessionId: string): Promise<ChatSession> { return this.#request(`/chat/sessions/${encodeURIComponent(sessionId)}`); }
  openChatSession(sessionId: string, settings: ChatSessionSettings): Promise<ChatSession> {
    return this.#request(`/chat/sessions/${encodeURIComponent(sessionId)}`, {method:'POST',body:JSON.stringify(settings)});
  }
  appendChatTurn(sessionId: string, input: components['schemas']['CLIChatTurnRequest']): Promise<ChatSession> {
    return this.#request(`/chat/sessions/${encodeURIComponent(sessionId)}/turn`, {method:'POST',body:JSON.stringify(input)});
  }
  deleteChatSession(sessionId: string): Promise<void> {
    return this.#request(`/chat/sessions/${encodeURIComponent(sessionId)}`, {method:'DELETE'});
  }

  usage(id: string, signal?: AbortSignal): Promise<components['schemas']['UsageReport']> {
    return this.#request(`/runs/${encodeURIComponent(id)}/usage`, {signal});
  }

  models(): Promise<components['schemas']['ModelInfo'][]> { return this.#request('/models'); }
  braveSearchStatus(): Promise<CredentialStatus> { return this.#request('/providers/brave-search/credential'); }
  setBraveSearchKey(token:string): Promise<CredentialStatus> { return this.#request('/providers/brave-search/credential',{method:'PUT',body:JSON.stringify({token})}); }
  clearBraveSearchKey(): Promise<CredentialStatus> { return this.#request('/providers/brave-search/credential',{method:'DELETE'}); }

  mcpServers(): Promise<MCPServer[]> { return this.#request('/mcp/servers'); }
  async mcpReconciliations(runId:string):Promise<MCPReconciliationItem[]>{const items=await this.#request<unknown>(`/runs/${encodeURIComponent(runId)}/mcp-reconciliations`);if(!Array.isArray(items))throw new Error('MCP reconciliation response was invalid.');return items as MCPReconciliationItem[];}
  async reconcileMcpEffect(runId:string,callId:string,resolution:'applied'|'not_applied'):Promise<MCPReconciliationItem[]>{const items=await this.#request<unknown>(`/runs/${encodeURIComponent(runId)}/tools/${encodeURIComponent(callId)}/mcp-reconcile`,{method:'POST',body:JSON.stringify({resolution,confirmed_external_effect_reviewed:true})});if(!Array.isArray(items))throw new Error('MCP reconciliation response was invalid.');return items as MCPReconciliationItem[];}
  async workspaceWriteReconciliations(runId:string):Promise<WorkspaceWriteReconciliationItem[]>{const items=await this.#request<unknown>(`/runs/${encodeURIComponent(runId)}/workspace-write-reconciliations`);if(!Array.isArray(items))throw new Error('Workspace write reconciliation response was invalid.');return items as WorkspaceWriteReconciliationItem[];}
  async reconcileWorkspaceWriteNotApplied(runId:string,callId:string):Promise<WorkspaceWriteReconciliationItem[]>{const items=await this.#request<unknown>(`/runs/${encodeURIComponent(runId)}/tools/${encodeURIComponent(callId)}/workspace-write-reconcile`,{method:'POST',body:JSON.stringify({confirmed_workspace_inspected:true})});if(!Array.isArray(items))throw new Error('Workspace write reconciliation response was invalid.');return items as WorkspaceWriteReconciliationItem[];}
  createMCPServer(input:{name:string;transport:'stdio'|'streamable_http';endpoint:string|null;command:string;args:string[];cwd:string|null;secret_env:string[]}):Promise<MCPServer>{return this.#request('/mcp/servers',{method:'POST',body:JSON.stringify(input)});}
  deleteMCPServer(id:string):Promise<void>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}`,{method:'DELETE'});}
  connectMCPServer(id:string):Promise<MCPConnect>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}/connect`,{method:'POST'});}
  disconnectMCPServer(id:string):Promise<{state:string}>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}/disconnect`,{method:'POST'});}
  async listMCPResources(id:string):Promise<MCPResource[]>{const result=await this.#request<{resources:unknown}>(`/mcp/servers/${encodeURIComponent(id)}/resources`,{method:'POST'});if(!Array.isArray(result.resources))throw new Error('MCP resource listing response was invalid.');return result.resources as MCPResource[];}
  async listMCPResourceTemplates(id:string):Promise<MCPResourceTemplate[]>{const result=await this.#request<{resource_templates:unknown}>(`/mcp/servers/${encodeURIComponent(id)}/resource-templates`,{method:'POST'});if(!Array.isArray(result.resource_templates))throw new Error('MCP resource template listing response was invalid.');return result.resource_templates as MCPResourceTemplate[];}
  readMCPResourceTemplate(id:string,name:string,arguments_:Record<string,string>):Promise<MCPResourceRead>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}/resource-templates/read`,{method:'POST',body:JSON.stringify({name,arguments:arguments_})});}
  readMCPResource(id:string,uri:string):Promise<MCPResourceRead>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}/resources/read`,{method:'POST',body:JSON.stringify({uri})});}
  setMCPResourceSubscription(id:string,uri:string,subscribe:boolean):Promise<{uri:string;subscribed:boolean;trust:'untrusted_mcp_output'}>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}/resources/subscription`,{method:'POST',body:JSON.stringify({uri,subscribe})});}
  async mcpResourceNotifications(id:string):Promise<MCPResourceNotification[]>{const result=await this.#request<{notifications:unknown}>(`/mcp/servers/${encodeURIComponent(id)}/resources/notifications`,{method:'POST'});if(!Array.isArray(result.notifications))throw new Error('MCP resource notification response was invalid.');return result.notifications as MCPResourceNotification[];}
  async listMCPPrompts(id:string):Promise<MCPPrompt[]>{const result=await this.#request<{prompts:unknown}>(`/mcp/servers/${encodeURIComponent(id)}/prompts`,{method:'POST'});if(!Array.isArray(result.prompts))throw new Error('MCP prompt listing response was invalid.');return result.prompts as MCPPrompt[];}
  previewMCPPrompt(id:string,name:string,arguments_:Record<string,string>):Promise<MCPPromptPreview>{return this.#request(`/mcp/servers/${encodeURIComponent(id)}/prompts/preview`,{method:'POST',body:JSON.stringify({name,arguments:arguments_})});}
  setMCPToolPermission(serverId:string,tool:MCPTool,permission:MCPTool['permission']):Promise<MCPTool>{return this.#request(`/mcp/servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(tool.name)}/permission`,{method:'PUT',body:JSON.stringify({permission,schema_sha256:tool.schema_sha256})});}
  setMCPSecret(serverId:string,name:string,value:string):Promise<{configured:boolean}>{return this.#request(`/mcp/servers/${encodeURIComponent(serverId)}/secrets/${encodeURIComponent(name)}`,{method:'PUT',body:JSON.stringify({value})});}
  clearMCPSecret(serverId:string,name:string):Promise<void>{return this.#request(`/mcp/servers/${encodeURIComponent(serverId)}/secrets/${encodeURIComponent(name)}`,{method:'DELETE'});}
  clearMCPOAuth(serverId:string):Promise<void>{return this.#request(`/mcp/servers/${encodeURIComponent(serverId)}/oauth`,{method:'DELETE'});}
  discoverAgentPlugins(root:string):Promise<PluginDiscovery>{return this.#request('/extensions/packages/discover',{method:'POST',body:JSON.stringify({root})});}
  listInstalledAgentPlugins():Promise<PluginDiscovery>{return this.#request('/extensions/packages/installed',{method:'GET'});}
  installAgentPluginArchive(archive_path:string):Promise<PluginInstallResult>{return this.#request('/extensions/packages/install',{method:'POST',body:JSON.stringify({archive_path})});}
  removeInstalledAgentPlugin(package_name:string):Promise<PluginRemovalResult>{return this.#request('/extensions/packages/remove',{method:'POST',body:JSON.stringify({package_name})});}
  listAgentPluginData():Promise<PluginDataListing>{return this.#request('/extensions/packages/data',{method:'GET'});}
  removeAgentPluginData(package_name:string):Promise<PluginDataRemovalResult>{return this.#request('/extensions/packages/data/remove',{method:'POST',body:JSON.stringify({package_name})});}
  importAgentPluginSkill(input:{root:string;package_name:string;skill_name:string}):Promise<components['schemas']['SkillSummary']>{return this.#request('/extensions/packages/import-skill',{method:'POST',body:JSON.stringify(input)});}
  importAgentPluginMCPServer(input:{root:string;package_name:string;server_name:string}):Promise<{id:string;name:string;transport:'stdio';state:'disconnected';secret_env:string[]}>{return this.#request('/extensions/packages/import-mcp-server',{method:'POST',body:JSON.stringify(input)});}
  async exportSkillPackage(id:string,version:number):Promise<Blob>{
    const path=`/skills/${encodeURIComponent(id)}/versions/${version}/export`;
    const response=await this.#fetch(new URL(path,this.#base),{headers:{Authorization:`Bearer ${this.#token}`},redirect:'error'});
    if(!response.ok)throw new EngineRequestError(response.status,`Engine request failed (${response.status}).`);
    if(!response.headers.get('content-type')?.startsWith('application/zip'))throw new Error('Engine returned an invalid procedure package.');
    return response.blob();
  }

  lmStudioConfig(): Promise<components['schemas']['LMStudioConfigRecord']> {
    return this.#request('/providers/lm-studio/config');
  }

  setLMStudioEndpoint(endpoint: string): Promise<components['schemas']['LMStudioConfigRecord']> {
    return this.#request('/providers/lm-studio/config', {method:'PUT',body:JSON.stringify({endpoint})});
  }

  lmStudioCredentialStatus(): Promise<components['schemas']['LMStudioCredentialStatus']> {
    return this.#request('/providers/lm-studio/credential');
  }

  setLMStudioCredential(token: string): Promise<components['schemas']['LMStudioCredentialStatus']> {
    return this.#request('/providers/lm-studio/credential', {method:'PUT',body:JSON.stringify({token})});
  }

  clearLMStudioCredential(): Promise<components['schemas']['LMStudioCredentialStatus']> {
    return this.#request('/providers/lm-studio/credential', {method:'DELETE'});
  }

  selectLMStudioModel(model: string | null): Promise<{model:string|null}> {
    return this.#request('/providers/lm-studio/selection', {method:'PUT',body:JSON.stringify({model})});
  }

  compatibleProviderConfig(): Promise<components['schemas']['CompatibleProviderConfigRecord']> {
    return this.#request('/providers/openai-compatible/config');
  }

  setCompatibleProviderEndpoint(endpoint: string): Promise<components['schemas']['CompatibleProviderConfigRecord']> {
    return this.#request('/providers/openai-compatible/config', {method:'PUT',body:JSON.stringify({endpoint})});
  }

  compatibleProviderCredentialStatus(): Promise<components['schemas']['LMStudioCredentialStatus']> {
    return this.#request('/providers/openai-compatible/credential');
  }

  setCompatibleProviderCredential(token: string): Promise<components['schemas']['LMStudioCredentialStatus']> {
    return this.#request('/providers/openai-compatible/credential', {method:'PUT',body:JSON.stringify({token})});
  }

  clearCompatibleProviderCredential(): Promise<components['schemas']['LMStudioCredentialStatus']> {
    return this.#request('/providers/openai-compatible/credential', {method:'DELETE'});
  }

  selectCompatibleProviderModel(model: string | null): Promise<{model:string|null}> {
    return this.#request('/providers/openai-compatible/selection', {method:'PUT',body:JSON.stringify({model})});
  }

  diagnoseProviderModel(model: string): Promise<{model:string;tool_round_trip_verified:boolean}> {
    return this.#request('/providers/diagnostics', {method:'POST',body:JSON.stringify({model})});
  }

  providers(): Promise<{id:string;status:string;error?:string}[]> { return this.#request('/providers'); }

  specialistProfiles(): Promise<SpecialistProfile[]> { return this.#request('/specialists/profiles'); }

  setSpecialistRoute(role: string, model_route: string | null): Promise<SpecialistProfile> {
    return this.#request(`/specialists/profiles/${encodeURIComponent(role)}/route`, {
      method: 'PUT', body: JSON.stringify({model_route}),
    });
  }

  setSpecialistInstructions(role: string, system_instructions: string): Promise<SpecialistProfile> {
    return this.#request(`/specialists/profiles/${encodeURIComponent(role)}/instructions`, {
      method: 'PUT', body: JSON.stringify({system_instructions}),
    });
  }

  setSpecialistIdentity(role: string, display_name: string): Promise<SpecialistProfile> {
    return this.#request(`/specialists/profiles/${encodeURIComponent(role)}/identity`, {
      method: 'PUT', body: JSON.stringify({display_name}),
    });
  }

  specialistMemory(role: string): Promise<Memory[]> {
    return this.#request(`/specialists/profiles/${encodeURIComponent(role)}/memory`);
  }

  saveSpecialistMemory(role: string, content: string): Promise<Memory> {
    return this.#request(`/specialists/profiles/${encodeURIComponent(role)}/memory`, {
      method: 'POST', body: JSON.stringify({content}),
    });
  }

  deleteSpecialistMemory(role: string, id: string): Promise<void> {
    return this.#request(`/specialists/profiles/${encodeURIComponent(role)}/memory/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  }

  executionDistributions(signal?: AbortSignal): Promise<components['schemas']['DistributionInventory']> {
    return this.#request('/execution/distributions', {signal});
  }

  executionReadiness(distribution: string, signal?: AbortSignal): Promise<components['schemas']['ReadinessResult']> {
    return this.#request('/execution/readiness', {method:'POST',body:JSON.stringify({distribution}),signal});
  }

  schedules(): Promise<Schedule[]> { return this.#request('/schedules'); }

  createSchedule(input: components['schemas']['CreateScheduleRequest']): Promise<Schedule> {
    return this.#request('/schedules', {method:'POST',body:JSON.stringify(input)});
  }

  setScheduleEnabled(id: string, enabled: boolean): Promise<Schedule> {
    return this.#request(`/schedules/${encodeURIComponent(id)}/enabled`, {method:'PUT',body:JSON.stringify({enabled})});
  }

  setScheduleDeliveryDestinations(id: string, input: components['schemas']['ScheduleDeliveryDestinationsRequest']): Promise<Schedule> {
    return this.#request(`/schedules/${encodeURIComponent(id)}/delivery`, {method:'PUT',body:JSON.stringify(input)});
  }

  reconcileScheduleDelivery(scheduleId:string,executionId:string,resolution:'delivered'|'retry'): Promise<Schedule> {
    return this.#request(`/schedules/${encodeURIComponent(scheduleId)}/executions/${encodeURIComponent(executionId)}/delivery/reconcile`,{method:'POST',body:JSON.stringify({resolution,confirmed_destination_checked:true})});
  }

  telegramStatus(): Promise<TelegramChannelStatus> { return this.#request('/channels/telegram'); }

  reconcileTelegramDelivery(updateId:number,resolution:'delivered'|'retry'): Promise<TelegramChannelStatus> {
    return this.#request(`/channels/telegram/deliveries/${updateId}/reconcile`,{method:'POST',body:JSON.stringify({resolution,confirmed_destination_checked:true})});
  }

  setTelegramToken(token: string): Promise<TelegramChannelStatus> {
    return this.#request('/channels/telegram/credential',{method:'PUT',body:JSON.stringify({token})});
  }

  clearTelegramToken(): Promise<TelegramChannelStatus> {
    return this.#request('/channels/telegram/credential',{method:'DELETE'});
  }

  telegramPairing(): Promise<components['schemas']['TelegramPairingCode']> {
    return this.#request('/channels/telegram/pairing',{method:'POST',body:JSON.stringify({})});
  }

  discordStatus(): Promise<DiscordChannelStatus> { return this.#request('/channels/discord'); }

  setDiscordToken(token:string): Promise<DiscordChannelStatus> {
    return this.#request('/channels/discord/credential',{method:'PUT',body:JSON.stringify({token})});
  }

  clearDiscordToken(): Promise<DiscordChannelStatus> {
    return this.#request('/channels/discord/credential',{method:'DELETE'});
  }

  discordPairing(): Promise<components['schemas']['DiscordPairingCode']> {
    return this.#request('/channels/discord/pairing',{method:'POST',body:JSON.stringify({})});
  }

  revokeDiscordLinks(): Promise<components['schemas']['DiscordLinkRevocation']> {
    return this.#request('/channels/discord/links',{method:'DELETE'});
  }

  reconcileDiscordDelivery(messageId:string,resolution:'delivered'|'retry'):Promise<DiscordChannelStatus> {
    return this.#request(`/channels/discord/deliveries/${encodeURIComponent(messageId)}/reconcile`,{method:'POST',body:JSON.stringify({resolution,confirmed_destination_checked:true})});
  }

  telegramLinkedUsers(): Promise<components['schemas']['TelegramLinkedUser'][]> {
    return this.#request('/channels/telegram/linked-users');
  }

  async channelIdentityBindings(): Promise<ChannelIdentityBinding[]> {
    const value=await this.#request<unknown>('/channels/identity-bindings');
    if(!Array.isArray(value)||value.some(item=>{
      if(typeof item!=='object'||item===null)return true;
      const binding=item as Record<string,unknown>;
      return typeof binding.id!=='string'||typeof binding.channel!=='string'||typeof binding.account_id!=='string'||typeof binding.external_identity!=='string'||typeof binding.session_id!=='string'||typeof binding.session_channel!=='string'||typeof binding.authorized!=='boolean'||!(binding.identity_link_id===null||typeof binding.identity_link_id==='string')||!(binding.title===null||typeof binding.title==='string')||!(binding.link_created_at===null||typeof binding.link_created_at==='string');
    }))throw new Error('The engine returned an invalid channel identity list.');
    return value as ChannelIdentityBinding[];
  }

  createChannelIdentityLink(bindingIds: [string,string], sharedHistoryBindingId: string): Promise<components['schemas']['ChannelIdentityLinkRecord']> {
    return this.#request('/channels/identity-links',{method:'POST',body:JSON.stringify({binding_ids:bindingIds,shared_history_binding_id:sharedHistoryBindingId})});
  }

  addChannelIdentityLinkMember(linkId: string, bindingId: string): Promise<components['schemas']['ChannelIdentityLinkMemberResponse']> {
    return this.#request(`/channels/identity-links/${encodeURIComponent(linkId)}/members`,{method:'POST',body:JSON.stringify({binding_id:bindingId,confirmed:true})});
  }

  mergeChannelIdentityLinks(targetLinkId: string, sourceLinkId: string): Promise<components['schemas']['MergeChannelIdentityLinksResponse']> {
    return this.#request(`/channels/identity-links/${encodeURIComponent(targetLinkId)}/merge`,{method:'POST',body:JSON.stringify({source_link_id:sourceLinkId,confirmed:true})});
  }

  revokeChannelIdentityLink(linkId: string): Promise<components['schemas']['RevokeChannelIdentityLinkResponse']> {
    return this.#request(`/channels/identity-links/${encodeURIComponent(linkId)}`,{method:'DELETE',body:JSON.stringify({confirmed:true})});
  }

  revokeTelegramLinks(): Promise<components['schemas']['TelegramLinkRevocation']> {
    return this.#request('/channels/telegram/links',{method:'DELETE'});
  }

  emailStatus(): Promise<EmailChannelStatus> { return this.#request('/channels/email'); }

  webhookStatus(): Promise<WebhookChannelStatus> { return this.#request('/channels/webhook'); }

  createWebhookCredential(): Promise<WebhookCredential> {
    return this.#request('/channels/webhook/credential',{method:'POST',body:JSON.stringify({})});
  }

  clearWebhookCredential(): Promise<WebhookChannelStatus> {
    return this.#request('/channels/webhook/credential',{method:'DELETE'});
  }

  reconcileEmailDelivery(uidValidity:string,uid:string,resolution:'delivered'|'retry'): Promise<EmailChannelStatus> {
    return this.#request(`/channels/email/deliveries/${encodeURIComponent(uidValidity)}/${encodeURIComponent(uid)}/reconcile`,{method:'POST',body:JSON.stringify({resolution,confirmed_destination_checked:true})});
  }

  setEmailAccount(input: components['schemas']['EmailCredentialRequest']): Promise<EmailChannelStatus> {
    return this.#request('/channels/email/credential',{method:'PUT',body:JSON.stringify(input)});
  }

  clearEmailAccount(): Promise<EmailChannelStatus> {
    return this.#request('/channels/email/credential',{method:'DELETE'});
  }

  history(query='',before?:string,signal?:AbortSignal,scope:'objective'|'conversation'='objective'): Promise<RunHistory> {
    const params=new URLSearchParams({q:query,limit:'20',scope});
    if(before)params.set('before',before);
    return this.#request(`/runs?${params}`,signal?{signal}:{});
  }

  plan(runId: string, signal?: AbortSignal): Promise<Plan> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/plan`, signal ? { signal } : {});
  }

  taskRecovery(runId:string,taskId:string):Promise<components['schemas']['TaskRecoverySummary']> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/recovery`);
  }

  decideTaskRecovery(runId:string,taskId:string,decision:'resume'|'review_candidate'|'fail'):Promise<Plan> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/recovery`,{method:'POST',body:JSON.stringify({decision})});
  }

  reconcileTaskCommand(runId:string,taskId:string,modelCallId:string):Promise<components['schemas']['TaskRecoverySummary']> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/recovery/commands/${encodeURIComponent(modelCallId)}`,{method:'POST'});
  }

  attachSavedTaskAction(runId:string,taskId:string,modelCallId:string):Promise<components['schemas']['TaskRecoverySummary']> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/recovery/actions/${encodeURIComponent(modelCallId)}`,{method:'POST'});
  }

  integrationPreview(runId:string,taskId:string):Promise<components['schemas']['IntegrationPreview']> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/integration-preview`,{method:'POST'});
  }

  candidate(runId: string, taskId: string): Promise<Candidate> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/candidate`);
  }

  dispatchPlan(runId: string): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/plan/dispatch`, {method:'POST'});
  }

  reviewRun(runId: string, decision: RunReviewDecision): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/review`, {method:'POST',body:JSON.stringify(decision)});
  }

  projectValidation(runId:string):Promise<ProjectValidation>{return this.#request(`/runs/${encodeURIComponent(runId)}/validation`);}
  runProjectValidation(runId:string,input:{request_id:string;expected_revision:number;argv:string[];max_seconds:number}):Promise<ProjectValidation>{return this.#request(`/runs/${encodeURIComponent(runId)}/validation`,{method:'POST',body:JSON.stringify(input)});}
  reconcileProjectValidation(runId:string,callId:string):Promise<ProjectValidation>{return this.#request(`/runs/${encodeURIComponent(runId)}/validation/${encodeURIComponent(callId)}/reconcile`,{method:'POST'});}

  reviewCandidate(runId: string, taskId: string, decision: ReviewDecision): Promise<Plan> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/review`, {method:'POST',body:JSON.stringify(decision)});
  }

  skills(): Promise<components['schemas']['SkillSummary'][]> { return this.#request('/skills'); }
  skillVersion(id:string,version:number): Promise<components['schemas']['SkillVersion']> { return this.#request(`/skills/${encodeURIComponent(id)}/versions/${version}`); }
  saveSkill(draft:components['schemas']['SkillDraft'],id?:string,expectedVersion?:number): Promise<components['schemas']['SkillSummary']> {
    return this.#request(id?`/skills/${encodeURIComponent(id)}/versions`:'/skills',{method:'POST',body:JSON.stringify(id?{...draft,expected_version:expectedVersion}:draft)});
  }
  skillTrials(id:string,version:number): Promise<components['schemas']['SkillTrialRun'][]> { return this.#request(`/skills/${encodeURIComponent(id)}/versions/${version}/trials`); }
  evaluateSkill(id:string,version:number,review:components['schemas']['SkillEvaluation']): Promise<components['schemas']['SkillVersion']> { return this.#request(`/skills/${encodeURIComponent(id)}/versions/${version}/evaluations`,{method:'POST',body:JSON.stringify(review)}); }
  skillLifecycle(id:string): Promise<components['schemas']['SkillLifecycleEvent'][]> { return this.#request(`/skills/${encodeURIComponent(id)}/lifecycle`); }
  decideSkillLifecycle(id:string,decision:components['schemas']['SkillLifecycleDecision']): Promise<components['schemas']['SkillSummary']> { return this.#request(`/skills/${encodeURIComponent(id)}/lifecycle`,{method:'POST',body:JSON.stringify(decision)}); }
  setSkillPinned(id:string,pinned:boolean):Promise<components['schemas']['SkillSummary']>{return this.#request(`/skills/${encodeURIComponent(id)}/pin`,{method:'PUT',body:JSON.stringify({pinned})});}
  deleteSkill(id:string): Promise<void> { return this.#request(`/skills/${encodeURIComponent(id)}`,{method:'DELETE'}); }

  memories(query = ''): Promise<Memory[]> { return this.#request(`/memory?q=${encodeURIComponent(query)}`); }
  savePreference(content: string, id?: string): Promise<Memory> { return this.#request(id ? `/memory/${encodeURIComponent(id)}` : '/memory', { method: 'POST', body: JSON.stringify({content}) }); }
  saveSourcedKnowledge(content: string, source_uri: string, id?: string): Promise<Memory> {
    return this.#request(id ? `/memory/knowledge/${encodeURIComponent(id)}` : '/memory/knowledge', {
      method: id ? 'PUT' : 'POST', body: JSON.stringify({content, source_uri}),
    });
  }
  deleteMemory(id: string): Promise<void> { return this.#request(`/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
  exportMemory(): Promise<Memory[]> { return this.#request('/memory/export'); }

  artifacts(runId: string, signal?: AbortSignal): Promise<Artifact[]> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/artifacts`, signal ? { signal } : {});
  }

  async artifactPreview(id: string, signal?: AbortSignal): Promise<string> {
    const response = await this.#fetch(new URL(`/artifacts/${encodeURIComponent(id)}`, this.#base), { headers: { Authorization: `Bearer ${this.#token}` }, redirect: 'error', ...(signal ? { signal } : {}) });
    if (!response.ok || !response.body) throw new EngineRequestError(response.status, 'Cannot read artifact.');
    const reader = response.body.getReader();
    const mediaType = response.headers.get('content-type')?.split(';', 1)[0];
    if (mediaType === 'application/pdf') {
      await reader.cancel();
      reader.releaseLock();
      return 'Original PDF is retained with this run. View the adjacent extracted document text artifact for its model input.';
    }
    if (mediaType === 'image/png' || mediaType === 'image/jpeg') {
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > 3 * 1024 * 1024) throw new Error('Image exceeds the preview limit.');
          chunks.push(chunk.value);
        }
      } finally { await reader.cancel(); reader.releaseLock(); }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      let binary = '';
      for (let index = 0; index < bytes.length; index += 32768) {
        binary += String.fromCharCode(...bytes.subarray(index, Math.min(index + 32768, bytes.length)));
      }
      return `data:${mediaType};base64,${btoa(binary)}`;
    }
    const decoder = new TextDecoder();
    let text = ''; let bytes = 0;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) return text + decoder.decode();
        const available = 65536 - bytes;
        const slice = chunk.value.subarray(0, available);
        bytes += slice.byteLength; text += decoder.decode(slice, { stream: true });
        if (bytes >= 65536) return text + decoder.decode() + '\n[Preview limited to 64 KiB]';
      }
    } finally { await reader.cancel(); reader.releaseLock(); }
  }

  async artifactDownload(id: string, signal?: AbortSignal): Promise<Blob> {
    const response = await this.#fetch(new URL(`/artifacts/${encodeURIComponent(id)}`, this.#base), { headers: { Authorization: `Bearer ${this.#token}` }, redirect: 'error', ...(signal ? { signal } : {}) });
    if (!response.ok || !response.body) throw new EngineRequestError(response.status, 'Cannot download artifact.');
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 8 * 1024 * 1024) throw new Error('Artifact exceeds the download limit.');
        chunks.push(chunk.value);
      }
    } finally { await reader.cancel(); reader.releaseLock(); }
    const content = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.byteLength; }
    return new Blob([content], {type: response.headers.get('content-type')?.split(';', 1)[0] ?? 'application/octet-stream'});
  }

  getRun(id: string, signal?: AbortSignal): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(id)}`, signal ? { signal } : {});
  }

  latestUnfinishedRun(signal?:AbortSignal):Promise<RunHistory['items'][number]|null>{
    return this.#request('/runs/latest-unfinished',signal?{signal}:{});
  }

  control(id: string, action: 'pause' | 'resume' | 'cancel'): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
  }

  async *events(id: string, after = 0, signal?: AbortSignal) {
    if (!Number.isSafeInteger(after) || after < 0) throw new Error('Event cursor must be a nonnegative integer.');
    const url = new URL(`/runs/${encodeURIComponent(id)}/events/stream`, this.#base);
    let cursor = after;
    for (let attempt = 0; ; attempt++) {
      try {
        signal?.throwIfAborted();
        const headers = new Headers({ Authorization: `Bearer ${this.#token}`, 'Last-Event-ID': String(cursor) });
        const response = await this.#fetch(url, { headers, redirect: 'error', ...(signal ? { signal } : {}) });
        if (!response.ok || !response.body) {
          await response.body?.cancel();
          throw new EngineRequestError(response.status, 'Cannot open engine event stream.');
        }
        for await (const event of decodeRunEvents(response.body, cursor)) {
          cursor = event.sequence;
          yield event;
          // Bound consecutive failures, not the lifetime number of recoveries.
          // A valid persisted event proves the connection made forward progress.
          attempt = 0;
        }
        return;
      } catch (failure) {
        if (signal?.aborted) throw failure;
        const transient = failure instanceof EventStreamDisconnected || failure instanceof TypeError ||
          (failure instanceof EngineRequestError && [408, 429, 500, 502, 503, 504].includes(failure.status));
        if (!transient || attempt >= 5) throw failure;
        await reconnectDelay(Math.min(250 * 2 ** attempt, 4000), signal);
      }
    }
  }
}
