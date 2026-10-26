# QuizMind AI — Flask starter project

A beginner-friendly quiz generator using Flask, SQLite, Gemini, and Gmail PDF reports.

## Features
- Email + password signup/login, with a show-password option.
- Passwords stored as secure hashes (never saved as plain text).
- Generate 5, 10, or 15 MCQs on any topic; Easy / Medium / Hard.
- Quick topic chips: AI/ML, DSA, Coding, History, Politics, GK, Science, Mathematics.
- Quiz history on the profile page.
- Answer review with green correct options, red selected-wrong options, and explanations.
- Download the complete quiz report as a PDF or email it to the signed-in user's email.
- Responsive dark interface.

## Run on Windows
1. Install Python 3.10 or newer.
2. Extract the ZIP and open this folder in VS Code or a terminal.
3. Create a virtual environment:
   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   ```
4. Install packages:
   ```bat
   pip install -r requirements.txt
   ```
5. Copy `.env.example` to `.env` and fill in your own keys.
6. Run:
   ```bat
   python app.py
   ```
7. Open http://127.0.0.1:5000

## Configure Gemini
- Create/get a key in Google AI Studio.
- Put it in `GEMINI_API_KEY` in `.env`.
- `GEMINI_MODEL` is configurable in the same file. This starter defaults to `gemini-2.5-flash`; use a model name actually enabled for your account. Model availability/names change, so don't assume a `3.8 Flash` model exists unless Google lists it for your account.

## Configure Gmail PDF reports
1. Turn on 2-Step Verification for the Gmail account used as sender.
2. Create a Google App Password.
3. Set `MAIL_SENDER` to the sender Gmail address and `MAIL_PASSWORD` to the App Password in `.env`.
4. Email reports are sent to the email address of the signed-in user.

## Important security notes
- Never upload or share your real `.env` file.
- `.env.example` contains placeholders only; no private API keys or passwords are included.
- Change `FLASK_SECRET_KEY` to a long random value.
- Keep `FLASK_DEBUG=false` outside local development.
- This is a learning project, not production-hardened. Before public deployment, add CSRF protection, rate limiting, stricter validation, secure cookie settings, and production hosting configuration.
- If the AI response fails JSON validation, retry generation. Model output can occasionally be malformed.


## New features in this update
- Admin link and password-protected dashboard (`/admin`) with registered-user names/emails and quiz totals. Set `ADMIN_PASSWORD` in Render Environment before using it.
- Forgot password by registered email, one-time code verification, and password reset. Set the existing `MAIL_SENDER`, `MAIL_PASSWORD`, `MAIL_SERVER`, and `MAIL_PORT` variables. OTP lifetime defaults to 10 minutes.
- Styled PDF report with score summary, per-question answer status, and explanations.
- Added Gunicorn dependency for Render. Existing Gemini model/API configuration is unchanged.

## Render environment settings
Keep your current Gemini settings exactly as they are. Add `ADMIN_PASSWORD` with a strong, unique value. Email uses SMTP settings already present; if sending still fails on Render, check deploy logs and confirm the provider allows SMTP from your hosting plan. Some free hosting plans block outbound SMTP, in which case a supported transactional email API/provider is needed.

**Privacy note:** The admin page exposes user names and email addresses, so keep `ADMIN_PASSWORD` secret. User passwords are stored as hashes and are not shown in admin.
