import {test,expect} from '@playwright/test';
import {navigateMainSection} from './navigation';

test('suggests an active procedure but waits for explicit selection',async({page})=>{
  const active={id:'active-procedure',name:'Summarize Test Reports',description:'Summarize engineering test evidence',category:'testing',lifecycle_state:'active',pinned:false,active_version:2,lifecycle_revision:1,latest_version:3,created_at:'2026-11-04T00:00:00Z',updated_at:'2026-11-04T00:00:00Z'};
  const candidate={...active,id:'candidate-procedure',lifecycle_state:'candidate',active_version:null};
  const versions=[{skill_id:active.id,version:2,content:'Read tests and summarize evidence.',description:active.description,category:active.category,required_tools:[],required_permissions:['workspace.read'],evaluation_evidence:{state:'evaluated',provenance:'user',evaluations:[]},created_at:active.created_at}];
  let runSubmissions=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe('Bearer '+'x'.repeat(32));
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers')return route.fulfill({json:[],headers});
    if(path==='/runs'&&request.method()==='POST'){runSubmissions++;return route.fulfill({status:201,json:{id:'run-1',state:'queued'},headers});}
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/skills'&&request.method()==='GET')return route.fulfill({json:[active,candidate],headers});
    if(path===`/skills/${active.id}/versions/2`)return route.fulfill({json:versions[0],headers});
    if(path===`/skills/${active.id}/lifecycle`)return route.fulfill({json:[],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    return route.fulfill({json:{status:'ok'},headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Objective',{exact:true}).fill('Please summarize the engineering test reports');
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('summary').filter({hasText:'Reusable procedures'}).click();
  const panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
  const suggestions=page.getByRole('region',{name:'Active procedure suggestions',exact:true});
  await suggestions.getByRole('button',{name:'Find matching active procedures',exact:true}).click();
  await expect(suggestions).toContainText('Summarize Test Reports v2');
  await expect(suggestions).not.toContainText('candidate-procedure');
  await suggestions.getByRole('button',{name:'Inspect active version',exact:true}).click();
  await panel.getByRole('button',{name:'Select active procedure for next run',exact:true}).click();
  await navigateMainSection(page,'New objective');
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toContainText('Active procedure: Summarize Test Reports · version 2');
  expect(runSubmissions).toBe(0);
});
