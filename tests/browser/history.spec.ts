import {test,expect} from '@playwright/test';

test('reopen saved runs and replay only the selected result',async({page})=>{
  const stamp='2026-11-03T00:00:00Z';
  const first={id:'first',objective:'Saved first objective',state:'completed',revision:4,acceptance_criteria:[],created_at:stamp};
  const older={...first,id:'older',objective:'Saved older objective'};
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs'){
      if(url.searchParams.get('scope')==='conversation'){
        expect(url.searchParams.get('q')).toBe('persisted result');
        expect(url.searchParams.has('before')).toBe(false);
        return route.fulfill({json:{items:[older],next_cursor:null},headers});
      }
      const items=url.searchParams.get('before')?[older]:[first];
      return route.fulfill({json:{items:items.filter(item=>item.objective.includes(url.searchParams.get('q')??'')),next_cursor:url.searchParams.get('before')?null:'older-page'},headers});
    }
    const selected=path.includes('/older')?older:first;
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/events/stream')){
      const event={sequence:1,event_type:'model.message',payload:{message:{role:'assistant',content:`Persisted result for ${selected.id}`}},created_at:stamp};
      return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify(event)}\n\nevent: done\ndata: {}\n\n`});
    }
    return route.fulfill({json:selected,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByRole('button',{name:'Load older runs',exact:true}).click();
  await page.getByRole('button',{name:'Open Saved first objective',exact:true}).click();
  await expect(page.getByText('Persisted result for first',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Active run completed'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Accepted result',exact:true})).toBeVisible();
  for(const action of ['pause','resume','cancel'])await expect(page.getByRole('button',{name:action,exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Open Saved older objective',exact:true}).click();
  await expect(page.getByText('Persisted result for older',{exact:true})).toBeVisible();
  await expect(page.getByText('Persisted result for first',{exact:true})).toHaveCount(0);
  await page.getByLabel('Search objective text (exact case)').fill('first');
  await page.getByRole('button',{name:'Search saved runs',exact:true}).click();
  await expect(page.getByRole('button',{name:'Open Saved first objective',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Open Saved older objective',exact:true})).toHaveCount(0);
  await page.getByLabel('Search in',{exact:true}).selectOption('conversation');
  await page.getByLabel('Search conversation phrase',{exact:true}).fill('persisted result');
  await page.getByRole('button',{name:'Search saved runs',exact:true}).click();
  await expect(page.getByRole('button',{name:'Open Saved older objective',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Open Saved first objective',exact:true})).toHaveCount(0);
  await expect(page.getByText('Searches objectives and public assistant answers.',{exact:false})).toBeVisible();
  expect(errors).toEqual([]);
  await page.screenshot({path:'test-results/history.png',fullPage:true});
});
