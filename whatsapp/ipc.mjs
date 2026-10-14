// Two bounded anonymous IPC lanes share one authenticated provider process.
import net from 'node:net';
import {once} from 'node:events';

const MAX_FRAME=1024*1024;

export function sendingSocket() {
  const raw=process.env.ANYMSG_WHATSAPP_SEND_FD;
  if(!/^[0-9]+$/.test(raw || '') || !Number.isSafeInteger(Number(raw)) || Number(raw)<3)
    throw new Error('private_sending_channel_required');
  return new net.Socket({fd:Number(raw),readable:true,writable:true});
}

async function* frames(input) {
  let pending=Buffer.alloc(0);
  for await(const chunk of input) {
    let offset=0;
    while(offset<chunk.length) {
      const newline=chunk.indexOf(10,offset),end=newline<0?chunk.length:newline;
      if(pending.length+end-offset+1>MAX_FRAME)throw new Error('provider_request_too_large');
      pending=Buffer.concat([pending,chunk.subarray(offset,end)]);
      if(newline<0)break;
      yield pending.toString('utf8');pending=Buffer.alloc(0);offset=newline+1;
    }
  }
  if(pending.length)throw new Error('provider_protocol_error');
}

export async function runLane(input,output,{methods,handle,safe=new Set(),onError=async()=>{}}) {
  const allowed=new Set(methods);
  async function emit(value) {
    const line=JSON.stringify(value)+'\n';
    if(Buffer.byteLength(line)>MAX_FRAME)throw new Error('provider_response_too_large');
    if(!output.write(line))await once(output,'drain');
  }
  // One in-flight request per lane. Backpressure remains in that lane only.
  for await(const line of frames(input)) {
    let request;
    try {
      request=JSON.parse(line);
      if(!request || !Number.isSafeInteger(request.id) || request.id<1 || !allowed.has(request.method))
        throw new Error('invalid_request');
      const result=await handle(request,emit);
      await emit({id:request.id,result});
    } catch(error) {
      await emit({id:request?.id,error:safe.has(error.message)?error.message:'provider_operation_failed'});
      await onError(error);
    }
  }
}
