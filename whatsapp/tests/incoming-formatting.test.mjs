import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {event} from '../capture.mjs';

function message(body='*hello*') {
  return {id:{_serialized:'false_100@c.us_17',remote:'100@c.us'},from:'100@c.us',
    fromMe:false,body,timestamp:1790856000,hasMedia:false,
    _data:{token:'PRIVATE_SENTINEL',formatting:[{type:'bold',secret:'DO_NOT_COPY'}]},
    getContact(){throw new Error('network forbidden');},getQuotedMessage(){throw new Error('network forbidden');}};
}
test('public cached native wire is preserved without claiming parsed entities',()=>{
  const raw=message('*hello* 5*3 _world_');const value=event(raw);
  assert.equal(value.body,raw.body);
  assert.deepEqual(value.formatting,{version:1,state:'wire_observed',basis:'whatsapp_native_wire',
    wire_sha256:createHash('sha256').update(raw.body).digest('hex'),entities:null,
    unsupported_entities:[],warnings:['syntax_interpretation_unknown']});
  assert.ok(!JSON.stringify(value).includes('PRIVATE_SENTINEL'));
  assert.ok(!JSON.stringify(value).includes('DO_NOT_COPY'));
});
test('edit callback body binds exact replacement wire, including empty body',()=>{
  for(const body of ['_replacement_', '', '😀'.repeat(32768)]) {
    const value=event(message(),body);
    assert.equal(value.body,body);
    assert.equal(value.formatting.wire_sha256,createHash('sha256').update(body).digest('hex'));
    assert.equal(value.formatting.entities,null);
  }
});
test('missing public body remains unknown instead of assuming an observed empty entity list',()=>{
  const raw=message();delete raw.body;
  const value=event(raw);
  assert.equal(value.body,'');
  assert.equal(value.formatting.state,'unknown');
  assert.equal(value.formatting.entities,null);
  assert.equal(value.formatting.wire_sha256,null);
});
