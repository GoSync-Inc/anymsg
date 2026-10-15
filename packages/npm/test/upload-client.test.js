import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync,writeFileSync,readFileSync,rmSync,symlinkSync,statSync,utimesSync,truncateSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {AnyMsgClient,AnyMsgError,tools} from '../lib/index.js';

const ID='up_'+'a'.repeat(32);
const CHUNK=262144;
const base={baseUrl:'https://archive.example/base',token:'synthetic-token'};
const sha=content=>createHash('sha256').update(content).digest('hex');
const metadata=(content=Buffer.from('fixture'))=>({account:'fixture',chat_id:'chat_fixture',filename:'fixture.bin',
  mime:'application/octet-stream',bytes:content.length,sha256:sha(content),request_id:'fixture-upload-001'});
function receipt(data,offset=0,changes={}) {
  const {request_id,...fields}=data;
  return {...fields,upload_id:ID,offset,status:offset===data.bytes?'ready':'uploading',chunk_bytes:CHUNK,
    expires_at:'2099-01-01T00:00:00Z',changes_provider_state:false,owner_approval_verified_by_server:false,...changes};
}
function fixture(t,content=Buffer.from('fixture')) {
  const directory=mkdtempSync(join(tmpdir(),'anymsg-upload-'));
  t.after(()=>rmSync(directory,{recursive:true,force:true}));
  const file=join(directory,'fixture.bin');writeFileSync(file,content);
  return {directory,file};
}
const selection={account:'fixture',chat_id:'chat_fixture',request_id:'fixture-upload-001'};

test('upload low-level methods preserve exact bytes with a chunk-only large JSON limit',async()=>{
  const seen=[];
  const client=new AnyMsgClient({...base,fetch:async(url,request)=>{
    seen.push({url:String(url),data:JSON.parse(request.body),size:Buffer.byteLength(request.body)});
    return Response.json({fixture:true});
  }});
  const data=metadata();const encoded=Buffer.alloc(CHUNK,42).toString('base64');
  await client.prepareFileUpload(data);
  await client.uploadFileChunk({upload_id:ID,offset:0,data_base64:encoded});
  await client.getFileUpload({upload_id:ID});
  await assert.rejects(client.search({query:'x'.repeat(16384)}),/16384/);
  assert.deepEqual(seen.map(item=>item.url.split('/').at(-1)),['prepare_file_upload','upload_file_chunk','get_file_upload']);
  assert.deepEqual(seen[0].data,data);
  assert.equal(seen[1].data.data_base64,encoded);
  assert.ok(seen[1].size>16384&&seen[1].size<=360448);
  for(const name of ['prepare_file_upload','upload_file_chunk','get_file_upload'])assert.ok(tools.includes(name));
});

const invalid=[
  ['prepare_file_upload',{...metadata(),filename:'../fixture'}],
  ['prepare_file_upload',{...metadata(),filename:'bad\ud800'}],
  ['prepare_file_upload',{...metadata(),mime:'image/jpeg; charset=utf-8'}],
  ['prepare_file_upload',{...metadata(),bytes:true}],
  ['prepare_file_upload',{...metadata(),bytes:32*1024*1024+1}],
  ['prepare_file_upload',{...metadata(),url:'https://source.example/file'}],
  ['prepare_file_upload',{...metadata(),request_id:'short'}],
  ['upload_file_chunk',{upload_id:ID,offset:true,data_base64:'YQ=='}],
  ['upload_file_chunk',{upload_id:ID,offset:0,data_base64:'YQ'}],
  ['upload_file_chunk',{upload_id:ID,offset:0,data_base64:'YR=='}],
  ['upload_file_chunk',{upload_id:ID,offset:0,data_base64:'YQ==\n'}],
  ['upload_file_chunk',{upload_id:ID,offset:0,data_base64:Buffer.alloc(CHUNK+1).toString('base64')}],
  ['get_file_upload',{upload_id:ID,path:'/server/private'}],
  ['get_file_upload',{upload_id:ID,get ignored(){throw new Error('private getter');}}],
];
for(const [i,[method,args]] of invalid.entries())test(`malformed upload rejected before fetch ${i}`,async()=>{
  let calls=0;
  const client=new AnyMsgClient({...base,fetch:()=>{calls++;return Response.json({});}});
  await assert.rejects(client.call(method,args),TypeError);
  assert.equal(calls,0);
});

