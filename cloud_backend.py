"""Supabase-backed shared documents for 108 STUDIO. Server-only privileged access."""
import os, json, urllib.request, urllib.parse, urllib.error, uuid, datetime

URL = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
KEY = os.getenv('SUPABASE_SECRET_KEY', '').strip()

def ready():
    return URL.startswith('https://') and bool(KEY)

def request(table, method='GET', query='', data=None, prefer='return=representation'):
    if not ready(): raise ValueError('Supabaseの環境変数が未設定です')
    if table not in ('studio_documents','studio_revisions','studio_edit_locks'): raise ValueError('invalid table')
    endpoint = URL + '/rest/v1/' + table + ('?' + query if query else '')
    headers = {'apikey': KEY, 'Authorization':'Bearer '+KEY, 'Accept':'application/json', 'Prefer':prefer}
    if data is not None: headers['Content-Type']='application/json'
    req = urllib.request.Request(endpoint, data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw=resp.read(); return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        raise ValueError('データベースエラー ('+str(e.code)+'): '+e.read(400).decode(errors='replace')) from e

def q(s): return urllib.parse.quote(str(s),safe='')
def documents():
    return request('studio_documents',query='select=id,client_name,title,version,updated_at,updated_by&order=updated_at.desc&limit=100')
def document(doc_id):
    rows=request('studio_documents',query='id=eq.'+q(doc_id)+'&select=*')
    return rows[0] if rows else None

def create(payload,email):
    content=payload.get('content',{})
    if not isinstance(content,dict): raise ValueError('資料の形式が不正です')
    if len(json.dumps(content,ensure_ascii=False))>4000000: raise ValueError('資料が大きすぎます（4MB以下）')
    rows=request('studio_documents','POST',data={'client_name':str(payload.get('client_name',''))[:150], 'title':str(payload.get('title','新しい企画資料'))[:200], 'content':content,'created_by':email,'updated_by':email})
    return rows[0]

def update(doc_id,payload,email):
    old_version=payload.get('version')
    if type(old_version) is not int or old_version<1: raise ValueError('version is required')
    content=payload.get('content')
    if not isinstance(content,dict): raise ValueError('資料の形式が不正です')
    if len(json.dumps(content,ensure_ascii=False))>4000000: raise ValueError('資料が大きすぎます（4MB以下）')
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows=request('studio_documents','PATCH','id=eq.'+q(doc_id)+'&version=eq.'+str(old_version),{'content':content,'version':old_version+1,'title':str(payload.get('title','新しい企画資料'))[:200],'client_name':str(payload.get('client_name',''))[:150],'updated_by':email,'updated_at':stamp})
    if not rows: return None
    # A revision is a best-effort snapshot; never roll back a successful document update.
    try: request('studio_revisions','POST',data={'document_id':doc_id,'version':old_version+1,'edited_by':email,'content':content})
    except Exception as e: print('revision warning:',e,flush=True)
    return rows[0]

def revisions(doc_id):
    return request('studio_revisions',query='document_id=eq.'+q(doc_id)+'&select=id,version,edited_by,created_at&order=version.desc&limit=30')
