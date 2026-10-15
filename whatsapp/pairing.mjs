// Pairing is successful only after a sequential cold reopen without another QR.
// LocalAuth has no session-saved event; ready alone does not test restoration.
export class PairingError extends Error {}

async function closeCleanly(client,timeoutMs) {
  // Puppeteer Browser.close() may swallow its own errors. Observe the actual
  // Chromium exit before destroy, including a process that has already exited.
  const browser=client.pupBrowser;
  const child=browser?.process?.();
  let onExit,timer;
  const exited=child ? new Promise(resolve=>{
    if(child.exitCode!=null || child.signalCode!=null)
      resolve({code:child.exitCode,signal:child.signalCode});
    else {onExit=(code,signal)=>resolve({code,signal});child.once('exit',onExit);}
  }) : Promise.resolve(null);
  try {
    const [,outcome]=await Promise.race([
      Promise.all([Promise.resolve().then(async()=>{
        // whatsapp-web.js 1.34.7 checks the removed isConnected() method and
        // silently skips closing Puppeteer 25. Close our owned browser first;
        // still require the observed clean process exit and auth cleanup.
        if(child && child.exitCode==null && child.signalCode==null &&
            typeof browser.close==='function')await browser.close();
        await client.destroy();
      }),exited]),
      new Promise((resolve,reject)=>{
        timer=setTimeout(()=>reject(new PairingError('pairing_close_timeout')),timeoutMs);
      }),
    ]);
    if(!outcome || outcome.code!==0 || outcome.signal!=null)
      throw new PairingError('pairing_close_failed');
  }catch(error){
    if(error instanceof PairingError)throw error;
    throw new PairingError('pairing_close_failed');
  }finally{
    clearTimeout(timer);
    if(onExit)child.removeListener('exit',onExit);
  }
}

export function createPairingCoordinator({createClient,expectedWid,onQr,closeTimeoutMs=10000}) {
  let active,runTask,stopTask,cancelReady,stopped=false;
  const closures=new WeakMap();
  const close=client=>{
    if(!closures.has(client))closures.set(client,Promise.resolve().then(()=>closeCleanly(client,closeTimeoutMs)));
    return closures.get(client);
  };
  const closeActive=async()=>{
    const client=active;
    if(client){await close(client);if(active===client)active=undefined;}
  };
  async function phase(allowQr) {
    if(stopped)throw new PairingError('pairing_cancelled');
    const client=createClient();active=client;
    let failure,resolveReady,rejectReady;
    const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
    const fail=code=>{
      failure ??= new PairingError(code);
      rejectReady(failure);
    };
    const handlers={
      ready:()=>{
        if(client.info?.wid?._serialized!==expectedWid)fail('whatsapp_identity_mismatch');
        else resolveReady();
      },
      qr:value=>{
        if(!allowQr){fail('pairing_restore_requires_qr');return;}
        try{onQr(value);}catch{fail('pairing_qr_failed');}
      },
      auth_failure:()=>fail('pairing_auth_failed'),
      disconnected:()=>fail('pairing_disconnected'),
    };
    for(const [name,handler] of Object.entries(handlers))client.on(name,handler);
    cancelReady=()=>fail('pairing_cancelled');
    try {
      // Do not close from inside the ready callback while initialize is still
      // installing its hooks. Both promises remain under the Python deadline.
      await Promise.all([ready,Promise.resolve().then(()=>client.initialize())]);
      if(stopped)throw new PairingError('pairing_cancelled');
      if(failure)throw failure;
      await close(client);
      if(failure)throw failure;
    }finally{
      try{await close(client);}
      finally{
        for(const [name,handler] of Object.entries(handlers))client.removeListener(name,handler);
        cancelReady=undefined;
        if(active===client)active=undefined;
      }
    }
  }
  return {
    run(){
      if(!runTask)runTask=Promise.resolve().then(async()=>{
        await phase(true);
        await phase(false);
        if(stopped)throw new PairingError('pairing_cancelled');
        return {restored:true};
      });
      return runTask;
    },
    stop(){
      stopped=true;cancelReady?.();
      if(!stopTask)stopTask=closeActive();
      return stopTask;
    },
  };
}
