import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AnyMsgClient, AnyMsgError, tools} from '../lib/index.js';

const options = {baseUrl:'https://archive.example/base', token:'synthetic-token'};
const errors = ['invalid_message_format','unsafe_message_markup','invalid_message_entities',
  'formatting_too_complex','unsupported_message_formatting','lossy_formatting_requires_confirmation',
  'message_too_long','preview_required','preview_mismatch'];

test('preview and send preserve explicit formatting, digest and policy without another call', async()=>{
  const captured=[];
  const client=new AnyMsgClient({...options,fetch:async(url,request)=>{
    captured.push([String(url),JSON.parse(request.body)]);
    return Response.json({preview_digest:'a'.repeat(64)});
  }});
  const draft={account:'synthetic',chat_id:'synthetic-chat',text:'**Текст 😀**',
    format:'markdown',reply_to:'synthetic-message',link_preview:true,allow_lossy:false};
  assert.equal((await client.previewMessage(draft)).preview_digest,'a'.repeat(64));
  const action={...draft,request_id:'synthetic-action',preview_digest:'a'.repeat(64)};
  await client.sendMessage(action);
  assert.deepEqual(captured,[['https://archive.example/base/v1/preview_message',draft],
    ['https://archive.example/base/v1/send_message',action]]);
  assert.ok(tools.includes('preview_message'));
});

test('plain preview does not insert hidden formatting or queue an action',async()=>{
  const captured=[];
  const client=new AnyMsgClient({...options,fetch:async(url,request)=>{
    captured.push([String(url),JSON.parse(request.body)]);return Response.json({});
  }});
  const draft={account:'synthetic',chat_id:'synthetic-chat',text:'Literal 5*3'};
  await client.previewMessage(draft);
  assert.deepEqual(captured,[['https://archive.example/base/v1/preview_message',draft]]);
});

for(const code of errors)test(`safe formatting error ${code}, no retry`,async()=>{
  let calls=0;
  const client=new AnyMsgClient({...options,fetch:async()=>{
    calls++;return Response.json({error:code,request_id:'b'.repeat(32),
      detail:'synthetic-private-draft'},{status:400});
  }});
  await assert.rejects(client.previewMessage({account:'synthetic',chat_id:'synthetic-chat',text:'draft'}),
    error=>error instanceof AnyMsgError && error.code===code && error.requestId==='b'.repeat(32)
      && !JSON.stringify(error).includes('synthetic-private-draft'));
  assert.equal(calls,1);
});
