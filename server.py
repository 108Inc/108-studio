"""108 STUDIO prototype: Google ID token login with email allowlist."""
import os, json, time, hmac, hashlib, base64, http.cookies, urllib.request, urllib.parse, io, re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.getenv('PORT', '8765'))
CLIENT_ID = os.getenv('STUDIO_GOOGLE_CLIENT_ID', '').strip()
SECRET = os.getenv('STUDIO_SESSION_SECRET', '')
ALLOWED = {e.strip().lower() for e in os.getenv('STUDIO_ALLOWED_EMAILS', '').split(',') if e.strip()}

def configured():
    return bool(CLIENT_ID and len(SECRET) >= 32 and ALLOWED)

def sign(value):
    return hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()

def encode_session(email):
    payload = base64.urlsafe_b64encode(json.dumps({'email': email, 'exp': int(time.time()) + 28800}).encode()).decode().rstrip('=')
    return payload + '.' + sign(payload)

def current_user(cookie):
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
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/api/logout':
            self.send_response(200); self.send_header('Set-Cookie', 'studio_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0'); self.end_headers(); return
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
                    'あなたは108 STUDIOのSNS企画書の簡潔な編集者です。目的はクライアントが一読で企画を理解できる文章にすること。'
                    '文章を豪華にする、丁寧に膨らませる、背景説明を補うことは禁止。'
                    '原文より短く、端的で自然な日本語にする。原文がすでに明快ならほぼそのまま返す。'
                    '重複、冗長な修飾、不要な前置き、抽象的な美辞麗句を削る。'
                    '企画の核、面白さ、出演メンバー、音源、撮影手順、固有名詞、数値、URLなど必要な事実は保持する。'
                    '原文にない演出、狙い、提案、見出し、箇条書きを足さない。'
                    '「'+label+'」の文章のみを返し、解説・引用符・挨拶は付けない。'
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
                # Do not present an expanded draft: one stricter retry, then retain original.
                if len(edited)>len(original):
                    edited=ask_ai(instructions+'絶対条件：出力文字数は原文の'+str(len(original))+'文字以下。短縮できなければ原文をそのまま返す。')
                if not edited or len(edited)>len(original):
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
