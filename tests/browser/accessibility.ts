import type {Page} from '@playwright/test';

function relativeLuminance(color:string){
  const channels=color.match(/[\d.]+/g)?.slice(0,3).map(Number);
  if(!channels||channels.length!==3)throw new Error(`Unexpected computed color: ${color}`);
  const [red,green,blue]=channels.map(value=>{const normalized=value/255;return normalized<=0.04045?normalized/12.92:((normalized+0.055)/1.055)**2.4;});
  return 0.2126*red!+0.7152*green!+0.0722*blue!;
}

export function contrastRatio(foreground:string,background:string){
  const first=relativeLuminance(foreground),second=relativeLuminance(background);
  return (Math.max(first,second)+0.05)/(Math.min(first,second)+0.05);
}

export async function visibleTextContrastFailures(page:Page){
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
