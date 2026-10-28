108 STUDIO v0.5.7 | Mobile Google login fix (main)

Based on v0.5.6. Google login now checks session persistence before redirect, sends credentials explicitly, uses SameSite=Lax for the first-party session cookie, and shows an actionable error if a mobile browser blocks cookies. Existing editor, Slack, PDF, and document library remain unchanged.

Deploy the extracted contents to GitHub main. Verify login on iPhone Safari and Android Chrome, and confirm PC login still works.
