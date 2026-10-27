"""108 STUDIO prototype: Google ID token login with email allowlist."""
import os, json, time, hmac, hashlib, base64, http.cookies, urllib.request, urllib.parse
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
        if path == '/': self.path = '/index.html'
        return super().do_GET()
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/api/logout':
            self.send_response(200); self.send_header('Set-Cookie', 'studio_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'); self.end_headers(); return
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
