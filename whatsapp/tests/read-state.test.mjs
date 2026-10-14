import test from 'node:test';
import assert from 'node:assert/strict';
import {cachedDialogs} from '../read-state.mjs';

test('catalog read snapshots use cached collection only and never merge phone and LID',()=>{
  const chats=['42@c.us','42:3@c.us','99@lid','42@lid','100@g.us','200@c.us','status@broadcast'].map(id=>
    ({id:{_serialized:id,server:id.split('@')[1]},name:'Synthetic',unreadCount:2}));
  const previous=global.window;
  global.window={require(name){assert.equal(name,'WAWebCollections');return {Chat:{getModelsArray:()=>chats}};}};
  try {
    const items=cachedDialogs({limit:100,ownerIds:['42@c.us','99@lid']});
    assert.equal(items.length,6);
    assert.deepEqual(items.map(x=>x[4].is_self_chat),[true,true,true,false,false,false]);
    assert.equal(items[0][4].unread_count,2);
    assert.equal(items[4][3],'group');
    assert.equal(cachedDialogs({limit:100,ownerIds:['42@c.us']})[2][4].is_self_chat,null);
  } finally {global.window=previous;}
});
