// A media element drives remote WebRTC audio decoding in Chrome. Analyse the
// same stream separately without routing it to the speakers a second time.
export function startRemoteAudio(stream,doc=globalThis.document) {
  const player=doc.createElement('audio');
  player.autoplay=true;player.hidden=true;player.setAttribute('playsinline','');
  player.srcObject=stream;doc.body.append(player);
  return {player,ready:player.play()};
}

export function stopRemoteAudio(player) {
  if(!player)return;
  player.pause();player.srcObject=null;player.remove();
}
