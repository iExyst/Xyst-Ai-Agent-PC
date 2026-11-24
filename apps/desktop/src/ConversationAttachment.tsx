import {useEffect,useState} from 'react';
import type {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type Attachment=components['schemas']['CLIChatAttachmentRecord'];

const labels:Record<Attachment['media_type'],string>={
  'image/png':'PNG image',
  'image/jpeg':'JPEG image',
  'image/gif':'GIF image',
  'image/webp':'WebP image',
  'application/pdf':'PDF document',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'Word document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'Excel workbook',
  'text/plain':'Text document',
  'text/markdown':'Markdown document',
  'audio/wav':'WAV audio',
};

const extensions:Record<Attachment['media_type'],string>={
  'image/png':'png','image/jpeg':'jpg','image/gif':'gif','image/webp':'webp',
  'application/pdf':'pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'xlsx',
  'text/plain':'txt','text/markdown':'md','audio/wav':'wav',
};

const imageTypes=new Set<Attachment['media_type']>(['image/png','image/jpeg','image/gif','image/webp']);

export function ConversationAttachment({attachment,client}:{attachment:Attachment;client:EngineClient}){
  const [preview,setPreview]=useState('');
  const [unavailable,setUnavailable]=useState(false);
  const [downloading,setDownloading]=useState(false);
  const isImage=imageTypes.has(attachment.media_type);
  useEffect(()=>{
    if(!isImage)return;
    let active=true;
    void client.artifactPreview(attachment.artifact_id)
      .then(value=>{if(active)setPreview(value);})
      .catch(()=>{if(active)setUnavailable(true);});
    return()=>{active=false;};
  },[attachment.artifact_id,client,isImage]);
  async function download(){
    setDownloading(true);
    try{
      const blob=await client.artifactDownload(attachment.artifact_id);
      const url=URL.createObjectURL(blob);
      const link=document.createElement('a');
      link.href=url;
      link.download=attachment.filename||`xaa-attachment.${extensions[attachment.media_type]}`;
      link.click();
      URL.revokeObjectURL(url);
    }catch{setUnavailable(true);}
    finally{setDownloading(false);}
  }
  return <figure className="conversation-attachment">
    {isImage&&preview.startsWith('data:image/')&&<img src={preview} alt={`Saved ${attachment.filename||labels[attachment.media_type]} attachment`}/>}
    {!isImage&&<button type="button" className="secondary-button" onClick={()=>void download()} disabled={downloading}>{downloading?'Preparing download…':`Download ${labels[attachment.media_type]}`}</button>}
    <figcaption>{attachment.filename||labels[attachment.media_type]} · {labels[attachment.media_type]} · {(attachment.size_bytes/1024).toFixed(1)} KiB · retained with run{unavailable?' · unavailable':''}</figcaption>
  </figure>;
}
