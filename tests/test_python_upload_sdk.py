"""Synthetic staged uploads: local fixtures and MockTransport, no provider calls."""
import base64
import hashlib
import json
import os

import httpx
import pytest

from anymsg import AnyMsgError, Client


UPLOAD_ID='up_'+'a'*32
CHUNK=262144


def metadata(content=b'fixture', **changes):
    return dict(account='fixture',chat_id='chat_fixture',filename='fixture.bin',mime='application/octet-stream',
        bytes=len(content),sha256=hashlib.sha256(content).hexdigest(),request_id='fixture-upload-001',**changes)


def receipt(data, offset=0, **changes):
    return {**{key:value for key,value in data.items() if key!='request_id'},'upload_id':UPLOAD_ID,
        'offset':offset,'status':'ready' if offset==data['bytes'] else 'uploading',
        'chunk_bytes':CHUNK,'expires_at':'2099-01-01T00:00:00Z',
        'changes_provider_state':False,'owner_approval_verified_by_server':False,**changes}


def client(handler):
    return Client('https://archive.example','synthetic-token',transport=httpx.MockTransport(handler))


def test_low_level_upload_tools_keep_exact_payload_and_chunk_only_large_body():
    seen=[]
    data=metadata()
    encoded=base64.b64encode(b'a'*CHUNK).decode()
    def handler(request):
        seen.append((request.url.path,json.loads(request.content),len(request.content)))
        return httpx.Response(200,json={'synthetic':True})
    with client(handler) as sdk:
        sdk.prepare_file_upload(**data)
        sdk.upload_file_chunk(upload_id=UPLOAD_ID,offset=0,data_base64=encoded)
        sdk.get_file_upload(upload_id=UPLOAD_ID)
        with pytest.raises(ValueError,match='request_too_large'):sdk.search('x'*16384)
    assert [entry[0] for entry in seen]==['/v1/prepare_file_upload','/v1/upload_file_chunk','/v1/get_file_upload']
    assert seen[0][1]==data and seen[2][1]=={'upload_id':UPLOAD_ID}
    assert 16384<seen[1][2]<=360448
    assert base64.b64decode(seen[1][1]['data_base64'])==b'a'*CHUNK


@pytest.mark.parametrize('method,params',[
    ('prepare_file_upload',{**metadata(),'filename':'../fixture'}),
    ('prepare_file_upload',{**metadata(),'filename':'bad\ud800'}),
    ('prepare_file_upload',{**metadata(),'mime':'image/jpeg; charset=utf-8'}),
    ('prepare_file_upload',{**metadata(),'bytes':True}),
    ('prepare_file_upload',{**metadata(),'bytes':32*1024**2+1}),
    ('prepare_file_upload',{**metadata(),'url':'https://source.example/file'}),
    ('prepare_file_upload',{**metadata(),'request_id':'short'}),
    ('upload_file_chunk',{'upload_id':UPLOAD_ID,'offset':True,'data_base64':'YQ=='}),
    ('upload_file_chunk',{'upload_id':UPLOAD_ID,'offset':0,'data_base64':'YQ'}),
    ('upload_file_chunk',{'upload_id':UPLOAD_ID,'offset':0,'data_base64':'YR=='}),
    ('upload_file_chunk',{'upload_id':UPLOAD_ID,'offset':0,'data_base64':'YQ==\n'}),
    ('upload_file_chunk',{'upload_id':UPLOAD_ID,'offset':0,'data_base64':base64.b64encode(b'a'*(CHUNK+1)).decode()}),
    ('get_file_upload',{'upload_id':UPLOAD_ID,'path':'/server/private'}),
])
def test_bad_upload_envelopes_never_reach_transport(method,params):
    with client(lambda _:pytest.fail('must not send malformed request')) as sdk:
        with pytest.raises(ValueError,match='invalid_upload_parameters'):sdk.call(method,params)


@pytest.mark.parametrize('code',['invalid_upload','invalid_upload_chunk','upload_limit','upload_capacity',
    'upload_expired','upload_offset_conflict','upload_hash_mismatch'])
def test_safe_upload_error_is_reported_once(code):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(409,json={'error':code,'details':'private fixture'})
    with client(handler) as sdk:
        with pytest.raises(AnyMsgError) as error:sdk.get_file_upload(upload_id=UPLOAD_ID)
    assert error.value.code==code and len(seen)==1 and 'private' not in str(error.value)


