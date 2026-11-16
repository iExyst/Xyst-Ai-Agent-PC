import { test, expect } from '@playwright/test';

test('mobile navigation shows destination labels and scrolls through every section', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');

  const navigation=page.getByRole('navigation',{name:'Main sections'});
  for(const label of ['New objective','Saved runs','Chats','Memory','Skills','Automations','Tools & integrations','Model setup','System monitor','Settings']){
    const link=navigation.getByRole('link',{name:new RegExp(label)});
    await expect(link).toBeVisible();
    await expect(link).toHaveCSS('font-size','9px');
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

  await navigation.getByRole('link',{name:/Settings/}).click();
  await expect(page.getByRole('heading',{level:1})).toHaveText('Settings');
});
