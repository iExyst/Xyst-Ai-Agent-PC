import {useEffect,useRef,type ReactNode} from 'react';

export function InlineConfirmation({label,children}:{label:string;children:ReactNode}) {
  const group=useRef<HTMLDivElement>(null);
  useEffect(()=>{group.current?.focus();},[]);
  return <div ref={group} className="inline-confirmation" role="group" aria-label={label} tabIndex={-1}>{children}</div>;
}
