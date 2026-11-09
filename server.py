"""108 STUDIO prototype: Google ID token login with email allowlist."""
import os, json, time, hmac, hashlib, base64, http.cookies, urllib.request, urllib.parse, io, re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.getenv('PORT', '8765'))
CLIENT_ID = os.getenv('STUDIO_GOOGLE_CLIENT_ID', '').strip()
SECRET = os.getenv('STUDIO_SESSION_SECRET', '')
ALLOWED = {e.strip().lower() for e in os.getenv('STUDIO_ALLOWED_EMAILS', '').split(',') if e.strip()}

# Shared documents require a persistent PostgreSQL database (Render DATABASE_URL).
import threading
_DB_LOCK = threading.Lock()
def shared_db():
    url=os.getenv('DATABASE_URL','').strip()
    if not url: raise RuntimeError('共同編集にはRenderのDATABASE_URL（PostgreSQL）が必要です')
    import psycopg
    return psycopg.connect(url,connect_timeout=8,options='-c statement_timeout=10000')

# Preview-only ephemeral presence; never touches shared document data.
_PRESENCE = {}
_PRESENCE_LOCK = threading.Lock()
def preview_presence(doc_id, client_id=None, name=None, page=None):
    now=time.time()
    with _PRESENCE_LOCK:
        for key, entry in list(_PRESENCE.items()):
            if now-entry['seen']>35: del _PRESENCE[key]
        if client_id and name:
            _PRESENCE[(doc_id,client_id)]={'name':name[:32],'page':str(page)[:24], 'seen':now}
        return [{'name':v['name'],'page':v['page'],'clientId':k[1]} for k,v in _PRESENCE.items() if k[0]==doc_id]

