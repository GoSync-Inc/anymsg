// Synthetic encrypted stream, no provider/session/network and no whole-file buffer.
import {createCipheriv,createHash,createHmac,hkdfSync} from 'node:crypto';
import {decrypt} from './media.mjs';
const size=Number(process.argv[2] || 256)*1024**2,block=Buffer.alloc(65536,29),key=Buffer.alloc(32,41);
const expanded=Buffer.from(hkdfSync('sha256',key,Buffer.alloc(0),'WhatsApp Document Keys',112)),iv=expanded.subarray(0,16);
async function* encrypted(){
  const aes=createCipheriv('aes-256-cbc',expanded.subarray(16,48),iv),mac=createHmac('sha256',expanded.subarray(48,80)).update(iv);
  for(let left=size;left>0;left-=65536){const part=aes.update(block.subarray(0,Math.min(left,65536)));mac.update(part);yield part;}
  const final=aes.final();mac.update(final);yield final;yield mac.digest().subarray(0,10);
}
const encHash=createHash('sha256');for await(const part of encrypted())encHash.update(part);
const plainHash=createHash('sha256');for(let left=size;left>0;left-=65536)plainHash.update(block.subarray(0,Math.min(left,65536)));
const metadata={mediaKey:key.toString('base64'),type:'document',size,filehash:plainHash.digest('base64'),encFilehash:encHash.digest('base64')};
let bytes=0,maxChunk=0;const sha=createHash('sha256'),start=performance.now();
for await(const part of decrypt(encrypted(),metadata,size)){bytes+=part.length;maxChunk=Math.max(maxChunk,part.length);sha.update(part);}
if(bytes!==size || sha.digest('base64')!==metadata.filehash)throw new Error('benchmark_mismatch');
console.log(JSON.stringify({bytes,max_chunk_bytes:maxChunk,elapsed_ms:performance.now()-start,max_rss_kib:process.resourceUsage().maxRSS,provider:'synthetic'}));