def test_helper_stages_exact_streamed_bytes_and_never_previews_or_sends(tmp_path):
    content=bytes(range(256))*(CHUNK//256)+b'last fixture chunk'
    path=tmp_path/'private-source.jpg';path.write_bytes(content)
    seen=[];staged=bytearray();data=None
    def handler(request):
        nonlocal data
        payload=json.loads(request.content);seen.append((request.url.path,payload))
        if request.url.path.endswith('/prepare_file_upload'):
            data=payload
            return httpx.Response(200,json=receipt(data))
        assert request.url.path.endswith('/upload_file_chunk')
        assert payload['offset']==len(staged)
        staged.extend(base64.b64decode(payload['data_base64']))
        return httpx.Response(200,json=receipt(data,len(staged)))
    with client(handler) as sdk:
        result=sdk.upload_file(path,account='fixture',chat_id='chat_fixture',request_id='fixture-upload-001',
            mime='IMAGE/JPEG',filename='photo.jpg')
    assert result['status']=='ready' and staged==content and len(seen)==3
    assert data=={**metadata(content),'filename':'photo.jpg','mime':'image/jpeg'}
    assert str(tmp_path) not in json.dumps(seen)
    assert all('send' not in operation and 'preview' not in operation for operation,_ in seen)


@pytest.mark.parametrize('existing_offset',[2,7])
def test_explicit_same_id_resume_uses_existing_upload(tmp_path,existing_offset):
    content=b'fixture';path=tmp_path/'fixture.bin';path.write_bytes(content)
    data=metadata(content);seen=[]
    def handler(request):
        payload=json.loads(request.content);seen.append(payload)
        if request.url.path.endswith('/prepare_file_upload'):
            assert payload==data
            return httpx.Response(200,json=receipt(data,existing_offset))
        assert payload['offset']==existing_offset
        assert base64.b64decode(payload['data_base64'])==content[existing_offset:]
        return httpx.Response(200,json=receipt(data,len(content)))
    with client(handler) as sdk:
        assert sdk.upload_file(path,account='fixture',chat_id='chat_fixture',request_id=data['request_id'])['status']=='ready'
    assert len(seen)==(1 if existing_offset==len(content) else 2)


@pytest.mark.parametrize('moment',['prepare','chunk'])
def test_source_change_rejected_even_when_original_mtime_is_restored(tmp_path,moment):
    path=tmp_path/'fixture.bin';path.write_bytes(b'fixture')
    original=path.stat();seen=[];data=None
    def handler(request):
        nonlocal data
        payload=json.loads(request.content);seen.append(request.url.path)
        if request.url.path.endswith('/prepare_file_upload'):data=payload
        if request.url.path.endswith('/'+('prepare_file_upload' if moment=='prepare' else 'upload_file_chunk')):
            path.write_bytes(b'changed')
            os.utime(path,ns=(original.st_atime_ns,original.st_mtime_ns))
        return httpx.Response(200,json=receipt(data,0 if len(seen)==1 else data['bytes']))
    with client(handler) as sdk:
        with pytest.raises(AnyMsgError,match='upload_file_changed'):
            sdk.upload_file(path,account='fixture',chat_id='chat_fixture',request_id='fixture-upload-001')
    assert len(seen)==(1 if moment=='prepare' else 2)


@pytest.mark.parametrize('failed_stage',['prepare_file_upload','upload_file_chunk'])
def test_helper_stops_after_uncertain_result_without_retry_or_send(tmp_path,failed_stage):
    path=tmp_path/'fixture.bin';path.write_bytes(b'fixture');seen=[]
    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith('/'+failed_stage):raise httpx.ReadTimeout('private filename')
        return httpx.Response(200,json=receipt(json.loads(request.content)))
    with client(handler) as sdk:
        with pytest.raises(AnyMsgError,match='request_failed'):
            sdk.upload_file(path,account='fixture',chat_id='chat_fixture',request_id='fixture-upload-001')
    assert seen==['/v1/prepare_file_upload']+(['/v1/upload_file_chunk'] if failed_stage=='upload_file_chunk' else [])


@pytest.mark.parametrize('change',[{'offset':100},{'chunk_bytes':CHUNK+1},{'chunk_bytes':1},{'status':'ready'},
    {'sha256':'b'*64},{'account':'different'},{'status':'expired'},
    {'changes_provider_state':True},{'owner_approval_verified_by_server':True}])
def test_helper_rejects_misleading_receipt_before_transfer(tmp_path,change):
    path=tmp_path/'fixture.bin';path.write_bytes(b'fixture');seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json=receipt(json.loads(request.content),**change))
    with client(handler) as sdk:
        with pytest.raises(AnyMsgError,match='invalid_upload_response|upload_expired'):
            sdk.upload_file(path,account='fixture',chat_id='chat_fixture',request_id='fixture-upload-001')
    assert len(seen)==1


def test_helper_rejects_symlink_empty_oversized_url_and_missing_without_path_leak(tmp_path):
    target=tmp_path/'fixture.bin';target.write_bytes(b'fixture')
    link=tmp_path/'link';link.symlink_to(target)
    empty=tmp_path/'empty';empty.touch()
    huge=tmp_path/'huge';huge.touch()
    with huge.open('wb') as source:source.truncate(32*1024**2+1)
    with client(lambda _:pytest.fail('invalid file must not make network requests')) as sdk:
        for file in (link,empty,huge,tmp_path,tmp_path/'private-missing','https://source.example/file'):
            with pytest.raises((AnyMsgError,ValueError)) as error:
                sdk.upload_file(file,account='fixture',chat_id='chat_fixture',request_id='fixture-upload-001')
            assert str(tmp_path) not in str(error.value)
