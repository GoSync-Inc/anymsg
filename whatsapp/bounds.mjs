// Bound the CDP round trip outside Chromium. A browser-side timer cannot cover
// a stalled evaluate, model serialization or delivery of its result to Node.
class EvaluationBusy extends Error {}
class EvaluationTimeout extends Error {}

class SingleEvaluation {
  pending=null;
  async run(operation,timeoutMs) {
    if(this.pending)throw new EvaluationBusy();
    const task=Promise.resolve().then(operation);
    this.pending=task;
    const clear=()=>{if(this.pending===task)this.pending=null;};
    // Keep the slot after timeout: Promise.race cannot cancel a CDP operation.
    // Observe late rejection too, without creating an unhandled finally chain.
    task.then(clear,clear);
    let timer;
    try {
      return await Promise.race([task,new Promise((resolve,reject)=>{
        timer=setTimeout(()=>reject(new EvaluationTimeout()),timeoutMs);
      })]);
    }finally{clearTimeout(timer);}
  }
}

export class PageEvaluationStalled extends Error {
  constructor(){super('provider_page_stalled');this.name='PageEvaluationStalled';}
}

export function createPageBounds({readTimeoutMs=15000,namesTimeoutMs=2000}={}) {
  for(const value of [readTimeoutMs,namesTimeoutMs])
    if(!Number.isFinite(value) || value<=0)throw new RangeError('invalid_evaluation_timeout');
  const reads=new SingleEvaluation(),names=new SingleEvaluation();
  return {
    async read(operation) {
      try{return await reads.run(operation,readTimeoutMs);}
      catch(error){
        if(error instanceof EvaluationTimeout || error instanceof EvaluationBusy)
          throw new PageEvaluationStalled();
        throw error;
      }
    },
    async names(ids,operation) {
      if(!ids.length)return {};
      try{return await names.run(()=>operation(ids),namesTimeoutMs);}
      catch{return {};}
    },
  };
}
