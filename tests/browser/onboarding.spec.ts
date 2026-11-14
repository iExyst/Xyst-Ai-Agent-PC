import {test,expect,type Page} from '@playwright/test';

function relativeLuminance(color:string){
  const channels=color.match(/[\d.]+/g)?.slice(0,3).map(Number);
  if(!channels||channels.length!==3)throw new Error(`Unexpected computed color: ${color}`);
  const [red,green,blue]=channels.map(value=>{const normalized=value/255;return normalized<=0.04045?normalized/12.92:((normalized+0.055)/1.055)**2.4;});
  return 0.2126*red!+0.7152*green!+0.0722*blue!;
}

function contrastRatio(foreground:string,background:string){
  const first=relativeLuminance(foreground),second=relativeLuminance(background);
  return (Math.max(first,second)+0.05)/(Math.min(first,second)+0.05);
}

async function visibleTextContrastFailures(page:Page){
  const pairs=await page.evaluate(()=>{
    const result:{text:string;foreground:string;background:string;minimum:number}[]=[];
    for(const element of Array.from(document.querySelectorAll<HTMLElement>('body *'))){
      const text=Array.from(element.childNodes).filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent?.trim()??'').filter(Boolean).join(' ');
      if(!text||!element.getClientRects().length||element.closest('[aria-hidden="true"],:disabled'))continue;
      const style=getComputedStyle(element);
      if(style.display==='none'||style.visibility==='hidden'||Number(style.opacity)===0)continue;
      let ancestor:HTMLElement|null=element;
      let background:string|undefined;
      let gradient=false;
      while(ancestor){
        const backgroundStyle=getComputedStyle(ancestor);
        if(backgroundStyle.backgroundImage!=='none'){gradient=true;break;}
        const channels=backgroundStyle.backgroundColor.match(/[\d.]+/g)?.map(Number);
        if(channels&&channels.length>=3&&(channels.length<4||channels[3]!>0)){background=backgroundStyle.backgroundColor;break;}
        ancestor=ancestor.parentElement;
      }
      if(gradient)continue;
      result.push({text:text.slice(0,90),foreground:style.color,background:background??'rgb(244, 246, 249)',minimum:parseFloat(style.fontSize)>=24||(parseFloat(style.fontSize)>=18.66&&Number(style.fontWeight)>=700)?3:4.5});
    }
    return result;
  });
  return pairs.filter(pair=>contrastRatio(pair.foreground,pair.background)<pair.minimum);
}

