"""The destructive retention policy needs two independent explicit CLI choices."""
import json

import pytest


@pytest.mark.parametrize('options,policy,apply',[
    ([],False,False),(['--expire-unprocessed'],True,False),
    (['--apply'],False,True),(['--expire-unprocessed','--apply'],True,True),
])
def test_unprocessed_retention_cli_requires_explicit_policy_and_apply(tmp_path,monkeypatch,capsys,options,policy,apply):
    from messaging_service import file_lifecycle,store
    dsn=tmp_path/'synthetic.dsn';dsn.write_text('synthetic-dsn-not-a-connection')
    calls={}
    class FakeStore:
        def __init__(self,*args):pass
        def close(self):calls['closed']=True
    class FakeWorker:
        def __init__(self,value,*,expire_unprocessed):calls['policy']=expire_unprocessed
        def run_once(self,**kwargs):calls['run']=kwargs;return {'dry_run':not kwargs['apply']}
    monkeypatch.setattr(store,'Store',FakeStore)
    monkeypatch.setattr(file_lifecycle,'LifecycleWorker',FakeWorker)
    assert file_lifecycle.main(['--dsn-file',str(dsn),*options])==0
    assert calls=={'policy':policy,'run':{'apply':apply,'limit':100,'max_bytes':1024**3},'closed':True}
    assert json.loads(capsys.readouterr().out)['dry_run'] is not apply


def test_unprocessed_policy_cannot_be_combined_with_a_pin_operation(tmp_path,capsys):
    from messaging_service.file_lifecycle import main
    with pytest.raises(SystemExit) as error:
        main(['--dsn-file',str(tmp_path/'unused'),'--digest','a'*64,'--pin','--expire-unprocessed'])
    assert error.value.code==2
    assert 'pin is a separate one-shot operation' in capsys.readouterr().err
