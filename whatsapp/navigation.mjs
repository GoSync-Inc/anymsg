// whatsapp-web.js inject() polls evaluate() before installing its navigation
// listener. Chromium can replace that context while restoring a linked session.
// Retry only this known lifecycle error, on the SAME browser/session. Never
// reset authentication, launch another browser, or swallow other failures.
export function recoverNavigation(client,{maxAttempts=3,timeoutMs=60000}={}) {
  const inject=client.inject.bind(client);
  let pending;
  client.inject=function(){
    if(pending)return pending;
    const run=async()=>{
      const deadline=Date.now()+timeoutMs;
      for(let attempt=1;;attempt++){
        try{return await inject();}
        catch(error){
          if(!String(error?.message || '').includes('Execution context was destroyed') ||
              attempt>=maxAttempts || Date.now()>=deadline)throw error;
          // waitForFunction survives context replacement; dispose its handle.
          const handle=await client.pupPage.waitForFunction('window.Debug?.VERSION != undefined',
            {timeout:Math.max(1,deadline-Date.now())});
          await handle.dispose();
        }
      }
    };
    const task=Promise.resolve().then(run);pending=task;
    const clear=()=>{if(pending===task)pending=undefined;};
    task.then(clear,clear);return task;
  };
  return client;
}
