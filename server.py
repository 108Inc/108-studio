"""108 STUDIO prototype: Google ID token login with email allowlist."""
import os, json, time, hmac, hashlib, base64, http.cookies, urllib.request, urllib.parse, io, re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import cloud_backend

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

LOGIN = '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>108 STUDIO | ログイン</title><script src="https://accounts.google.com/gsi/client" async defer></script><style>body{background:#f2f4f7;color:#18233a;font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0}.box{background:white;border:1px solid #e2e5ec;border-radius:18px;padding:42px;max-width:420px;text-align:center;box-shadow:0 10px 50px #14203a10}h1{letter-spacing:.1em}p{color:#65718a;line-height:1.7}#btn{display:flex;justify-content:center;margin:26px 0}#msg{color:#b13046}</style><div class="box"><h1>108 STUDIO</h1><p>社内向け企画資料エディタ<br>許可されたGoogleアカウントでログインしてください。</p><div id="btn"></div><p id="msg"></p></div><script>const cid=__CLIENT_ID__;const msg=document.getElementById('msg');if(!cid){msg.textContent='管理者によるログイン設定が必要です'}else{window.onload=()=>{google.accounts.id.initialize({client_id:cid,callback:async r=>{try{const res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:r.credential})});const j=await res.json();if(!res.ok)throw Error(j.error||'ログイン失敗');location.href='/'}catch(e){msg.textContent=e.message}}});google.accounts.id.renderButton(document.getElementById('btn'),{theme:'outline',size:'large',text:'signin_with',width:290})}}</script></html>'''

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
        if path == '/api/studio/status': return self.send_json({'configured':cloud_backend.ready(),'email':user})
        if path == '/api/studio/documents':
            try: return self.send_json({'documents':cloud_backend.documents()})
            except Exception as e: return self.send_json({'error':str(e)},502)
        if path.startswith('/api/studio/documents/'):
            parts=path.strip('/').split('/')
            if len(parts) not in (4,5): return self.send_json({'error':'not found'},404)
            try:
                import uuid
                doc_id=str(uuid.UUID(parts[3]))
                if len(parts)==5 and parts[4]=='revisions': return self.send_json({'revisions':cloud_backend.revisions(doc_id)})
                if len(parts)!=4: return self.send_json({'error':'not found'},404)
                doc=cloud_backend.document(doc_id)
                return self.send_json({'document':doc} if doc else {'error':'not found'},200 if doc else 404)
            except ValueError as e: return self.send_json({'error':str(e)},400)
            except Exception as e: return self.send_json({'error':str(e)},502)
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
        if path == '/api/studio/documents' or path.startswith('/api/studio/documents/'):
            user=current_user(self.headers.get('Cookie',''))
            if not user: return self.send_json({'error':'ログインが必要です'},401)
            origin=self.headers.get('Origin','')
            if origin and origin not in ('https://'+self.headers.get('Host',''),'http://localhost:'+str(PORT)):
                return self.send_json({'error':'origin mismatch'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4500000: return self.send_json({'error':'リクエストサイズが不正です'},413)
                data=json.loads(self.rfile.read(length))
                if path == '/api/studio/documents':
                    return self.send_json({'document':cloud_backend.create(data,user)},201)
                import uuid
                parts=path.strip('/').split('/')
                if len(parts)!=4: return self.send_json({'error':'not found'},404)
                doc_id=str(uuid.UUID(parts[3]))
                updated=cloud_backend.update(doc_id,data,user)
                if not updated: return self.send_json({'error':'conflict','document':cloud_backend.document(doc_id)},409)
                return self.send_json({'document':updated})
            except ValueError as e: return self.send_json({'error':str(e)},400)
            except Exception as e: return self.send_json({'error':'保存できませんでした: '+str(e)},502)
        if path == '/api/logout':
            self.send_response(200); self.send_header('Set-Cookie', 'studio_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'); self.end_headers(); return
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
            self.send_header('Set-Cookie', 'studio_session=' + session + '; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=28800')
            self.send_header('Content-Length', str(len(result))); self.end_headers(); self.wfile.write(result)
        except Exception as e: self.send_json({'error': str(e)}, 403)

if __name__ == '__main__':
    print('108 STUDIO listening on', PORT, 'configured:', configured(), flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