test('onboarding requires a real tool diagnostic and runs its demonstration in Observe mode',async({page})=>{
  let created:Record<string,unknown>|undefined;
  const run={id:'onboarding-run',objective:'Read project documentation',state:'verifying',acceptance_criteria:[],revision:1,model:'fixture/model',limits:{max_iterations:8,max_seconds:900}};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers/lm-studio/config'&&request.method()==='GET')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/providers/lm-studio/selection')return route.fulfill({json:{model:request.postDataJSON().model},headers});
    if(path==='/providers/diagnostics')return route.fulfill({json:{model:'fixture/model',tool_round_trip_verified:true},headers});
    if(path==='/system/resources')return route.fulfill({json:{sampled_at:'2026-11-14T17:00:00Z',platform:'win32',logical_processors:8,cpu_percent:24,memory_total_bytes:16*1024**3,memory_available_bytes:8*1024**3,memory_used_percent:50,disk_total_bytes:512*1024**3,disk_free_bytes:256*1024**3,queue_active:false,queue_waiting:0,queue_reserved_tokens:0,queue_max_reserved_tokens:200000,queue_max_pending:4,gpu_devices:[],accelerator_telemetry_available:false},headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'unsupported',distributions:[]},headers});
    if(path==='/runs'&&request.method()==='POST'){created=request.postDataJSON();return route.fulfill({json:run,headers});}
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'run.state_changed',payload:{to:'verifying'},created_at:'2026-11-05T00:00:00Z'})}\n\nevent: done\ndata: {}\n\n`});
    if(path==='/runs/onboarding-run')return route.fulfill({json:run,headers});
    return route.fulfill({json:[],headers});
  });
  await page.goto('/');
  await page.keyboard.press('Tab');
  const skipLink=page.getByRole('link',{name:'Skip to main content'});
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('main')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Engine token')).toBeFocused();
  await expect(page.getByRole('note')).toContainText('keeps it running in the system tray');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByLabel('Engine token').focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button',{name:'Connect',exact:true})).toBeFocused();
  await page.keyboard.press('Enter');
  const navigation=page.getByRole('navigation',{name:'Main sections'});
  await expect(navigation.getByRole('link',{name:'New objective'})).toBeVisible();
  expect(await visibleTextContrastFailures(page)).toEqual([]);
  const placeholderPairs=await Promise.all([page.getByLabel('Workspace path').evaluate(element=>({foreground:getComputedStyle(element,'::placeholder').color,background:getComputedStyle(element).backgroundColor})),page.locator('#run-objective').evaluate(element=>({foreground:getComputedStyle(element,'::placeholder').color,background:getComputedStyle(element).backgroundColor}))]);
  for(const pair of placeholderPairs)expect(contrastRatio(pair.foreground,pair.background)).toBeGreaterThanOrEqual(4.5);
  await expect(navigation.locator('a.active')).toHaveCount(1);
  await expect(navigation.getByRole('link',{name:'New objective'})).toHaveAttribute('aria-current','page');
  await navigation.getByRole('link',{name:'New objective'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#new-objective')).toBeInViewport();
  await expect(page.getByRole('heading',{level:1})).toBeFocused();
  await navigation.getByRole('link',{name:'Tools & integrations'}).click();
  await expect(navigation.locator('a.active')).toHaveCount(1);
  await expect(navigation.getByRole('link',{name:'Tools & integrations'})).toHaveAttribute('aria-current','page');
  await expect(page.locator('#integrations')).toBeInViewport();
  await expect(page.getByRole('heading',{level:1})).toBeFocused();
  expect(await visibleTextContrastFailures(page)).toEqual([]);
  await expect(page.getByLabel('Local model')).toHaveCount(0);
  await navigation.getByRole('link',{name:'Model setup'}).click();
  await expect(navigation.locator('a.active')).toHaveCount(1);
  await expect(navigation.getByRole('link',{name:'Model setup'})).toHaveAttribute('aria-current','page');
  await expect(page.getByRole('heading',{name:'Model setup'})).toBeVisible();
  await expect(page.getByRole('heading',{level:1})).toBeFocused();
  await expect(page.getByLabel('Local model')).toBeVisible();
  expect(await visibleTextContrastFailures(page)).toEqual([]);
  await navigation.getByRole('link',{name:'System monitor'}).click();
  await expect(page.getByRole('heading',{level:1,name:'System monitor'})).toBeVisible();
  expect(await visibleTextContrastFailures(page)).toEqual([]);
  await navigation.getByRole('link',{name:'Settings'}).click();
  await expect(page.getByRole('heading',{level:1,name:'Settings'})).toBeVisible();
  expect(await visibleTextContrastFailures(page)).toEqual([]);
  await navigation.getByRole('link',{name:'Model setup'}).click();
  await expect(page.getByRole('heading',{level:1,name:'Model setup'})).toBeVisible();
  const demo=page.getByRole('button',{name:'Start read-only onboarding demonstration'});
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByRole('button',{name:'Run harmless tool-use diagnostic'}).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.')).toBeVisible();
  await navigation.getByRole('link',{name:'New objective'}).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture-project');
  await expect(demo).toBeEnabled();
  await demo.click();
  await expect(page.getByRole('heading',{name:'Active run verifying'})).toBeVisible();
  await navigation.getByRole('link',{name:'Active run'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#active-run')).toBeInViewport();
  await expect(page.getByRole('heading',{level:1})).toBeFocused();
  await expect(navigation.locator('a.active')).toHaveCount(1);
  await expect(navigation.getByRole('link',{name:'Active run'})).toHaveAttribute('aria-current','page');
  await navigation.getByRole('link',{name:'Saved runs'}).click();
  await expect(navigation.locator('a.active')).toHaveCount(1);
  await expect(navigation.getByRole('link',{name:'Saved runs'})).toHaveAttribute('aria-current','page');
  await expect(navigation.getByRole('link',{name:'Active run'})).not.toHaveAttribute('aria-current','page');
  expect(created).toMatchObject({workspace:'C:\\fixture-project',mode:'observe',command_grant:null});
  expect(created?.objective).toContain('Do not modify files or run commands');
});
