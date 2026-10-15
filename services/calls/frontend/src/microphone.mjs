// The SDK still owns capture and transport. This controls its microphone gate.
export class MicrophoneControl {
  constructor(setMic,onChange=()=>{}) {
    this.setMic=setMic;this.onChange=onChange;
    this.ready=false;this.mode=false;this.held=false;this.normal=true;this.enabled=false;
  }
  sync() {
    const enabled=this.ready && (this.mode ? this.held : this.normal);
    if(enabled!==this.enabled) {this.enabled=enabled;this.setMic(enabled);}
    this.onChange(this);
  }
  setMode(value) {this.mode=!!value;this.held=false;this.normal=true;this.sync();}
  start() {this.ready=true;this.held=false;this.normal=true;this.enabled=null;this.sync();}
  stop() {this.ready=false;this.held=false;this.sync();}
  press() {if(this.ready && this.mode) {this.held=true;this.sync();}}
  release() {this.held=false;this.sync();}
  toggle() {if(this.ready && !this.mode) {this.normal=!this.normal;this.sync();}}
}

export function bindTalkButton(button,mic,win,doc) {
  button.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;
    event.preventDefault();button.setPointerCapture(event.pointerId);mic.press();
  });
  for(const name of ['pointerup','pointercancel','lostpointercapture','blur'])
    button.addEventListener(name,()=>mic.release());
  button.addEventListener('keydown',event=>{
    if(!['Space','Enter'].includes(event.code))return;
    event.preventDefault();if(!event.repeat)mic.press();
  });
  button.addEventListener('keyup',event=>{
    if(!['Space','Enter'].includes(event.code))return;
    event.preventDefault();mic.release();
  });
  win.addEventListener('blur',()=>mic.release());
  doc.addEventListener('visibilitychange',()=>{if(doc.hidden)mic.release();});
}