def shared_init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS studio_shared_docs (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, data JSONB NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1, updated_by TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")

    conn.execute("""CREATE TABLE IF NOT EXISTS studio_shared_versions (
        doc_id TEXT NOT NULL, revision INTEGER NOT NULL, data JSONB NOT NULL,
        updated_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(doc_id,revision))""")

DEFAULT_CLIENTS=['CUTIE STREET','22/7','Cloud ten','Ettone','僕が見たかった青空']
def clients_init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS studio_clients (
        name TEXT PRIMARY KEY, color TEXT NOT NULL DEFAULT '#243f71',
        channel TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    conn.execute("ALTER TABLE studio_clients ADD COLUMN IF NOT EXISTS border_color TEXT NOT NULL DEFAULT '#000000'")
    conn.execute("ALTER TABLE studio_clients ADD COLUMN IF NOT EXISTS text_mode TEXT NOT NULL DEFAULT 'auto'")
    for name in DEFAULT_CLIENTS:
        conn.execute("INSERT INTO studio_clients(name) VALUES (%s) ON CONFLICT DO NOTHING",(name,))
    conn.commit()

def clients_list():
    with shared_db() as conn:
        clients_init(conn)
        rows=conn.execute("SELECT name,color,channel,border_color,text_mode FROM studio_clients ORDER BY name").fetchall()
        return [{'name':r[0],'color':r[1],'channel':r[2],'borderColor':r[3],'textMode':r[4]} for r in rows]

def clients_save(body):
    name=body.get('name','')
    color=body.get('color','#243f71')
    channel=body.get('channel','')
    border_color=body.get('borderColor','#000000')
    text_mode=body.get('textMode','auto')
    if (not isinstance(name,str) or not 1<=len(name.strip())<=100
        or not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color)
        or not isinstance(border_color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',border_color)
        or text_mode not in ('auto','white','black')
        or not isinstance(channel,str) or len(channel)>120):
        raise ValueError('クライアント名・カラー・文字色・Slackチャンネルを確認してください')
    with shared_db() as conn:
        clients_init(conn)
        conn.execute("""INSERT INTO studio_clients(name,color,channel,border_color,text_mode) VALUES (%s,%s,%s,%s,%s)
          ON CONFLICT(name) DO UPDATE SET color=EXCLUDED.color,channel=EXCLUDED.channel,
          border_color=EXCLUDED.border_color,text_mode=EXCLUDED.text_mode,updated_at=NOW()""",
          (name.strip(),color,channel.strip(),border_color,text_mode))
    return {'ok':True}

APPROVER_EMAIL = 'r.nakajima@108-inc.net'

_WORKFLOW_READY = False
_WORKFLOW_LOCK = threading.Lock()
def workflow_init(conn):
    global _WORKFLOW_READY
    if _WORKFLOW_READY:return
    with _WORKFLOW_LOCK:
        if _WORKFLOW_READY:return
        shared_init(conn)
        for col, typ in [('owner_email','TEXT'),('due_date','TEXT'),('workflow_status',"TEXT DEFAULT '作成中'"),('approved_revision','INTEGER'),('approved_by','TEXT'),('approved_at','TIMESTAMPTZ'),('submitted_at','TIMESTAMPTZ'),('review_notes','JSONB'),('doc_type',"TEXT NOT NULL DEFAULT 'test'"),('target_week','TEXT')]:
            conn.execute('ALTER TABLE studio_shared_docs ADD COLUMN IF NOT EXISTS '+col+' '+typ)
        conn.commit()
        _WORKFLOW_READY=True

def workflow_action(doc_id, action, user, body):
    with shared_db() as conn:
        workflow_init(conn)
        row=conn.execute("SELECT revision,workflow_status,approved_revision FROM studio_shared_docs WHERE id=%s FOR UPDATE",(doc_id,)).fetchone()
        if not row:return {'error':'資料が見つかりません'},404
        revision,status,approved=row
        if action=='metadata':
            due=body.get('dueDate','')
            owner=body.get('owner','')
            doc_type=body.get('docType')
            target_week=body.get('targetWeek')
            if not isinstance(due,str) or (due and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',due)) or not isinstance(owner,str) or len(owner)>120:return {'error':'入力値が不正です'},422
            if doc_type is not None and doc_type not in ('test','production'):return {'error':'資料区分が不正です'},422
            if target_week is not None and (not isinstance(target_week,str) or (target_week and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',target_week))):return {'error':'対象週が不正です'},422
            conn.execute("UPDATE studio_shared_docs SET due_date=%s,owner_email=%s,doc_type=COALESCE(%s,doc_type),target_week=COALESCE(%s,target_week) WHERE id=%s",(due,owner,doc_type,target_week,doc_id))
        elif action=='request':
            conn.execute("UPDATE studio_shared_docs SET workflow_status='承認待ち' WHERE id=%s",(doc_id,))
        elif action=='approve':
            if user!=APPROVER_EMAIL:return {'error':'中島のGoogleアカウントのみ承認できます'},403
            if status!='承認待ち':return {'error':'承認待ちではありません'},409
            conn.execute("UPDATE studio_shared_docs SET workflow_status='承認済み',approved_revision=revision,approved_by=%s,approved_at=NOW() WHERE id=%s",(user,doc_id))
        elif action=='reject':
            if user!=APPROVER_EMAIL:return {'error':'中島のGoogleアカウントのみ差し戻せます'},403
            notes=body.get('notes')
            if not isinstance(notes,list) or not notes or len(notes)>40:return {'error':'差し戻し理由を入力してください'},422
            clean=[]
            for note in notes:
                if not isinstance(note,dict):return {'error':'指摘内容が不正です'},422
                message=str(note.get('text','')).strip()
                page=note.get('page',0)
                if not message or len(message)>1000 or type(page)!=int or not 0<=page<=500:return {'error':'指摘内容を確認してください'},422
                clean.append({'page':page,'text':message,'by':user})
            conn.execute("UPDATE studio_shared_docs SET workflow_status='差し戻し',review_notes=%s WHERE id=%s",(__import__('psycopg').types.json.Jsonb(clean),doc_id))
        elif action=='submit':
            if status not in ('承認済み','提出済み') or approved!=revision:return {'error':'最新版の承認が必要です'},409
            conn.execute("UPDATE studio_shared_docs SET workflow_status='提出済み',submitted_at=NOW() WHERE id=%s",(doc_id,))
        else:return {'error':'操作が不正です'},422
    return {'ok':True},200

def shared_versions(doc_id):
    with shared_db() as conn:
        shared_init(conn)
        rows=conn.execute("SELECT revision,updated_by,created_at FROM studio_shared_versions WHERE doc_id=%s ORDER BY revision DESC LIMIT 100",(doc_id,)).fetchall()
        return [{'revision':r[0],'updatedBy':r[1],'updatedAt':r[2].isoformat()} for r in rows]

def shared_version_get(doc_id,revision):
    with shared_db() as conn:
        shared_init(conn)
        row=conn.execute("SELECT data FROM studio_shared_versions WHERE doc_id=%s AND revision=%s",(doc_id,revision)).fetchone()
        return {'data':row[0],'revision':revision} if row else None

def shared_list():
    with shared_db() as conn:
        shared_init(conn)
        workflow_init(conn)
        rows=conn.execute("SELECT id,name,data->>'client',workflow_status,revision,updated_by,updated_at,owner_email,due_date,approved_revision,review_notes,doc_type,target_week FROM studio_shared_docs ORDER BY updated_at DESC LIMIT 500").fetchall()
        return [{'id':r[0],'name':r[1],'client':r[2] or 'クライアント未設定','status':r[3] or '作成中','revision':r[4],'updatedBy':r[5],'updatedAt':r[6].isoformat(),'owner':r[7] or '', 'dueDate':r[8] or '', 'approvedRevision':r[9],'reviewNotes':r[10] or [],'docType':r[11] or 'test','targetWeek':r[12] or ''} for r in rows]

def shared_get(doc_id):
    with shared_db() as conn:
        shared_init(conn)
        workflow_init(conn)
        row=conn.execute("SELECT name,data,revision,updated_by,updated_at,workflow_status,owner_email,due_date,approved_revision,review_notes,doc_type,target_week FROM studio_shared_docs WHERE id=%s",(doc_id,)).fetchone()
        return {'id':doc_id,'name':row[0],'data':row[1],'revision':row[2],'updatedBy':row[3],'updatedAt':row[4].isoformat(),'status':row[5],'owner':row[6] or '', 'dueDate':row[7] or '', 'approvedRevision':row[8],'reviewNotes':row[9] or [], 'docType':row[10] or 'test','targetWeek':row[11] or ''} if row else None

def next_friday_iso():
    from datetime import date,timedelta
    today=date.today()
    return (today+timedelta(days=(4-today.weekday())%7)).isoformat()

def shared_create(name,data,user):
    import uuid
    doc_id=uuid.uuid4().hex
    with shared_db() as conn:
        shared_init(conn)
        workflow_init(conn)
        conn.execute("INSERT INTO studio_shared_docs(id,name,data,updated_by,due_date,owner_email,doc_type) VALUES (%s,%s,%s,%s,%s,%s,'production')",(doc_id,name,__import__('psycopg').types.json.Jsonb(data),user,next_friday_iso(),user))
        conn.execute("INSERT INTO studio_shared_versions(doc_id,revision,data,updated_by) VALUES (%s,1,%s,%s)",(doc_id,__import__('psycopg').types.json.Jsonb(data),user))
    return {'id':doc_id,'revision':1}

def shared_update(doc_id,revision,data,user):
    with shared_db() as conn:
        shared_init(conn)
        workflow_init(conn)
        row=conn.execute("""UPDATE studio_shared_docs SET data=%s,revision=revision+1,workflow_status='作成中',
            updated_by=%s,updated_at=NOW() WHERE id=%s AND revision=%s
            RETURNING revision""",(__import__('psycopg').types.json.Jsonb(data),user,doc_id,revision)).fetchone()
        if row:
            conn.execute("INSERT INTO studio_shared_versions(doc_id,revision,data,updated_by) VALUES (%s,%s,%s,%s)",(doc_id,row[0],__import__('psycopg').types.json.Jsonb(data),user))
            return {'revision':row[0]}
        exists=conn.execute("SELECT 1 FROM studio_shared_docs WHERE id=%s",(doc_id,)).fetchone()
        return {'conflict':bool(exists),'missing':not bool(exists)}

def shared_delete(doc_id):
    with shared_db() as conn:
        shared_init(conn)
        workflow_init(conn)
        conn.execute("DELETE FROM studio_shared_versions WHERE doc_id=%s",(doc_id,))
        deleted=conn.execute("DELETE FROM studio_shared_docs WHERE id=%s RETURNING id",(doc_id,)).fetchone()
        return bool(deleted)

def configured():
    return bool(CLIENT_ID and len(SECRET) >= 32 and ALLOWED)

def sign(value):
    return hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()

def encode_session(email):
    payload = base64.urlsafe_b64encode(json.dumps({'email': email, 'exp': int(time.time()) + 28800}).encode()).decode().rstrip('=')
    return payload + '.' + sign(payload)

def current_user(cookie):
    # Preview branch only: demo identity; never enable this flag on production.
    if os.getenv('STUDIO_PREVIEW_NO_LOGIN') == 'yes':
        return 'preview@108-studio.test'
    if not configured(): return None
    try:
        jar = http.cookies.SimpleCookie(); jar.load(cookie or '')
        payload, signature = jar['studio_session'].value.rsplit('.', 1)
        if not hmac.compare_digest(sign(payload), signature): return None
        info = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        email = info['email'].lower()
        return email if email in ALLOWED and info['exp'] > time.time() else None
    except (KeyError, ValueError, TypeError, json.JSONDecodeError): return None

def verify_token(token):
    if not configured(): raise ValueError('ログイン設定が未完了です')
    if not isinstance(token, str) or len(token) > 10000: raise ValueError('不正な認証情報です')
    url = 'https://oauth2.googleapis.com/tokeninfo?' + urllib.parse.urlencode({'id_token': token})
    with urllib.request.urlopen(url, timeout=12) as response:
        info = json.load(response)
    if info.get('aud') != CLIENT_ID or info.get('email_verified') not in (True, 'true'):
        raise ValueError('Google認証に失敗しました')
    email = info.get('email', '').lower()
    if email not in ALLOWED: raise ValueError('このアカウントには利用権限がありません')
    return email

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('STUDIO_AI_MODEL', 'gpt-4.1-mini').strip()
AI_FIELDS = ('title', 'category', 'url', 'members', 'sound', 'part', 'location', 'content', 'shoot', 'aim', 'extra')

def polish_ideas(ideas):
    if not OPENAI_API_KEY:
        raise ValueError('AI文章整形が未設定です。Renderの環境変数 OPENAI_API_KEY を登録してください。原文は取り込んでいません。')
    instruction = ("あなたは108 STUDIOのクライアント提出用SNS企画書の文章校正者です。"
      "入力は最終確定済みの企画です。企画を考案・追加・分割・統合・評価せず、意図や面白さを変えないこと。"
      "相談・詰めたいこと・質問・社内FB・人選理由など提出不要の情報を除外する。"
      "話し言葉、重複、冗長な表現を整理し、自然な日本語で簡潔にする。"
      "出演者、音源、使用箇所、場所・衣装、撮影手順、企画の狙い、数値、固有名詞、URLを創作・変更・省略しない。"
      "記載のない内容は補わない。空欄は空欄のまま。"
      "titleは冒頭の装飾記号を除去。category、url、members、sound、part、locationは編集対象外（返却値は無視され元データを使用）。"
      "content、shoot、aim、extraは元情報を保持して整える。"
      "入力配列と同じ件数・同じ順序でJSONを返す。")
    schema = {'type':'object','additionalProperties':False,'required':['ideas'],
      'properties':{'ideas':{'type':'array','items':{'type':'object','additionalProperties':False,
        'required':list(AI_FIELDS),'properties':{k:{'type':'string'} for k in AI_FIELDS}}}}}
    payload={'model':OPENAI_MODEL,'temperature':0,'response_format':{'type':'json_schema','json_schema':{'name':'studio_polished_ideas','strict':True,'schema':schema}},
      'messages':[{'role':'system','content':instruction},{'role':'user','content':json.dumps({'ideas':ideas},ensure_ascii=False)}]}
    req=urllib.request.Request('https://api.openai.com/v1/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),
      headers={'Authorization':'Bearer '+OPENAI_API_KEY,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=65) as resp: data=json.load(resp)
        answer=json.loads(data['choices'][0]['message']['content'])['ideas']
    except Exception as exc:
        raise ValueError('AI文章整形に失敗しました。企画は登録されていません。しばらくして再試行してください。') from exc
    if not isinstance(answer,list) or len(answer)!=len(ideas):
        raise ValueError('AIの出力件数が一致しません。登録は行っていません。')
    for old,new in zip(ideas,answer):
        if not isinstance(new,dict) or any(not isinstance(new.get(k),str) for k in AI_FIELDS):
            raise ValueError('AIの出力形式が不正です。登録は行っていません。')
        # Immutable fields are always restored from the trusted parsed source.
        # Do not reject the whole batch merely because the model echoed them differently.
        for k in ('category','url','members','sound','part','location'):
            new[k] = old[k]
        if not new['title'].strip():
            new['title'] = old['title']
        # Do not allow the model to erase non-empty descriptive sections.
        for k in ('content','shoot','aim','extra'):
            if old[k].strip() and not new[k].strip():
                new[k] = old[k]
    return answer

SLACK_TOKEN = os.getenv('STUDIO_SLACK_BOT_TOKEN', '').strip()

def slack_api(method, params=None):
    if not SLACK_TOKEN: raise ValueError('Slack連携が未設定です。Renderに STUDIO_SLACK_BOT_TOKEN を設定してください')
    url = 'https://slack.com/api/' + method
    if params: url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + SLACK_TOKEN, 'Accept': 'application/json', 'User-Agent': '108-STUDIO/0.5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp: data = json.load(resp)
    except Exception as e: raise ValueError('Slackへの接続に失敗しました') from e
    if not data.get('ok'):
        error = data.get('error','unknown_error')
        if error in ('not_in_channel','channel_not_found'): raise ValueError('チャンネルを読み取れません。Slackアプリをチャンネルに招待してください')
        if error == 'missing_scope': raise ValueError('Slackアプリの権限が不足しています。channels:history / groups:history / channels:read / groups:read を確認してください')
        raise ValueError('Slack APIエラー: ' + str(error))
    return data

def is_final_thread(text):
    import re
    return bool(re.search(r'最終\s*確定\s*企画', text or ''))

LOGIN = '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>108 STUDIO | ログイン</title><script src="https://accounts.google.com/gsi/client" async defer></script><style>body{background:#f2f4f7;color:#18233a;font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100dvh;margin:0;padding:16px;box-sizing:border-box}.box{box-sizing:border-box;width:100%;background:white;border:1px solid #e2e5ec;border-radius:18px;padding:clamp(20px,6vw,42px);max-width:420px;text-align:center;box-shadow:0 10px 50px #14203a10}h1{letter-spacing:.1em}p{color:#65718a;line-height:1.7}#btn{display:flex;justify-content:center;margin:26px 0;min-height:44px}#msg{color:#b13046;white-space:pre-line;line-height:1.6;font-size:14px}#retry{display:none;margin:14px auto;border:0;border-radius:8px;background:#152440;color:white;padding:12px 20px;font-size:14px;cursor:pointer}</style></head><body><div class="box"><h1>108 STUDIO</h1><p>社内向け企画資料エディタ<br>許可されたGoogleアカウントでログインしてください。</p><div id="btn"></div><p id="msg" role="status" aria-live="polite"></p><button id="retry" type="button">再読み込みして確認</button></div><script>\nconst cid=__CLIENT_ID__,msg=document.getElementById(\'msg\'),retry=document.getElementById(\'retry\');\nretry.addEventListener(\'click\',()=>location.reload());\nasync function checkSession(){const r=await fetch(\'/api/me\',{credentials:\'include\',cache:\'no-store\'});if(!r.ok)return false;const j=await r.json();return !!j.email;}\nasync function onCredential(response){msg.textContent=\'ログインを確認しています…\';retry.style.display=\'none\';try{if(!response||!response.credential)throw Error(\'Googleの認証情報を受け取れませんでした\');const res=await fetch(\'/api/login\',{method:\'POST\',credentials:\'include\',cache:\'no-store\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({credential:response.credential})});const j=await res.json();if(!res.ok)throw Error(j.error||\'ログインに失敗しました\');if(!await checkSession())throw Error(\'ログインは成功しましたが、ブラウザにログイン情報を保存できませんでした。Safari/Chromeで直接開き、Cookieを許可して再試行してください。\');location.replace(\'/\');}catch(e){msg.textContent=e.message||\'ログインに失敗しました\';retry.style.display=\'block\';}}\nfunction initialize(){if(!cid){msg.textContent=\'管理者によるログイン設定が必要です\';return;}if(!window.google||!google.accounts||!google.accounts.id){msg.textContent=\'Googleログインを読み込めませんでした。Safari/Chromeで開き直してください。\';retry.style.display=\'block\';return;}google.accounts.id.initialize({client_id:cid,callback:onCredential,ux_mode:\'popup\'});google.accounts.id.renderButton(document.getElementById(\'btn\'),{theme:\'outline\',size:\'large\',text:\'signin_with\',width:Math.min(290,window.innerWidth-75)});}\nwindow.addEventListener(\'load\',initialize);\n</script></body></html>'

# Chromium renders native text and links, avoiding html2canvas's Japanese glyph corruption.
def render_native_pdf(markup):
    from playwright.sync_api import sync_playwright
    if not isinstance(markup,str) or len(markup.encode('utf-8'))>14_000_000:
        raise ValueError('PDF資料が大きすぎます（14MB以内）')
    if re.search(r'<\s*(script|iframe|object|embed|form|base)\b',markup,re.I):
        raise ValueError('PDF用HTMLに使用できない要素が含まれています')
    # Render's existing service build command may differ from render.yaml.
    # Prefer the bundled Noto font, then install a cached runtime fallback.
    import subprocess
    import urllib.request
    candidates = [
        Path.home()/'.local/share/fonts/NotoSansCJKjp-Regular.otf',
        Path('/usr/local/share/fonts/NotoSansCJKjp-Regular.otf'),
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        Path('/tmp/studio-fonts/NotoSansCJKjp-Regular.otf'),
    ]
    try:
        matched = subprocess.run(['fc-match','-f','%{file}','Noto Sans CJK JP'],capture_output=True,text=True,timeout=5).stdout.strip()
        if matched and ('NotoSansCJK' in Path(matched).name or 'NotoSansCJK' in matched):
            candidates.append(Path(matched))
    except (OSError,subprocess.TimeoutExpired):
        pass
    font_path = next((p for p in candidates if p.is_file() and p.stat().st_size>1_000_000),None)
    if font_path is None:
        font_path = Path('/tmp/studio-fonts/NotoSansCJKjp-Regular.otf')
        font_path.parent.mkdir(parents=True,exist_ok=True)
        temp_path = font_path.with_suffix('.download')
        try:
            urllib.request.urlretrieve('https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf',temp_path)
            if temp_path.stat().st_size<1_000_000:
                raise RuntimeError('Downloaded Japanese font is unexpectedly small')
            temp_path.replace(font_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError('Japanese PDF font installation failed; check Render build command and network access: '+str(exc)[:120]) from exc
    # Ensure the runtime-downloaded font is visible to Chromium's fontconfig.
    # A font sitting in /tmp is not automatically discovered by fc-match.
    import shutil
    installed_dir=Path.home()/'.local/share/fonts'
    installed_dir.mkdir(parents=True,exist_ok=True)
    installed_font=installed_dir/'NotoSansCJKjp-Regular.otf'
    if font_path.resolve()!=installed_font.resolve() and (not installed_font.exists() or installed_font.stat().st_size!=font_path.stat().st_size):
        shutil.copyfile(font_path,installed_font)
    # Rebuild fontconfig only when installing a new font; doing it on every PDF is slow.
    if not getattr(render_native_pdf,'_font_ready',False):
        subprocess.run(['fc-cache',str(installed_dir)],check=True,timeout=30)
    actual_font=subprocess.run(['fc-match','-f','%{file}','Noto Sans CJK JP'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
    if not actual_font or not Path(actual_font).is_file() or 'NotoSansCJK' not in Path(actual_font).name:
        raise RuntimeError('Japanese font is not registered with fontconfig: '+actual_font[:160])
    render_native_pdf._font_ready=True
    # Match the editor's Noto Sans JP family. The installed CJK JP font is
    # a reliable local fallback if the Google Fonts stylesheet is unavailable.
    font_css=('<style>@import url("https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;800;900&display=swap");'
              'html,body,.page,.page *{font-family:"Noto Sans JP","Noto Sans CJK JP",sans-serif!important}</style>')
    markup=font_css+markup
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=(os.getenv('STUDIO_CHROMIUM_PATH') or ('/usr/bin/chromium' if Path('/usr/bin/chromium').exists() else None)),args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            page=browser.new_page(viewport={'width':794,'height':1123},device_scale_factor=1)
            # Do not allow the PDF worker to fetch arbitrary external URLs.
            def filter_request(route):
                u=urllib.parse.urlparse(route.request.url)
                if u.scheme=='data' or (u.scheme=='https' and u.hostname in ('fonts.googleapis.com','fonts.gstatic.com')):
                    return route.continue_()
                return route.abort()
            page.route('**/*',filter_request)
            page.set_content(markup,wait_until='domcontentloaded',timeout=25000)
            page.evaluate('document.fonts.ready')
            page.locator('.page').first.wait_for(timeout=10000)
            count=page.locator('.page').count()
            if count<1 or count>60:raise ValueError('PDFページ数は1〜60ページにしてください')
            # Print CSS preserves backgrounds and all <a href> links as PDF annotations.
            return page.pdf(format='A4',print_background=True,prefer_css_page_size=True,display_header_footer=False)
        finally:browser.close()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)
    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()
    def send_bytes(self, body, status=200, ctype='text/html; charset=utf-8'):
        self.send_response(status); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), status, 'application/json; charset=utf-8')
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/health': return self.send_json({'ok': True})
        user = current_user(self.headers.get('Cookie', ''))
        if path == '/api/me': return self.send_json({'email': user} if user else {'email': None}, 200 if user else 401)
        if not user:
            if path in ('/', '/index.html'):
                return self.send_bytes(LOGIN.replace('__CLIENT_ID__', json.dumps(CLIENT_ID)).encode())
            return self.send_json({'error': 'ログインが必要です'}, 401)
        if os.getenv('STUDIO_PREVIEW_NO_LOGIN') == 'yes' and re.fullmatch(r'/api/preview-presence/[a-f0-9]{32}',path):
            return self.send_json({'users':preview_presence(path.rsplit('/',1)[-1])})
        if path == '/api/dojo/feedback':
            url=os.getenv('DOJO_API_URL','').rstrip('/')
            key=os.getenv('DOJO_STUDIO_API_KEY','').strip()
            if not url or not key:return self.send_json({'error':'道場連携の環境変数が未設定です'},503)
            if not url.startswith('https://') and not url.startswith('http://localhost:'):return self.send_json({'error':'道場のURLはHTTPSが必要です'},503)
            params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            idea_id=(params.get('id') or [''])[0].strip()
            client=(params.get('client') or [''])[0].strip()
            if not idea_id or len(idea_id)>150 or not client or len(client)>120:return self.send_json({'error':'企画IDとクライアントを指定してください'},422)
            try:
                req=urllib.request.Request(url+'/api/studio/feedback?'+urllib.parse.urlencode({'id':idea_id,'client':client}),headers={'X-108-Integration-Key':key,'Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=15) as response:data=json.load(response)
                return self.send_json(data)
            except Exception as exc:
                print('Dojo feedback integration error:',type(exc).__name__,str(exc)[:200],flush=True)
                return self.send_json({'error':'中島FBを取得できませんでした'},502)
        if path == '/api/dojo/feedback':
            url=os.getenv('DOJO_API_URL','').rstrip('/')
            key=os.getenv('DOJO_STUDIO_API_KEY','').strip()
            if not url or not key:return self.send_json({'error':'道場連携が未設定です'},503)
            if not url.startswith('https://') and not url.startswith('http://localhost:'):return self.send_json({'error':'道場URLが不正です'},503)
            params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            idea_id=(params.get('id') or [''])[0].strip()
            client=(params.get('client') or [''])[0].strip()
            if not idea_id or not client:return self.send_json({'error':'IDとクライアントが必要です'},422)
            try:
                req=urllib.request.Request(url+'/api/studio/feedback?'+urllib.parse.urlencode({'id':idea_id,'client':client}),headers={'X-108-Integration-Key':key,'Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=15) as response:data=json.load(response)
                return self.send_json(data)
            except Exception:
                return self.send_json({'error':'道場FBの取得に失敗しました'},502)
        if path == '/api/dojo/ideas':
            url=os.getenv('DOJO_API_URL','').rstrip('/')
            key=os.getenv('DOJO_STUDIO_API_KEY','').strip()
            if not url or not key:return self.send_json({'error':'道場連携の環境変数が未設定です'},503)
            if not url.startswith('https://') and not url.startswith('http://localhost:'):
                return self.send_json({'error':'道場のURLはHTTPSが必要です'},503)
            client=(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('client') or [''])[0].strip()
            if not client or len(client)>120:return self.send_json({'error':'クライアントを指定してください'},422)
            try:
                req=urllib.request.Request(url+'/api/studio/ideas?'+urllib.parse.urlencode({'client':client}),
                    headers={'X-108-Integration-Key':key,'Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=15) as response:
                    data=json.load(response)
                return self.send_json({'ideas':data.get('ideas',[])})
            except Exception as exc:
                print('Dojo integration error:',type(exc).__name__,str(exc)[:200],flush=True)
                return self.send_json({'error':'108道場から企画を取得できませんでした'},502)
        if os.getenv('STUDIO_PREVIEW_NO_LOGIN') == 'yes' and re.fullmatch(r'/api/preview-presence/[a-f0-9]{32}',path):
            if not current_user(self.headers.get('Cookie','')):return self.send_json({'error':'unauthorized'},401)
            origin=self.headers.get('Origin','');host=self.headers.get('Host','')
            if origin and origin not in ('https://'+host,'http://'+host,'http://localhost:'+str(PORT)):return self.send_json({'error':'origin mismatch'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024:return self.send_json({'error':'invalid size'},422)
                body=json.loads(self.rfile.read(length))
                cid=body.get('clientId');name=body.get('name');page=body.get('page','')
                if not isinstance(cid,str) or not re.fullmatch(r'[a-f0-9]{32}',cid) or not isinstance(name,str) or not name.strip():return self.send_json({'error':'invalid user'},422)
                return self.send_json({'users':preview_presence(path.rsplit('/',1)[-1],cid,name.strip(),page)})
            except (ValueError,TypeError):return self.send_json({'error':'invalid request'},422)
        if path == '/api/clients':
            try:return self.send_json({'clients':clients_list()})
            except Exception as e:return self.send_json({'error':'クライアント一覧を取得できませんでした'},503)
        if path == '/api/shared-docs' or path.startswith('/api/shared-docs/'):
            try:
                if path == '/api/shared-docs':return self.send_json({'documents':shared_list()})
                match=re.fullmatch(r'/api/shared-docs/([a-f0-9]{32})/versions(?:/([0-9]+))?',path)
                if match:
                    doc_id=match.group(1)
                    if not shared_get(doc_id):return self.send_json({'error':'資料が見つかりません'},404)
                    if match.group(2):
                        version=shared_version_get(doc_id,int(match.group(2)))
                        return self.send_json(version if version else {'error':'履歴が見つかりません'},200 if version else 404)
                    return self.send_json({'versions':shared_versions(doc_id)})
                doc_id=path.rsplit('/',1)[-1]
                if not re.fullmatch(r'[a-f0-9]{32}',doc_id):return self.send_json({'error':'invalid document id'},400)
                doc=shared_get(doc_id)
                return self.send_json(doc if doc else {'error':'資料が見つかりません'},200 if doc else 404)
            except Exception as e:
                print('shared GET failed',repr(e),flush=True)
                return self.send_json({'error':str(e) if isinstance(e,RuntimeError) else '共有資料の読み込みに失敗しました'},503)
        if path.startswith('/api/slack/'):
            try:
                args = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                channel = args.get('channel',[''])[0]
                if path == '/api/slack/status': return self.send_json({'connected':bool(SLACK_TOKEN)})
                if path == '/api/slack/channels':
                    channels=[]; cursor=''
                    for _ in range(5):
                        q={'types':'public_channel,private_channel','limit':200,'exclude_archived':'true'}
                        if cursor: q['cursor']=cursor
                        result=slack_api('conversations.list',q)
                        channels += [{'id':x['id'],'name':x.get('name','')} for x in result.get('channels',[]) if x.get('is_member')]
                        cursor=result.get('response_metadata',{}).get('next_cursor','')
                        if not cursor: break
                    return self.send_json({'channels':channels})
                if not re.fullmatch(r'[CG][A-Z0-9]{7,20}',channel): return self.send_json({'error':'SlackチャンネルIDを指定してください'},400)
                if path == '/api/slack/threads':
                    items=[]; cursor=''; scanned=0; candidates=0; pages=0
                    for _ in range(5):
                        q={'channel':channel,'limit':200}
                        if cursor: q['cursor']=cursor
                        result=slack_api('conversations.history',q)
                        pages += 1
                        for m in result.get('messages',[]):
                            scanned += 1
                            if is_final_thread(m.get('text','')):
                                candidates += 1
                                # Slack may include thread_ts on a parent; only exclude actual replies.
                                if m.get('thread_ts') and m['thread_ts'] != m.get('ts'):
                                    continue
                                items.append({'ts':m['ts'], 'title':m.get('text','').split('\n')[0][:100],
                                              'replies':m.get('reply_count',0), 'date':time.strftime('%Y/%m/%d',time.localtime(float(m['ts'])))})
                        cursor=result.get('response_metadata',{}).get('next_cursor','')
                        if not cursor: break
                    items.sort(key=lambda x:float(x['ts']), reverse=True)
                    return self.send_json({'threads':items,'diagnostics':{'scanned':scanned,'matched':candidates,'pages':pages,'more':bool(cursor)}})
                if path == '/api/slack/replies':
                    ts=args.get('ts',[''])[0]
                    if not re.fullmatch(r'\d{10,12}\.\d{6}',ts): return self.send_json({'error':'スレッドIDが不正です'},400)
                    items=[]; cursor=''
                    for _ in range(10):
                        q={'channel':channel,'ts':ts,'limit':200}
                        if cursor: q['cursor']=cursor
                        result=slack_api('conversations.replies',q)
                        items.extend(result.get('messages',[]))
                        cursor=result.get('response_metadata',{}).get('next_cursor','')
                        if not cursor: break
                    # Parent message is not an idea. Skip bot/system messages and deleted replies.
                    ideas=[{'ts':m['ts'],'text':m.get('text','')} for m in items if m.get('ts')!=ts and m.get('text','').strip() and m.get('subtype') not in ('message_deleted','channel_join','channel_leave')]
                    return self.send_json({'messages':ideas})
                return self.send_json({'error':'not found'},404)
            except ValueError as e: return self.send_json({'error':str(e)},400)
            except Exception: return self.send_json({'error':'Slack取得中にエラーが発生しました'},502)
        if path == '/': self.path = '/index.html'
        return super().do_GET()
    def do_DELETE(self):
        path=urllib.parse.urlparse(self.path).path
        match=re.fullmatch(r'/api/shared-docs/([a-f0-9]{32})',path)
        if not match:return self.send_json({'error':'Not found'},404)
        user=current_user(self.headers.get('Cookie',''))
        if not user:return self.send_json({'error':'ログインが必要です'},401)
        origin=self.headers.get('Origin','')
        host=self.headers.get('Host','')
        if origin and origin not in ('https://'+host,'http://'+host,'http://localhost:'+str(PORT)):
            return self.send_json({'error':'origin mismatch'},403)
        try:
            if not shared_delete(match.group(1)):return self.send_json({'error':'資料が見つかりません'},404)
            return self.send_json({'ok':True})
        except Exception as e:
            print('shared DELETE failed',repr(e),flush=True)
            return self.send_json({'error':'資料削除に失敗しました'},503)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/api/clients':
            user=current_user(self.headers.get('Cookie',''))
            if not user:return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            host=self.headers.get('Host','')
            if origin and origin not in ('https://'+host,'http://'+host,'http://localhost:'+str(PORT)):return self.send_json({'error':'origin mismatch'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:return self.send_json({'error':'入力が大きすぎます'},422)
                return self.send_json(clients_save(json.loads(self.rfile.read(length))))
            except ValueError as e:return self.send_json({'error':str(e)},422)
            except Exception as e:return self.send_json({'error':'保存に失敗しました'},503)
        if path == '/api/logout':
            self.send_response(200); self.send_header('Set-Cookie', 'studio_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0'); self.end_headers(); return
        match=re.fullmatch(r'/api/shared-docs/([a-f0-9]{32})/workflow',path)
        if match:
            user=current_user(self.headers.get('Cookie',''))
            if not user:return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            host=self.headers.get('Host','')
            if origin and origin not in ('https://'+host,'http://'+host,'http://localhost:'+str(PORT)):return self.send_json({'error':'origin mismatch'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:return self.send_json({'error':'リクエストが不正です'},422)
                body=json.loads(self.rfile.read(length))
                result,code=workflow_action(match.group(1),body.get('action'),user,body)
                return self.send_json(result,code)
            except Exception as e:
                print('workflow error',repr(e),flush=True)
                return self.send_json({'error':'承認処理に失敗しました'},503)
        if path == '/api/shared-docs' or path.startswith('/api/shared-docs/'):
            user=current_user(self.headers.get('Cookie',''))
            if not user:return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            host=self.headers.get('Host','')
            if origin and origin not in ('https://'+host,'http://'+host,'http://localhost:'+str(PORT)):
                return self.send_json({'error':'origin mismatch'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8_000_000:raise ValueError('資料データは8MB以内にしてください')
                body=json.loads(self.rfile.read(length))
                data=body.get('data')
                if not isinstance(data,dict) or not isinstance(data.get('ideas'),list):
                    raise ValueError('資料データが不正です')
                if path == '/api/shared-docs':
                    name=str(body.get('name','')).strip()[:120]
                    if not name:raise ValueError('資料名を入力してください')
                    return self.send_json(shared_create(name,data,user),201)
                doc_id=path.rsplit('/',1)[-1]
                if not re.fullmatch(r'[a-f0-9]{32}',doc_id):raise ValueError('資料IDが不正です')
                revision=body.get('revision')
                if type(revision)!=int or revision<1:raise ValueError('版番号が不正です')
                result=shared_update(doc_id,revision,data,user)
                return self.send_json(result,409 if result.get('conflict') else 404 if result.get('missing') else 200)
            except ValueError as e:return self.send_json({'error':str(e)},422)
            except Exception as e:
                print('shared POST failed',repr(e),flush=True)
                return self.send_json({'error':str(e) if isinstance(e,RuntimeError) else '共有資料の保存に失敗しました'},503)
        if path == '/api/export-pdf':
            if not current_user(self.headers.get('Cookie','')):
                return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            host=self.headers.get('Host','')
            if origin and origin not in ('https://'+host,'http://'+host):
                return self.send_json({'error':'origin mismatch'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=14_000_000:raise ValueError('PDF資料が大きすぎます')
                payload=json.loads(self.rfile.read(length))
                doc_id=payload.get('docId','')
                revision=payload.get('revision')
                if not isinstance(doc_id,str) or not re.fullmatch(r'[a-f0-9]{32}',doc_id) or type(revision)!=int:
                    return self.send_json({'error':'承認済みの共有資料からPDFを出力してください'},403)
                with shared_db() as conn:
                    workflow_init(conn)
                    row=conn.execute("SELECT revision,approved_revision,workflow_status,doc_type FROM studio_shared_docs WHERE id=%s",(doc_id,)).fetchone()
                if not row:
                    return self.send_json({'error':'資料が見つかりません'},404)
                is_approved_version = row[1] == revision
                if row[3]!='test' and not is_approved_version and current_user(self.headers.get('Cookie',''))!=APPROVER_EMAIL:
                    return self.send_json({'error':'修正版のPDF出力には再承認が必要です'},403)
                if revision != row[0] and not is_approved_version:
                    return self.send_json({'error':'承認済み版または最新版を指定してください'},409)
                if is_approved_version and revision != row[0]:
                    # The approved snapshot is immutable; reject client HTML that does not match its revision.
                    version=shared_version_get(doc_id,revision)
                    if not version:return self.send_json({'error':'承認済み版が見つかりません'},404)
                pdf=render_native_pdf(payload.get('html',''))
                self.send_bytes(pdf,200,'application/pdf')
            except ValueError as e:return self.send_json({'error':str(e)},422)
            except Exception as e:
                print('PDF export failed:',repr(e),flush=True)
                return self.send_json({'error':'PDF生成エラー: '+str(e)[:240]},500)
            return
        if path == '/api/tiktok-thumbnail':
            if not current_user(self.headers.get('Cookie', '')):
                return self.send_json({'error': 'ログインが必要です'}, 401)
            origin = self.headers.get('Origin', '')
            if origin and origin not in ('https://' + self.headers.get('Host',''), 'http://localhost:' + str(PORT)):
                return self.send_json({'error': 'origin mismatch'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096: raise ValueError('リクエストが大きすぎます')
                payload = json.loads(self.rfile.read(length))
                video_url = str(payload.get('url', '')).strip()
                parsed = urllib.parse.urlparse(video_url)
                host = (parsed.hostname or '').lower()
                if parsed.scheme != 'https' or host not in ('www.tiktok.com', 'tiktok.com', 'm.tiktok.com') or not parsed.path.startswith('/@') or '/video/' not in parsed.path:
                    raise ValueError('通常のTikTok動画URL（https://www.tiktok.com/@.../video/...）を指定してください')
                canonical = 'https://www.tiktok.com' + parsed.path
                endpoint = 'https://www.tiktok.com/oembed?' + urllib.parse.urlencode({'url': canonical})
                req = urllib.request.Request(endpoint, headers={'User-Agent': 'Mozilla/5.0 (108 STUDIO preview)', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=9) as response:
                    info = json.load(response)
                thumbnail = info.get('thumbnail_url', '')
                parsed_thumb = urllib.parse.urlparse(thumbnail)
                if parsed_thumb.scheme != 'https' or not parsed_thumb.hostname:
                    raise ValueError('サムネイルURLが取得できませんでした')
                return self.send_json({'thumbnail': thumbnail, 'source': 'TikTok oEmbed'})
            except Exception:
                return self.send_json({'error': 'TikTokからサムネイルを取得できませんでした。動画の公開状態やTikTok側の制限が原因の場合があります。画像を手動で指定してください。'}, 422)
        if path == '/api/ai/polish-field':
            if not current_user(self.headers.get('Cookie','')): return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            if origin and origin not in ('https://'+self.headers.get('Host',''),'http://localhost:'+str(PORT)):
                return self.send_json({'error':'origin mismatch'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=30000: raise ValueError('入力が大きすぎます')
                body=json.loads(self.rfile.read(size))
                field=body.get('field'); original=body.get('text')
                if field not in ('title','content','shoot','aim','extra','freeNote') or not isinstance(original,str) or not original.strip() or len(original)>12000:
                    raise ValueError('編集対象の文章が不正です')
                key=os.environ.get('OPENAI_API_KEY','')
                if not key: raise ValueError('OPENAI_API_KEY が設定されていません')
                label={'title':'企画タイトル','content':'企画内容','shoot':'撮影・演出イメージ','aim':'狙い・意図','extra':'補足','freeNote':'自由記入欄'}[field]
                instructions=(
                    'あなたは108 STUDIOのSNS企画書の文章編集者です。目的はクライアントが一読で内容と撮影イメージを理解できる、読みやすい文章に整えること。'
                    '短縮そのものを目的にしない。原文が短く説明不足なら、原文に含まれる情報だけを自然につなぎ、少し長くしてよい。'
                    '原文が長ければ重複や冗長な表現を削る。適切な長さなら大きく変えない。'
                    '読みやすい行数を優先する。通常は1〜3文程度、意味の区切りで改行する。タイトルは原則1行で簡潔に。'
                    '撮影手順が複数ある場合は必要な順序を保持する。読みやすさのための改行は可。'
                    '原文にない事実、出演者、演出、撮影工程、狙い、固有名詞、数値、URLを創作・推測・追加しない。'
                    '企画の核、面白さ、出演メンバー、音源、撮影手順、固有名詞、数値、URLは保持する。'
                    '過剰に丁寧な表現、抽象的な美辞麗句、余計な前置きは使わない。'
                    '「'+label+'」の修正文のみ返し、解説・引用符・挨拶は付けない。'
                )
                def ask_ai(system_text):
                    payload={'model':os.environ.get('STUDIO_AI_MODEL','gpt-4.1-mini'),'messages':[
                        {'role':'system','content':system_text},
                        {'role':'user','content':original}], 'temperature':0.1}
                    req=urllib.request.Request('https://api.openai.com/v1/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
                    with urllib.request.urlopen(req,timeout=45) as response: result=json.load(response)
                    return result['choices'][0]['message']['content'].strip()
                edited=ask_ai(instructions)
                if not edited: raise ValueError('AIの出力が空でした')
                # A short original may benefit from a slightly longer, clearer draft.
                # Preserve the user's original for side-by-side review in the UI.
                if not edited.strip():
                    edited=original
                return self.send_json({'text':edited})
            except ValueError as exc: return self.send_json({'error':str(exc)},422)
            except Exception: return self.send_json({'error':'AI文章修正に失敗しました。元の文章は変更していません。'},502)
        if path == '/api/ai/polish':
            if not current_user(self.headers.get('Cookie','')): return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            if origin and origin not in ('https://'+self.headers.get('Host',''),'http://localhost:'+str(PORT)):
                return self.send_json({'error':'origin mismatch'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=150000: raise ValueError('入力が大きすぎます')
                body=json.loads(self.rfile.read(size))
                ideas=body.get('ideas')
                if not isinstance(ideas,list) or not 1<=len(ideas)<=20: raise ValueError('1〜20件の企画を選択してください')
                for idea in ideas:
                    if not isinstance(idea,dict) or any(not isinstance(idea.get(k),str) for k in AI_FIELDS):
                        raise ValueError('企画データが不正です')
                return self.send_json({'ideas':polish_ideas(ideas)})
            except ValueError as exc: return self.send_json({'error':str(exc)},422)
            except Exception: return self.send_json({'error':'AI文章整形に失敗しました。登録は行っていません。'},502)
        if path == '/api/extract-pdf':
            if not current_user(self.headers.get('Cookie', '')): return self.send_json({'error':'ログインが必要です'},401)
            origin = self.headers.get('Origin', '')
            if origin and origin not in ('https://' + self.headers.get('Host',''), 'http://localhost:' + str(PORT)):
                return self.send_json({'error':'origin mismatch'},403)
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 12*1024*1024: raise ValueError('PDFは12MB以下にしてください')
                data = self.rfile.read(size)
                if not data.startswith(b'%PDF-'): raise ValueError('PDFファイルではありません')
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data), strict=False)
                if len(reader.pages)>60: raise ValueError('PDFは60ページ以下にしてください')
                texts = [(page.extract_text(extraction_mode='layout') or '') for page in reader.pages]
                text = '\n\n'.join(texts)
                if not text.strip(): raise ValueError('文字を抽出できませんでした。画像だけのPDFはテキストを貼り付けてください')
                return self.send_json({'text':text[:180000],'pages':len(reader.pages)})
            except Exception as e: return self.send_json({'error':str(e)},400)
        if path != '/api/login': return self.send_json({'error': 'not found'}, 404)
        if self.headers.get('Origin') and self.headers['Origin'] != 'https://' + self.headers.get('Host', '') and self.headers['Origin'] != 'http://localhost:' + str(PORT):
            return self.send_json({'error': 'origin mismatch'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 20000: raise ValueError('認証情報が不正です')
            body = json.loads(self.rfile.read(length))
            email = verify_token(body.get('credential', ''))
            session = encode_session(email)
            result = b'{"ok":true}'
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', 'studio_session=' + session + '; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=28800')
            self.send_header('Content-Length', str(len(result))); self.end_headers(); self.wfile.write(result)
        except Exception as e: self.send_json({'error': str(e)}, 403)

if __name__ == '__main__':
    print('108 STUDIO listening on', PORT, 'configured:', configured(), flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
