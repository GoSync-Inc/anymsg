import test from 'node:test';
import assert from 'node:assert/strict';
import {AnyMsgClient,tools} from '../lib/index.js';

test('local dismissal preserves exact target and never retries a changed hint',async()=>{
  const seen=[];
  const args={chat_id:'chat_synthetic',message_id:'msg_synthetic',expected_revision:3};
  const client=new AnyMsgClient({baseUrl:'https://archive.example',token:'synthetic',fetch:async(url,req)=>{
    seen.push([String(url),JSON.parse(req.body)]);
    return Response.json({error:'reply_target_changed',detail:'synthetic-sensitive'},{status:409});
  }});
  assert.ok(tools.includes('dismiss_reply'));
  await assert.rejects(client.dismissReply(args),{code:'reply_target_changed'});
  assert.deepEqual(seen,[['https://archive.example/v1/dismiss_reply',args]]);
});
