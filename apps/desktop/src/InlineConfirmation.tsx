import {useEffect,useRef,type ReactNode} from 'react';

export function InlineConfirmation({label,id,open,children}:{label:string;id:string;open:boolean;children:ReactNode}) {
  const group=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!open)return;
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:undefined;
    group.current?.focus();
    return ()=>{if(previous?.isConnected)previous.focus();};
  },[open]);
  return <div ref={group} id={id} className="inline-confirmation" role="group" aria-label={label} tabIndex={-1} hidden={!open}>{children}</div>;
}
