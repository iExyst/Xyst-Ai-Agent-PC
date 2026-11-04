import {test,expect} from '@playwright/test';

for(const reconnect of [false,true])test(reconnect?'interrupted stream resumes into a saved candidate without repeating partial text':'partial streamed text is displayed without becoming a final candidate',async({page})=>{
  const run={id:'stream-run',objective:'Stream a response',state:'running',revision:2,acceptance_criteria:[]};
  const cursors:string[]=[];
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const path=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/events/stream')){
      cursors.push(request.headers()['last-event-id']??'');
      const partial=`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'model.delta',payload:{inference_id:'attempt',text:'Unfinished streamed text'},created_at:'2026-11-03T00:00:00Z'})}\n\n`;
      const final=`id: 2\nevent: run_event\ndata: ${JSON.stringify({sequence:2,event_type:'model.message',payload:{message:{content:'Recovered saved answer'}},created_at:'2026-11-03T00:00:01Z'})}\n\n`;
      return route.fulfill({headers,contentType:'text/event-stream',body:partial+(reconnect?(cursors.length===1?'id: 2\nevent: run_event\ndata: {':final+'event: done\ndata: {}\n\n'):'event: done\ndata: {}\n\n')});
    }
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  if(reconnect){
    await expect(page.getByRole('heading',{name:'Candidate answer',exact:true})).toBeVisible();
    await expect(page.getByText('Recovered saved answer',{exact:true})).toBeVisible();
    await expect(page.getByRole('heading',{name:'Partial response',exact:true})).toHaveCount(0);
    expect(cursors).toEqual(['0','1']);
    return;
  }
  await expect(page.getByRole('heading',{name:'Partial response',exact:true})).toBeVisible();
  await expect(page.getByText('Unfinished streamed text',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Candidate answer',exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Final run review'})).toHaveCount(0);
});
