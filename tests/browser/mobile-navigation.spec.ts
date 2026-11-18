import { test, expect } from '@playwright/test';

test('mobile navigation shows destination labels and scrolls through every section', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');

  const navigation=page.getByRole('navigation',{name:'Main sections'});
  await expect(navigation).toBeVisible({timeout:15000});
  for(const label of ['New objective','Saved runs','Chats','Memory','Skills','Automations','Tools & integrations','Model setup','System monitor','Settings']){
    const link=navigation.getByRole('link',{name:new RegExp(label)});
    await expect(link).toBeVisible({timeout:10000});
    await expect(link).toHaveCSS('font-size','12px');
    const target=await link.evaluate(element=>({width:element.getBoundingClientRect().width,height:element.getBoundingClientRect().height}));
    expect(target.width,`${label} should have a readable touch target`).toBeGreaterThanOrEqual(44);
    expect(target.height,`${label} should have a readable touch target`).toBeGreaterThanOrEqual(44);
    const labelFits=await link.evaluate(element=>{
      const textNode=[...element.childNodes].find(child=>child.nodeType===Node.TEXT_NODE&&child.textContent?.trim());
      if(!textNode)return false;
      const range=document.createRange();range.selectNodeContents(textNode);
      const bounds=element.getBoundingClientRect();
      return [...range.getClientRects()].length>0&&[...range.getClientRects()].every(rect=>rect.width>0&&rect.left>=bounds.left&&rect.right<=bounds.right+1&&rect.top>=bounds.top&&rect.bottom<=bounds.bottom+1);
    });
    expect(labelFits,`${label} label should remain visible inside its mobile navigation target`).toBe(true);
  }
  const dimensions=await navigation.evaluate(element=>({clientWidth:element.clientWidth,scrollWidth:element.scrollWidth}));
  expect(dimensions.scrollWidth).toBeGreaterThan(dimensions.clientWidth);

  await page.evaluate(()=>{document.body.setAttribute('tabindex','-1');document.body.focus();});
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link',{name:'Skip to main content'})).toBeFocused();
  const labels=['New objective','Saved runs','Chats','Memory','Skills','Automations','Tools & integrations','Model setup','System monitor','Settings'];
  for(const label of labels){
    const link=navigation.getByRole('link',{name:new RegExp(label)});
    await page.keyboard.press('Tab');
    await expect(link,`${label} should be reachable with Tab`).toBeFocused();
    await expect.poll(()=>link.evaluate(element=>{
      const parent=element.parentElement!.getBoundingClientRect();
      const bounds=element.getBoundingClientRect();
      const style=getComputedStyle(element);
      return bounds.left>=parent.left&&bounds.right<=parent.right&&style.outlineStyle!=='none'&&parseFloat(style.outlineWidth)>=3;
    }),{message:`${label} keyboard focus should scroll fully into view with a visible indicator`}).toBe(true);
  }
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{level:1})).toHaveText('Settings');
});
