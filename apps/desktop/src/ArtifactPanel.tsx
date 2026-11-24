import {useRef,useState} from 'react';
import type {Artifact,EngineClient} from './api/client.ts';

function artifactName(provenance:string):string {
  if(provenance==='git.diff')return 'Project changes';
  if(provenance==='user.input_document')return 'Original document';
  if(provenance==='user.input_image_original')return 'Original image';
  if(provenance==='user.input_document_text')return 'Extracted document text';
  if(provenance.startsWith('browser.screenshot:'))return 'Browser screenshot';
  if(provenance.startsWith('browser.download:'))return provenance.slice('browser.download:'.length)||'Downloaded file';
  if(provenance.startsWith('browser.'))return 'Browser evidence';
  return provenance.replaceAll(/[._:]+/g,' ').trim()||'Run artifact';
}

function formatSize(bytes:number):string {
  if(!Number.isFinite(bytes)||bytes<0)return 'Unknown size';
  if(bytes<1024)return `${bytes} B`;
  const units=['KiB','MiB','GiB'];
  let size=bytes/1024,index=0;
  while(size>=1024&&index<units.length-1){size/=1024;index++;}
  return `${size<10?size.toFixed(1):Math.round(size)} ${units[index]}`;
}

function mediaLabel(mediaType:string):string {
  if(mediaType==='image/png')return 'PNG image';
  if(mediaType==='image/jpeg')return 'JPEG image';
  if(mediaType==='image/gif')return 'GIF image';
  if(mediaType==='image/webp')return 'WebP image';
  if(mediaType==='application/pdf')return 'PDF document';
  if(mediaType.includes('spreadsheet'))return 'Spreadsheet';
  if(mediaType.includes('wordprocessing'))return 'Word document';
  if(mediaType==='text/plain'||mediaType==='text/markdown')return 'Text file';
  if(mediaType==='audio/wav')return 'WAV audio';
  return mediaType||'File';
}

export function ArtifactPanel({artifacts,client,onError}:{artifacts:Artifact[];client:EngineClient;onError:(message:string)=>void}) {
  const [preview,setPreview]=useState<{id:string;content:string}|undefined>();
  const [loadingId,setLoadingId]=useState('');
  const previewRequest=useRef(0);
  async function view(item:Artifact){
    const request=++previewRequest.current;
    setLoadingId(item.id);
    try{const content=await client.artifactPreview(item.id);if(previewRequest.current===request)setPreview({id:item.id,content});}
    catch(failure){onError(failure instanceof Error?failure.message:String(failure));}
    finally{if(previewRequest.current===request)setLoadingId('');}
  }
  async function download(item:Artifact){
    try{
      const blob=await client.artifactDownload(item.id);
      const url=URL.createObjectURL(blob);
      const link=document.createElement('a');
      link.href=url;
      link.download=item.provenance.startsWith('browser.download:')?item.provenance.slice('browser.download:'.length):item.media_type==='application/pdf'?'xaa-input-document.pdf':item.media_type==='audio/wav'?'xaa-input-audio.wav':item.media_type.includes('wordprocessing')?'xaa-input-document.docx':'xaa-input-document.xlsx';
      link.click();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(failure){onError(failure instanceof Error?failure.message:String(failure));}
  }
  return <section className="artifact-panel" aria-label="Files and artifacts"><header><div><p className="eyebrow">RUN OUTPUTS</p><h2>Files and artifacts</h2></div><span className="artifact-count">{artifacts.length} {artifacts.length===1?'file':'files'}</span></header>
    <ul className="artifact-grid">{artifacts.map(item=>{
      const downloadable=item.provenance.startsWith('browser.download:')||['application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','audio/wav'].includes(item.media_type);
      return <li className="artifact-card" key={item.id}>
        <div className="artifact-card-top"><span className="artifact-file-icon" aria-hidden="true">{item.media_type.startsWith('image/')?'▧':downloadable?'↓':'▤'}</span><span className="artifact-kind">{mediaLabel(item.media_type)}</span></div>
        <h3>{artifactName(item.provenance)}</h3>
        <p className="artifact-provenance">{item.provenance}</p>
        <div className="artifact-metadata"><span>{formatSize(item.size_bytes)}</span><span title={item.sha256?`SHA-256 ${item.sha256}`:'SHA-256 unavailable'}>{item.sha256?`SHA-256 · ${item.sha256.slice(0,12)}…`:'SHA-256 unavailable'}</span></div>
        {item.sha256&&<details className="artifact-checksum"><summary>Inspect full SHA-256</summary><code>{item.sha256}</code></details>}
        {downloadable?<button type="button" className="artifact-action" onClick={()=>void download(item)}>Download {item.provenance}</button>:<button type="button" className="artifact-action" disabled={loadingId===item.id} onClick={()=>void view(item)}>{loadingId===item.id?'Loading preview…':`View ${item.provenance}`}</button>}
      </li>;
    })}</ul>
    {preview&&<section className="artifact-preview" aria-label="Artifact preview"><header><div><p className="eyebrow">UNTRUSTED FILE CONTENT</p><h3>{artifactName(artifacts.find(item=>item.id===preview.id)?.provenance??'')}</h3></div><button type="button" className="secondary-button" onClick={()=>{previewRequest.current++;setLoadingId('');setPreview(undefined);}}>Close preview</button></header>{preview.content.startsWith('data:image/')?<img src={preview.content} alt="Untrusted image content"/>:<pre>{preview.content}</pre>}</section>}
  </section>;
}
