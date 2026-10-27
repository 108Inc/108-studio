# 108 STUDIO v0.1

Internal prototype of the SNS proposal editor.

## Files
- `server.py`: Python HTTP server, Google login and email allowlist
- `static/index.html`: editor with local browser storage and print-to-PDF
- `render.yaml`: Render web service blueprint
- `requirements.txt`: Python dependencies

## Render environment variables
- `STUDIO_GOOGLE_CLIENT_ID`: Google OAuth web client ID (configure the Render origin in Google Cloud)
- `STUDIO_SESSION_SECRET`: generated random secret, at least 32 characters
- `STUDIO_ALLOWED_EMAILS`: comma-separated list of employee email addresses

**Important:** This is a prototype. Data is saved to localStorage in each user's browser, not shared or durably backed up. Avoid real confidential project content until persistent server-side storage and backup are implemented. Google login is required to access the app but does not encrypt local browser data. The PDF uses the browser's Print -> Save as PDF. The visual design is provisional, not a verified reproduction of the Canva template.

## Deployment
1. Upload `server.py`, `render.yaml`, `requirements.txt`, and the `static` folder to a new GitHub repository (replace README if desired).
2. In Render, create **Blueprint** from this repository, or create **Web Service** with Python runtime, `pip install -r requirements.txt`, `python server.py`.
3. Set `STUDIO_GOOGLE_CLIENT_ID`, `STUDIO_ALLOWED_EMAILS`, and `STUDIO_SESSION_SECRET` in Render environment settings.
4. In Google Cloud OAuth client configuration, register `https://YOUR-SERVICE.onrender.com` under Authorized JavaScript origins. The client ID can be reused from 108 DOJO only if the same origin is added to its configuration.
5. Deploy and sign in with an allowed Google account.

Do not copy DOJO Google refresh tokens or Slack API tokens into this service.
