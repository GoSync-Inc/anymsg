// Private bounded side channel: an unsolicited stdout frame would corrupt RPCs.
import fs from 'node:fs';
import path from 'node:path';
export const CONNECTION_ERRORS=new Set(['whatsapp_logged_out','whatsapp_pairing_required',
  'provider_auth_failed','whatsapp_identity_mismatch','provider_disconnected','provider_not_ready',
  'provider_initialization_failed','provider_page_stalled','durable_capture_failed',
  'spool_full','disk_reserve','capture_overload']);
export function disconnectCode(reason) {
  return ['LOGOUT','UNPAIRED','UNPAIRED_IDLE'].includes(reason)?'whatsapp_logged_out':'provider_disconnected';
}
export function connectClient(client,onReady,onFailure,stop) {
  let connected=false,failure=null;
  return new Promise((resolve,reject)=>{
    const fail=code=>{failure??=code;onFailure(failure);reject(new Error(failure));if(connected)void stop(1);};
    client.once('qr',()=>fail('whatsapp_pairing_required'));
    client.once('auth_failure',()=>fail('provider_auth_failed'));
    client.once('disconnected',reason=>fail(disconnectCode(reason)));
    client.once('ready',async()=>{
      try{await onReady();if(failure){onFailure(failure);return;}connected=true;resolve();}
      catch(error){fail(CONNECTION_ERRORS.has(error.message)?error.message:'provider_not_ready');}
    });
    Promise.resolve().then(()=>client.initialize()).catch(()=>fail('provider_initialization_failed'));
  });
}
export function writeFailure(directory,code) {
  if(!CONNECTION_ERRORS.has(code))code='provider_disconnected';
  const target=path.join(directory,'.transport-failure');
  const temporary=target+'.pending-'+process.pid;
  let fd;
  try {
    fd=fs.openSync(temporary,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600);
    fs.writeFileSync(fd,JSON.stringify({code,observed_at:new Date().toISOString()}));fs.fsyncSync(fd);
    fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,target);
    const dir=fs.openSync(directory,fs.constants.O_RDONLY|fs.constants.O_DIRECTORY);
    try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}
  }finally{if(fd!==undefined)fs.closeSync(fd);try{fs.unlinkSync(temporary);}catch{}}
}