for(const code of ['invalid_upload','invalid_upload_chunk','upload_limit','upload_capacity',
  'upload_expired','upload_offset_conflict','upload_hash_mismatch'])
  test(`safe upload error ${code} is not retried`,async()=>{
    let calls=0;
    const client=new AnyMsgClient({...base,fetch:()=>{calls++;
      return Response.json({error:code,detail:'private fixture'},{status:409});}});
    await assert.rejects(client.getFileUpload({upload_id:ID}),error=>error instanceof AnyMsgError&&
      error.code===code&&!String(error).includes('private'));
    assert.equal(calls,1);
  });

test('local helper hashes and stages streamed exact bytes without any send',async t=>{
  const content=Buffer.concat([Buffer.alloc(CHUNK,43),Buffer.from('final fixture')]);
  const {file,directory}=fixture(t,content);
  const seen=[];const chunks=[];let data;let offset=0;
  const client=new AnyMsgClient({...base,fetch:(url,request)=>{
    const payload=JSON.parse(request.body);seen.push({url:String(url),payload,at:performance.now()});
    if(String(url).endsWith('/prepare_file_upload')){data=payload;return Response.json(receipt(data));}
    assert.ok(String(url).endsWith('/upload_file_chunk'));
    assert.equal(payload.offset,offset);
    const bytes=Buffer.from(payload.data_base64,'base64');chunks.push(bytes);offset+=bytes.length;
    return Response.json(receipt(data,offset));
  }});
  const result=await client.uploadFile(file,{...selection,filename:'photo.jpg',mime:'IMAGE/JPEG'});
  assert.equal(result.status,'ready');
  assert.deepEqual(Buffer.concat(chunks),content);
  assert.deepEqual(data,{...metadata(content),filename:'photo.jpg',mime:'image/jpeg'});
  assert.equal(seen.length,3);
  assert.ok(seen[2].at-seen[1].at>=120,'sequential chunks must be paced');
  assert.ok(!JSON.stringify(seen).includes(directory));
});

for(const existingOffset of [2,7])test(`explicit same request ID resumes offset ${existingOffset}`,async t=>{
  const {file}=fixture(t);const content=readFileSync(file);const data=metadata(content);let calls=0;
  const client=new AnyMsgClient({...base,fetch:(url,request)=>{
    calls++;const payload=JSON.parse(request.body);
    if(String(url).endsWith('/prepare_file_upload')){
      assert.deepEqual(payload,data);return Response.json(receipt(data,existingOffset));
    }
    assert.equal(payload.offset,existingOffset);
    assert.deepEqual(Buffer.from(payload.data_base64,'base64'),content.subarray(existingOffset));
    return Response.json(receipt(data,content.length));
  }});
  assert.equal((await client.uploadFile(file,selection)).status,'ready');
  assert.equal(calls,existingOffset===content.length?1:2);
});

for(const moment of ['prepare','chunk'])test(`local modification during ${moment} refuses success`,async t=>{
  const {file}=fixture(t);const original=statSync(file);let data;let calls=0;
  const client=new AnyMsgClient({...base,fetch:(url,request)=>{
    calls++;const preparation=String(url).endsWith('/prepare_file_upload');
    if(preparation)data=JSON.parse(request.body);
    if(preparation===(moment==='prepare')){
      writeFileSync(file,'changed');utimesSync(file,original.atime,original.mtime);
    }
    return Response.json(receipt(data,preparation?0:data.bytes));
  }});
  await assert.rejects(client.uploadFile(file,selection),{code:'upload_file_changed'});
  assert.equal(calls,moment==='prepare'?1:2);
});

for(const stage of ['prepare_file_upload','upload_file_chunk'])
  test(`uncertain ${stage} stops without retry or new ID`,async t=>{
    const {file}=fixture(t);const seen=[];
    const client=new AnyMsgClient({...base,fetch:(url,request)=>{
      seen.push(String(url).split('/').at(-1));
      if(String(url).endsWith('/'+stage))throw new Error('private filename');
      return Response.json(receipt(JSON.parse(request.body)));
    }});
    await assert.rejects(client.uploadFile(file,selection),{code:'transport_error'});
    assert.deepEqual(seen,['prepare_file_upload',...(stage==='upload_file_chunk'?['upload_file_chunk']:[])]);
  });

