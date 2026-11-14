import {expect,type Page} from '@playwright/test';

export async function navigateMainSection(page:Page,name:string){
  const escaped=name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const link=page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:new RegExp(escaped)});
  await link.evaluate(element=>(element as HTMLAnchorElement).click());
  const heading=name==='Tools & integrations'?'Integrations':name==='Model setup'?'Model setup':name==='System monitor'?'System monitor':name==='Settings'?'Settings':name==='New objective'?'Workspace':name==='Active run'||name==='Saved runs'?'Runs and activity':undefined;
  if(heading) await expect(page.getByRole('heading',{level:1})).toHaveText(heading);
}

export async function selectLocalModel(page:Page,model:string,returnTo='New objective'){
  await navigateMainSection(page,'Model setup');
  await page.getByLabel(/Local model/).selectOption(model);
  await navigateMainSection(page,returnTo);
}
