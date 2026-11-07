export type ActiveProcedure={
  id:string;
  name:string;
  description:string;
  category:string;
  lifecycle_state:string;
  active_version:number|null;
  latest_version:number;
};
export type ProcedureSuggestion<T extends ActiveProcedure=ActiveProcedure>={item:T;matchedTerms:string[];score:number};

const STOP_WORDS=new Set(['about','after','also','and','are','can','for','from','into','its','more','not','only','that','the','their','then','this','through','using','with','workspace','your']);

export function suggestActiveProcedures<T extends ActiveProcedure>(items:T[],objective:string):ProcedureSuggestion<T>[]{
  const query=objective.toLocaleLowerCase();
  const terms=new Set((query.match(/[a-z0-9]{3,}/g)??[]).filter(term=>!STOP_WORDS.has(term)));
  if(!terms.size)return [];
  return items.flatMap(item=>{
    if(item.lifecycle_state!=='active'||item.active_version===null)return [];
    const metadata=new Set(`${item.name} ${item.description} ${item.category}`.toLocaleLowerCase().match(/[a-z0-9]{3,}/g)??[]);
    const matchedTerms=[...terms].filter(term=>metadata.has(term));
    const exactName=item.name.trim().length>=4&&query.includes(item.name.trim().toLocaleLowerCase());
    const score=matchedTerms.length+(exactName?2:0);
    return score>=2?[{item,matchedTerms,score}]:[];
  }).sort((left,right)=>right.score-left.score||left.item.name.localeCompare(right.item.name)).slice(0,5);
}
