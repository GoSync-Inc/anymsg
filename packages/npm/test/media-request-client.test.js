import test from 'node:test';
import assert from 'node:assert/strict';
import {AnyMsgClient, tools} from '../lib/index.js';

test('explicit original request preserves reference and never retries uncertain response', async()=>{
  const seen=[];
  const client=new AnyMsgClient({baseUrl:'https://archive.example', token:'synthetic',fetch:async(url,request)=>{
    seen.push([String(url),JSON.parse(request.body)]);
    throw new Error('synthetic private diagnostic');
  }});
  assert.ok(tools.includes('request_file_download'));
  await assert.rejects(client.requestFileDownload({attachment_id:'file_synthetic'}));
  assert.deepEqual(seen,[['https://archive.example/v1/request_file_download',{attachment_id:'file_synthetic'}]]);
});

test('invalid original retrieval arguments are rejected before fetch',async()=>{
  const client=new AnyMsgClient({baseUrl:'https://archive.example', token:'synthetic',fetch:async()=>{
    assert.fail('invalid request must not leave the client');
  }});
  for (const args of [{},{attachment_id:''},{attachment_id:'x'.repeat(257)},
    {attachment_id:7},{attachment_id:true},{attachment_id:null},
    {attachment_id:'file_synthetic',request_id:'unaccepted'}]) {
    await assert.rejects(client.call('request_file_download',args),TypeError);
  }
});

test('bounded-demand limit is preserved safely with no automatic retry',async()=>{
  let count=0;
  const client=new AnyMsgClient({baseUrl:'https://archive.example',token:'synthetic',fetch:async()=>{
    count++;
    return Response.json({error:'media_request_limit',detail:'synthetic-private'},{status:429});
  }});
  await assert.rejects(client.requestFileDownload({attachment_id:'file_synthetic'}),error=>{
    assert.equal(error.code,'media_request_limit');
    assert.equal(String(error).includes('private'),false);
    return true;
  });
  assert.equal(count,1);
});
