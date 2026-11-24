import {useEffect,useState} from 'react';
import type {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type Attachment=components['schemas']['CLIChatAttachmentRecord'];

const labels:Record<Attachment['media_type'],string>={
  'image/png':'PNG image',
  'image/jpeg':'JPEG image',
  'image/gif':'GIF image',
  'image/webp':'WebP image',
};

export function ConversationAttachment({attachment,client}:{attachment:Attachment;client:EngineClient}){
  const [preview,setPreview]=useState('');
  const [unavailable,setUnavailable]=useState(false);
  useEffect(()=>{
    let active=true;
    void client.artifactPreview(attachment.artifact_id)
      .then(value=>{if(active)setPreview(value);})
      .catch(()=>{if(active)setUnavailable(true);});
    return()=>{active=false;};
  },[attachment.artifact_id,client]);
  return <figure className="conversation-attachment">
    {preview.startsWith('data:image/')&&<img src={preview} alt={`Saved ${labels[attachment.media_type]} attachment`}/>}
    <figcaption>{labels[attachment.media_type]} · {(attachment.size_bytes/1024).toFixed(1)} KiB · retained with run{unavailable?' · preview unavailable':''}</figcaption>
  </figure>;
}
