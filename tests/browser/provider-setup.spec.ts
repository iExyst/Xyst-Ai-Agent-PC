import {test,expect} from '@playwright/test';
import {navigateMainSection} from './navigation';

test('LM Studio address, discovered model and tool diagnostic are configured before use',async({page})=>{
  let endpoint='http://127.0.0.1:1234';
  let selectedModel:string|null=null;
  let credentialConfigured=false;
  let compatibleEndpoint:string|null=null;
  let compatibleSelectedModel:string|null=null;
  let compatibleCredentialConfigured=false;
  let diagnostics=0;
  let loadedInstances:{id:string;context_length:number}[]=[];
  const modelLoads:{model:string;context_length?:number}[]=[];
  const modelEstimates:{model:string;context_length?:number}[]=[];
  const modelUnloads:string[]=[];
  let releaseInitialConfig!:()=>void;
  let holdInitialConfig=true;
  const initialConfigGate=new Promise<void>(resolve=>{releaseInitialConfig=resolve;});
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model',context_length:32768,size_bytes:2500000000,loaded:loadedInstances.length>0,loaded_instances:loadedInstances,capabilities:{vision:false,trained_for_tool_use:true}},...(compatibleEndpoint?[{id:'openai-compatible::remote-model',capabilities:{vision:false,trained_for_tool_use:false}}]:[])],headers});
    if(path==='/models/load'&&req.method()==='POST'){
      const data=req.postDataJSON();modelLoads.push(data);loadedInstances=[{id:'fixture-instance-1',context_length:data.context_length??4096}];
      return route.fulfill({json:{instance_id:'fixture-instance-1',status:'loaded'},headers});
    }
    if(path==='/providers/lm-studio/model-estimate'&&req.method()==='POST'){
      const data=req.postDataJSON();modelEstimates.push(data);
      return route.fulfill({json:{model:data.model,context_length:data.context_length??null,status:'estimated',estimated_gpu_memory_bytes:2500000000,estimated_total_memory_bytes:4000000000,guardrail:'may_load',message:'Estimate only; no model was loaded or downloaded.'},headers});
    }
    if(path==='/models/unload'&&req.method()==='POST'){
      const id=req.postDataJSON().instance_id;modelUnloads.push(id);loadedInstances=loadedInstances.filter(instance=>instance.id!==id);
      return route.fulfill({json:{instance_id:id,status:'unloaded'},headers});
    }
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'},{id:'openai-compatible',status:compatibleEndpoint?'connected':'not_configured'}],headers});
    if(path==='/providers/lm-studio/config'&&req.method()==='GET'){
      if(holdInitialConfig){holdInitialConfig=false;await initialConfigGate;}
      return route.fulfill({json:{endpoint,selected_model:selectedModel},headers});
    }
    if(path==='/providers/openai-compatible/config'&&req.method()==='GET')return route.fulfill({json:{endpoint:compatibleEndpoint,selected_model:compatibleSelectedModel},headers});
    if(path==='/providers/openai-compatible/config'&&req.method()==='PUT'){
      compatibleEndpoint=req.postDataJSON().endpoint;compatibleSelectedModel=null;
      return route.fulfill({json:{endpoint:compatibleEndpoint,selected_model:null},headers});
    }
    if(path==='/providers/openai-compatible/credential'&&req.method()==='GET')return route.fulfill({json:{configured:compatibleCredentialConfigured},headers});
    if(path==='/providers/openai-compatible/credential'&&req.method()==='PUT'){
      expect(req.postDataJSON().token).toBe('compatible-test-token');compatibleCredentialConfigured=true;
      return route.fulfill({json:{configured:true},headers});
    }
    if(path==='/providers/openai-compatible/credential/diagnostics'&&req.method()==='POST')return route.fulfill({json:{status:'verified',model_count:1,message:'The provider accepted the saved token and returned its model list.'},headers});
    if(path==='/providers/openai-compatible/credential'&&req.method()==='DELETE'){
      compatibleCredentialConfigured=false;return route.fulfill({json:{configured:false},headers});
    }
    if(path==='/providers/lm-studio/credential'&&req.method()==='GET')return route.fulfill({json:{configured:credentialConfigured},headers});
    if(path==='/providers/lm-studio/credential'&&req.method()==='PUT'){
      expect(req.postDataJSON().token).toBe('test-api-token');credentialConfigured=true;
      return route.fulfill({json:{configured:true},headers});
    }
    if(path==='/providers/lm-studio/credential'&&req.method()==='DELETE'){
      credentialConfigured=false;return route.fulfill({json:{configured:false},headers});
    }
    if(path==='/providers/lm-studio/config'&&req.method()==='PUT'){
      endpoint=req.postDataJSON().endpoint==='http://localhost:1234'?'http://127.0.0.1:1234':req.postDataJSON().endpoint;selectedModel=null;
      return route.fulfill({json:{endpoint,selected_model:null},headers});
    }
    if(path==='/providers/lm-studio/selection'&&req.method()==='PUT'){
      selectedModel=req.postDataJSON().model;compatibleSelectedModel=null;
      return route.fulfill({json:{model:selectedModel},headers});
    }
    if(path==='/providers/openai-compatible/selection'&&req.method()==='PUT'){
      compatibleSelectedModel=req.postDataJSON().model;selectedModel=null;
      return route.fulfill({json:{model:compatibleSelectedModel},headers});
    }
    if(path==='/providers/diagnostics'&&req.method()==='POST'){
      const model=req.postDataJSON().model;diagnostics++;
      return route.fulfill({json:{model,tool_round_trip_verified:true},headers});
    }
    if(path==='/memory'||path==='/skills')return route.fulfill({json:[],headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'unavailable',distributions:[]},headers});
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'Model setup');
  const lmStudioEndpoint=page.getByLabel('LM Studio server address');
  await expect(lmStudioEndpoint).toBeDisabled();
  releaseInitialConfig();
  await expect(lmStudioEndpoint).toBeEnabled();
  await expect(page.getByRole('status').filter({hasText:'LM Studio is connected'})).toBeVisible();
  await expect(page.getByRole('option',{name:'fixture/model',exact:true})).toHaveCount(1);
  await page.getByLabel('Optional LM Studio API token').fill('test-api-token');
  await page.getByRole('button',{name:'Save API token securely'}).click();
  await expect(page.getByText('A token is saved in Windows Credential Manager. The token is never shown after saving.')).toBeVisible();
  await expect(page.getByLabel('Optional LM Studio API token')).toHaveValue('');
  await page.getByRole('button',{name:'Clear saved API token'}).click();
  await expect(page.getByText('No API token is saved. The token is never shown after saving.')).toBeVisible();
  await lmStudioEndpoint.fill('http://localhost:1234');
  await page.getByRole('button',{name:'Save address and connect'}).click();
  await expect.poll(()=>endpoint).toBe('http://127.0.0.1:1234');
  await page.getByLabel('Local model or compatible provider model').selectOption('fixture/model');
  await expect.poll(()=>selectedModel).toBe('fixture/model');
  await expect(page.getByRole('status').filter({hasText:'Installed model files: 2.5 GB. Model is installed and not loaded.'})).toBeVisible();
  const localCapabilities=page.getByRole('region',{name:'Capabilities for fixture/model'});
  await expect(localCapabilities).toContainText('Vision input');
  await expect(localCapabilities).toContainText('Not reported');
  await expect(localCapabilities).toContainText('Tool-use training');
  await expect(localCapabilities).toContainText('Reported');
  await expect(localCapabilities).toContainText('Tool use still requires the harmless diagnostic below before workspace tools are enabled.');
  await expect(page.getByLabel('LM Studio model lifecycle')).toBeVisible();
  await page.getByRole('button',{name:'Estimate model memory'}).click();
  await expect.poll(()=>modelEstimates).toEqual([{model:'fixture/model'}]);
  await expect(page.getByRole('status').filter({hasText:'LM Studio estimate · advisory only'})).toContainText('GPU memory: 2.5 GB · Total memory: 4.0 GB');
  await expect(page.getByRole('status').filter({hasText:'LM Studio estimate · advisory only'})).toContainText('No model was loaded or downloaded.');
  expect(modelLoads).toEqual([]);
  await page.getByLabel('Context length for new instance (optional)').fill('65536');
  await page.getByRole('button',{name:'Load model',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('exceeds the discovered model maximum');
  expect(modelLoads).toEqual([]);
  await page.getByLabel('Context length for new instance (optional)').fill('8192');
  await page.getByRole('button',{name:'Load model',exact:true}).click();
  await expect.poll(()=>modelLoads).toEqual([{model:'fixture/model',context_length:8192}]);
  await expect(page.getByRole('status').filter({hasText:'Loaded model instance fixture-instance-1.'})).toBeVisible();
  const loadedInstancesList=page.getByRole('list',{name:'Loaded model instances'});
  await expect(loadedInstancesList).toContainText('fixture-instance-1');
  await expect(loadedInstancesList).toContainText('8,192 token context');
  await loadedInstancesList.getByRole('button',{name:'Unload this instance'}).click();
  await expect.poll(()=>modelUnloads).toEqual(['fixture-instance-1']);
  await expect(page.getByText('No model instances are currently loaded.')).toBeVisible();
  await page.getByRole('button',{name:'Run harmless tool-use diagnostic'}).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.')).toBeVisible();
  await page.getByLabel('Provider server address').fill('https://api.example.test');
  await page.getByRole('button',{name:'Save address and discover models'}).click();
  await expect(page.getByRole('option',{name:'openai-compatible::remote-model',exact:true})).toHaveCount(1);
  await page.getByLabel('Local model or compatible provider model').selectOption('openai-compatible::remote-model');
  await expect.poll(()=>compatibleSelectedModel).toBe('openai-compatible::remote-model');
  const compatibleCapabilities=page.getByRole('region',{name:'Capabilities for openai-compatible::remote-model'});
  await expect(compatibleCapabilities).toContainText('Vision input');
  await expect(compatibleCapabilities).toContainText('Tool-use training');
  await expect(compatibleCapabilities).toContainText('Not reported');
  await page.getByLabel('Optional provider API token').fill('compatible-test-token');
  await page.getByRole('button',{name:'Save provider token securely'}).click();
  await expect(page.getByLabel('Optional provider API token')).toHaveValue('');
  await page.getByRole('button',{name:'Check saved token'}).click();
  await expect(page.getByRole('status').filter({hasText:'The provider accepted the saved token'})).toBeVisible();
  await page.getByRole('button',{name:'Run harmless tool-use diagnostic'}).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.')).toBeVisible();
  expect(diagnostics).toBe(2);
});