for(const [i,change] of [{offset:100},{chunk_bytes:CHUNK+1},{chunk_bytes:1},{status:'ready'},
  {sha256:'b'.repeat(64)},{account:'different'},{status:'expired'},
  {changes_provider_state:true},{owner_approval_verified_by_server:true}].entries())
  test(`misleading upload receipt rejects before chunk ${i}`,async t=>{
    const {file}=fixture(t);let calls=0;
    const client=new AnyMsgClient({...base,fetch:(url,request)=>{calls++;
      return Response.json(receipt(JSON.parse(request.body),0,change));}});
    await assert.rejects(client.uploadFile(file,selection),{code:change.status==='expired'?'upload_expired':'invalid_upload_response'});
    assert.equal(calls,1);
  });

test('invalid local files and aborted uploads never reach fetch or expose paths',async t=>{
  const {file,directory}=fixture(t);
  const link=join(directory,'link');symlinkSync(file,link);
  const empty=join(directory,'empty');writeFileSync(empty,'');
  const huge=join(directory,'huge');writeFileSync(huge,'');truncateSync(huge,32*1024*1024+1);
  let calls=0;
  const client=new AnyMsgClient({...base,fetch:()=>{calls++;return Response.json({});}});
  for(const source of [link,empty,huge,directory,join(directory,'private-missing'),'https://source.example/file'])
    await assert.rejects(client.uploadFile(source,selection),error=>!String(error).includes(directory));
  await assert.rejects(client.uploadFile(file,{...selection,signal:AbortSignal.abort()}),{code:'request_aborted'});
  assert.equal(calls,0);
});

test('upload TypeScript declarations include exact status and separate local helper',()=>{
  const declared=readFileSync(new URL('../lib/index.d.ts',import.meta.url),'utf8');
  for(const name of ['FileUploadPreparation','FileUploadChunk','FileUploadStatus','LocalFileUploadOptions'])
    assert.match(declared,new RegExp(`export interface ${name} \\{`));
  assert.match(declared,/status: 'uploading' \| 'ready' \| 'expired'/);
  assert.match(declared,/uploadFile\(file: string, options: LocalFileUploadOptions\): Promise<FileUploadStatus>/);
});

function runCli(args) {
  return new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,[new URL('../bin/anymsg.js',import.meta.url).pathname,...args],
      {stdio:['ignore','pipe','pipe']});
    let stdout='',stderr='';
    child.stdout.on('data',part=>stdout+=part);child.stderr.on('data',part=>stderr+=part);
    const timer=setTimeout(()=>{child.kill('SIGTERM');reject(new Error('synthetic CLI deadline'));},10000);
    child.once('error',error=>{clearTimeout(timer);reject(error);});
    child.once('close',code=>{clearTimeout(timer);resolve({code,stdout,stderr});});
  });
}

test('CLI upload_file consumes local --file and stages on synthetic loopback only',async t=>{
  const {file,directory}=fixture(t);
  const token=join(directory,'private.token');writeFileSync(token,'synthetic-token',{mode:0o600});
  const seen=[];let data;
  const server=createServer((request,response)=>{
    let body='';request.on('data',part=>body+=part);request.on('end',()=>{
      const payload=JSON.parse(body);seen.push({url:request.url,payload});
      response.setHeader('content-type','application/json');
      if(request.url==='/v1/prepare_file_upload'){data=payload;response.end(JSON.stringify(receipt(data)));}
      else response.end(JSON.stringify(receipt(data,data.bytes)));
    });
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const args=['--url',`http://127.0.0.1:${server.address().port}`,'--token-file',token,'--json',JSON.stringify(selection)];
    const result=await runCli(['upload_file','--file',file,...args]);
    assert.equal(result.code,0);assert.equal(result.stderr,'');assert.equal(JSON.parse(result.stdout).status,'ready');
    assert.deepEqual(seen.map(item=>item.url),['/v1/prepare_file_upload','/v1/upload_file_chunk']);
    assert.equal(seen[0].payload.filename,'fixture.bin');assert.ok(!JSON.stringify(seen).includes(directory));
    const invalid=await runCli(['search','--file',file,...args]);
    assert.equal(invalid.code,1);assert.equal(JSON.parse(invalid.stderr).error,'invalid_arguments');
    assert.equal(seen.length,2);
  } finally { await new Promise(resolve=>server.close(resolve)); }
});
