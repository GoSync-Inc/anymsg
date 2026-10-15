"""Account-local implementation identity, written only by the fenced collector.

Old/unobserved installations remain webjs. Provider changes invalidate operation
previews through adapter binding; no caller can select a more permissive adapter.
"""

def account_provider(c,account,platform):
    if platform!='whatsapp':return None
    row=c.execute('SELECT operations FROM collector_health WHERE account=%s',(account,)).fetchone()
    observation=(row['operations'] if row else {}).get('provider',{})
    return ('whatsmeow' if observation.get('name')=='whatsmeow' and
            observation.get('contract_version')==1 else 'webjs')
