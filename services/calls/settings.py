"""Private operator configuration. Secrets are never returned by status endpoints."""
import json
import os
from pathlib import Path
import tempfile

from pydantic import BaseModel,ConfigDict,Field,SecretStr
from outbound import CallingSettings


class TwilioSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    account_sid:str=Field(default='',max_length=34)
    auth_token:SecretStr=Field(default=SecretStr(''),max_length=512)
    phone_from:str=Field(default='',max_length=30)
    whatsapp_from:str=Field(default='',max_length=30)
    sip_from:str=Field(default='assistant',max_length=80)
    sip_auth_username:str=Field(default='',max_length=80)
    sip_auth_password:SecretStr=Field(default=SecretStr(''),max_length=512)
    public_url:str=Field(default='',max_length=300)

    def private(self):
        return {**self.model_dump(exclude={'auth_token','sip_auth_password'}),'auth_token':self.auth_token.get_secret_value(),
                'sip_auth_password':self.sip_auth_password.get_secret_value()}


class Setup(CallingSettings):
    twilio:TwilioSettings=Field(default_factory=TwilioSettings)


def read_private(path):
    path=Path(path)
    if not path.exists():return {}
    if path.is_symlink() or path.stat().st_mode&0o077 or path.stat().st_size>65536:
        raise ValueError('private_settings_required')
    return json.loads(path.read_text())


def write_private(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as output:
            json.dump(value,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
