// Only aggregate audio counters; never retain ICE addresses, device IDs or audio.
const finite=value=>Number.isFinite(value) && value>=0 ? value : null;
export class AudioHealth {
  constructor() {
    this.values={availability:'pending',samples:0,packets_received:null,packets_lost:null,
      jitter_ms:null,jitter_buffer_ms:null,concealed_samples:null,silent_concealed_samples:null,packets_discarded:null,concealment_events:null,
      total_samples_received:null,concealed_percent:null,browser_stalls:0,max_poll_gap_ms:0};
    this.lastTick=null;
  }
  tick(at) {
    if(this.lastTick!==null) {
      const gap=at-this.lastTick;
      this.values.max_poll_gap_ms=Math.max(this.values.max_poll_gap_ms,gap);
      if(gap>120)this.values.browser_stalls++;
    }
    this.lastTick=at;
  }
  async sample(pc) {
    if(!pc?.getStats) {if(!this.values.samples)this.values.availability='unavailable';return;}
    try {
      const reports=[...(await pc.getStats()).values()].filter(r=>r.type==='inbound-rtp' && (r.kind || r.mediaType)==='audio');
      if(!reports.length)return;
      this.values.availability='supported';this.values.samples++;
      const sum=key=>reports.every(r=>finite(r[key])!==null) ? reports.reduce((n,r)=>n+r[key],0) : null;
      for(const [field,key] of Object.entries({packets_received:'packetsReceived',packets_lost:'packetsLost',packets_discarded:'packetsDiscarded',
        concealed_samples:'concealedSamples',silent_concealed_samples:'silentConcealedSamples',
        concealment_events:'concealmentEvents',total_samples_received:'totalSamplesReceived'})) {
        const value=sum(key);
        if(value!==null)this.values[field]=Math.max(this.values[field] ?? 0,value);
      }
      const jitters=reports.map(r=>finite(r.jitter)).filter(v=>v!==null);
      if(jitters.length)this.values.jitter_ms=Math.max(...jitters)*1000;
      const delay=sum('jitterBufferDelay'),emitted=sum('jitterBufferEmittedCount');
      if(delay!==null && emitted>0)this.values.jitter_buffer_ms=delay/emitted*1000;
      if(this.values.concealed_samples!==null && this.values.total_samples_received>0)
        this.values.concealed_percent=Math.min(100,this.values.concealed_samples/this.values.total_samples_received*100);
    } catch {if(!this.values.samples)this.values.availability='unavailable';}
  }
  snapshot() {return {...this.values};}
}

export function audioHealthText(h) {
  if(!h || h.availability!=='supported')return 'Статистика аудио ещё не получена или недоступна в этом браузере.';
  const value=(n,suffix='')=>n===null || n===undefined ? 'нет данных' : `${Math.round(n*10)/10}${suffix}`;
  const nonSilent=Number.isFinite(h.concealed_samples) && Number.isFinite(h.silent_concealed_samples) && h.total_samples_received>0
    ? Math.max(0,h.concealed_samples-h.silent_concealed_samples)/h.total_samples_received*100 : null;
  const warning=h.total_samples_received>=240000 && (nonSilent ?? h.concealed_percent)>5
    ? (nonSilent===null ? 'Возможны пропуски звука. ' : 'Звук приходит с пропусками. ') : '';
  return warning+`Потеряно пакетов: ${value(h.packets_lost)} · замещено звуковых отсчётов: ${value(h.concealed_percent,'%')} (включая тишину; без замещённой тишины: ${value(nonSilent,'%')}) · джиттер: ${value(h.jitter_ms,' мс')} · буфер: ${value(h.jitter_buffer_ms,' мс')} · задержек браузера: ${h.browser_stalls}. Это измерения передачи; чистота голоса оценивается на слух.`;
}
