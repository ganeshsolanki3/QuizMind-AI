"""QuizMind AI — simple Flask quiz app.
Keep API keys and email credentials in .env; do not put secrets in source code.
"""
import os, json, io, smtplib, secrets, hashlib, html
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Table, TableStyle, KeepTogether
from reportlab.lib.enums import TA_CENTER, TA_LEFT

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-development-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///quizmind.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Central configuration: change the model or credentials in .env only.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAIL_SENDER = os.getenv("MAIL_SENDER", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
OTP_TTL_MINUTES = int(os.getenv("OTP_TTL_MINUTES", "10"))

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    quizzes = db.relationship("Quiz", backref="owner", lazy=True, cascade="all, delete-orphan")

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    questions_json = db.Column(db.Text, nullable=False)
    answers_json = db.Column(db.Text, nullable=False, default="{}")
    score = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def current_user():
    return db.session.get(User, session.get("user_id"))

def make_pdf(quiz, user):
    questions = json.loads(quiz.questions_json)
    answers = json.loads(quiz.answers_json or "{}")
    memory = io.BytesIO()
    doc = SimpleDocTemplate(memory, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=36, bottomMargin=40,
                            title=f"QuizMind AI Report - {quiz.topic}", author="QuizMind AI")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=24, leading=29, textColor=colors.HexColor("#10233f"), alignment=TA_LEFT, spaceAfter=8))
    styles.add(ParagraphStyle(name="Sub", parent=styles["Normal"], fontSize=10, leading=15, textColor=colors.HexColor("#52627a"), spaceAfter=5))
    styles.add(ParagraphStyle(name="Question", parent=styles["Heading3"], fontSize=12, leading=16, textColor=colors.HexColor("#142b4a"), spaceAfter=7))
    styles.add(ParagraphStyle(name="Answer", parent=styles["BodyText"], fontSize=9, leading=13, textColor=colors.HexColor("#26364c"), spaceAfter=4))
    styles.add(ParagraphStyle(name="Explain", parent=styles["BodyText"], fontSize=9, leading=13, textColor=colors.HexColor("#52627a"), leftIndent=8, spaceBefore=3, spaceAfter=4))
    esc = lambda x: html.escape(str(x))
    pct = round((quiz.score / quiz.total) * 100) if quiz.total else 0
    story = [Paragraph("QUIZMIND AI", styles["Sub"]), Paragraph("Your Quiz Report", styles["CoverTitle"]),
             Paragraph(f"{esc(quiz.topic)}  ·  {esc(quiz.difficulty.title())} difficulty", styles["Sub"]), Spacer(1, 10)]
    summary = [[Paragraph(f"<b>{quiz.score}/{quiz.total}</b><br/><font size='8'>CORRECT</font>", styles["Answer"]),
                Paragraph(f"<b>{pct}%</b><br/><font size='8'>SCORE</font>", styles["Answer"]),
                Paragraph(f"<b>{len(questions)}</b><br/><font size='8'>QUESTIONS</font>", styles["Answer"]),
                Paragraph(f"<b>{esc(user.name)}</b><br/><font size='8'>LEARNER</font>", styles["Answer"])]]
    t = Table(summary, colWidths=[doc.width/4]*4)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#eaf4ff")),("BOX",(0,0),(-1,-1),0.7,colors.HexColor("#c5d8ee")),("INNERGRID",(0,0),(-1,-1),0.5,colors.white),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12)]))
    story += [t, Spacer(1, 12), Paragraph(f"Prepared for {esc(user.name)} ({esc(user.email)}) · {quiz.created_at.strftime('%d %b %Y, %I:%M %p') if quiz.created_at else ''}", styles["Sub"]), Spacer(1, 14)]
    for i, q in enumerate(questions):
        chosen = answers.get(str(i), None)
        correct_idx = q.get("answer", 0)
        correct = q.get("options", [])[correct_idx] if 0 <= correct_idx < len(q.get("options", [])) else "Unavailable"
        chosen_text = "Not answered" if chosen is None else (q["options"][int(chosen)] if str(chosen).isdigit() and 0 <= int(chosen) < len(q["options"]) else "Not answered")
        is_right = chosen is not None and str(chosen).isdigit() and int(chosen) == correct_idx
        status_color = "#147d50" if is_right else "#bd3446"
        block = [Paragraph(f"QUESTION {i+1:02d}", styles["Sub"]), Paragraph(esc(q.get("question", "")), styles["Question"]),
                 Paragraph(f"<b>Your answer:</b> {esc(chosen_text)}  <font color='{status_color}'><b>{'CORRECT' if is_right else 'INCORRECT'}</b></font>", styles["Answer"]),
                 Paragraph(f"<b>Correct answer:</b> {esc(correct)}", styles["Answer"]),
                 Paragraph(f"<b>Explanation:</b> {esc(q.get('explanation', 'No explanation provided.'))}", styles["Explain"]), Spacer(1, 8)]
        card = Table([[block]], colWidths=[doc.width])
        card.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#f7f9fc")),("BOX",(0,0),(-1,-1),0.6,colors.HexColor("#dbe4ef")),("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
        story.extend([KeepTogether(card), Spacer(1, 9)])
    def footer(canvas, document):
        canvas.saveState(); canvas.setStrokeColor(colors.HexColor("#dbe4ef")); canvas.line(42, 28, A4[0]-42, 28)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(colors.HexColor("#6b7890")); canvas.drawString(42, 16, "QuizMind AI · Learn something new, one quiz at a time")
        canvas.drawRightString(A4[0]-42, 16, f"Page {document.page}"); canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    memory.seek(0)
    return memory

def send_report_email(quiz, user):
    if not (MAIL_SENDER and MAIL_PASSWORD):
        raise RuntimeError("Email is not configured. Add MAIL_SENDER and MAIL_PASSWORD to .env.")
    pdf = make_pdf(quiz, user)
    msg = EmailMessage()
    msg["Subject"] = f"Your QuizMind report: {quiz.topic} ({quiz.score}/{quiz.total})"
    msg["From"] = MAIL_SENDER
    msg["To"] = user.email
    msg.set_content(f"Hi {user.name},\\n\\nAttached is your quiz report with answers, correct options, explanations and score.\\n\\n— QuizMind AI")
    msg.add_attachment(pdf.read(), maintype="application", subtype="pdf", filename="quiz_report.pdf")
    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
        server.starttls()
        server.login(MAIL_SENDER, MAIL_PASSWORD)
        server.send_message(msg)

@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("home.html", user=current_user())

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name, email, password = (request.form.get(k, "").strip() for k in ("name", "email", "password"))
        if not name or not email or len(password) < 8:
            flash("Enter all fields. Password must be at least 8 characters.")
        elif User.query.filter_by(email=email.lower()).first():
            flash("An account with this email already exists.")
        else:
            user = User(name=name, email=email.lower(), password_hash=generate_password_hash(password))
            db.session.add(user); db.session.commit()
            session["user_id"] = user.id
            return redirect(url_for("home"))
    return render_template("auth.html", mode="signup")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            session["user_id"] = user.id
            return redirect(url_for("home"))
        flash("Incorrect email or password.")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("If that email is registered, a verification code will be sent.")
            return redirect(url_for("forgot_password"))
        otp = f"{secrets.randbelow(1000000):06d}"
        session["reset_email"] = email
        session["reset_otp_hash"] = hashlib.sha256(otp.encode()).hexdigest()
        session["reset_otp_expires"] = (datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)).timestamp()
        session["reset_verified"] = False
        try:
            msg = EmailMessage(); msg["Subject"] = "QuizMind AI password reset code"; msg["From"] = MAIL_SENDER; msg["To"] = email
            msg.set_content(f"Your QuizMind AI password reset code is {otp}. It expires in {OTP_TTL_MINUTES} minutes. If you did not request this, ignore this email.")
            if MAIL_PORT == 465:
                with smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=20) as server:
                    server.login(MAIL_SENDER, MAIL_PASSWORD); server.send_message(msg)
            else:
                with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=20) as server:
                    server.starttls(); server.login(MAIL_SENDER, MAIL_PASSWORD); server.send_message(msg)
            flash("Verification code sent. Check your email and spam folder.")
            return redirect(url_for("verify_reset_otp"))
        except Exception:
            app.logger.exception("Password reset email failed")
            flash("Could not send the code. Email service may be blocked or misconfigured; try again later.")
    return render_template("forgot_password.html")

@app.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    if not session.get("reset_email") or not session.get("reset_otp_hash"):
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        if datetime.utcnow().timestamp() > session.get("reset_otp_expires", 0):
            session.pop("reset_otp_hash", None); flash("Code expired. Request a new one."); return redirect(url_for("forgot_password"))
        if secrets.compare_digest(hashlib.sha256(otp.encode()).hexdigest(), session.get("reset_otp_hash", "")):
            session["reset_verified"] = True
            return redirect(url_for("reset_password"))
        flash("Incorrect verification code.")
    return render_template("verify_otp.html")

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if not session.get("reset_verified") or not session.get("reset_email"):
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif password != confirm:
            flash("Passwords do not match.")
        else:
            user = User.query.filter_by(email=session["reset_email"]).first()
            if not user:
                flash("Account not found. Please sign up again."); return redirect(url_for("signup"))
            user.password_hash = generate_password_hash(password); db.session.commit()
            for key in ("reset_email", "reset_otp_hash", "reset_otp_expires", "reset_verified"):
                session.pop(key, None)
            session.pop("user_id", None)
            flash("Password changed successfully. Please log in.")
            return redirect(url_for("login"))
    return render_template("reset_password.html")

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        if ADMIN_PASSWORD and secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect admin password or ADMIN_PASSWORD is not configured on Render.")
    return render_template("admin_login.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    users = User.query.order_by(User.id.desc()).all()
    quizzes = Quiz.query.count()
    return render_template("admin_dashboard.html", users=users, total_users=len(users), total_quizzes=quizzes,
                           completed_quizzes=Quiz.query.filter(Quiz.answers_json != "{}").count())

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    topic = request.form.get("topic", "").strip()
    difficulty = request.form.get("difficulty", "medium").lower()
    try:
        count = int(request.form.get("count", "5"))
    except ValueError:
        count = 5
    if not topic or count not in (5, 10, 15) or difficulty not in ("easy", "medium", "hard"):
        flash("Enter a topic and select valid quiz settings.")
        return redirect(url_for("home"))
    if not GEMINI_API_KEY:
        flash("Gemini API key is missing. Add GEMINI_API_KEY to .env.")
        return redirect(url_for("home"))
    prompt = """Create exactly COUNT multiple-choice quiz questions about: TOPIC.
Difficulty: DIFFICULTY. Make the questions distinct and appropriate for that difficulty.
Return ONLY valid JSON object with this shape:
{"questions":[{"question":"...","options":["A...","B...","C...","D..."],"answer":0,"explanation":"..."}]}
answer must be a zero-based integer index from 0 to 3. Exactly four options per question.
No markdown fences. Make explanations educational and concise."""
    prompt = prompt.replace("COUNT", str(count)).replace("TOPIC", topic).replace("DIFFICULTY", difficulty)
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        questions = data["questions"]
        if len(questions) != count:
            raise ValueError("The model returned the wrong number of questions.")
        for q in questions:
            if not isinstance(q.get("answer"), int) or len(q.get("options", [])) != 4:
                raise ValueError("The generated question format was invalid.")
        quiz = Quiz(user_id=session["user_id"], topic=topic, difficulty=difficulty,
                    questions_json=json.dumps(questions), answers_json="{}",
                    score=0, total=count)
        db.session.add(quiz); db.session.commit()
        return redirect(url_for("take_quiz", quiz_id=quiz.id))
    except Exception as exc:
        app.logger.exception("Quiz generation failed")
        flash("Quiz generation failed. Check your API key/model and try again. Details: " + str(exc)[:180])
        return redirect(url_for("home"))

@app.route("/quiz/<int:quiz_id>", methods=["GET", "POST"])
@login_required
def take_quiz(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=session["user_id"]).first_or_404()
    questions = json.loads(quiz.questions_json)
    if request.method == "POST":
        answers = {}
        score = 0
        for i, q in enumerate(questions):
            val = request.form.get(f"q{i}", "")
            if val.isdigit() and 0 <= int(val) < 4:
                answers[str(i)] = int(val)
                score += int(int(val) == q["answer"])
        quiz.answers_json = json.dumps(answers)
        quiz.score = score
        db.session.commit()
        return redirect(url_for("report", quiz_id=quiz.id))
    return render_template("quiz.html", quiz=quiz, questions=questions)

@app.route("/report/<int:quiz_id>")
@login_required
def report(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=session["user_id"]).first_or_404()
    questions = json.loads(quiz.questions_json)
    answers = json.loads(quiz.answers_json or "{}")
    return render_template("report.html", quiz=quiz, questions=questions, answers=answers)

@app.route("/email-report/<int:quiz_id>", methods=["POST"])
@login_required
def email_report(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=session["user_id"]).first_or_404()
    try:
        send_report_email(quiz, current_user())
        flash("PDF report emailed to your account email.")
    except Exception as exc:
        flash("Email failed: " + str(exc)[:180])
    return redirect(url_for("report", quiz_id=quiz.id))

@app.route("/download-report/<int:quiz_id>")
@login_required
def download_report(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=session["user_id"]).first_or_404()
    return send_file(make_pdf(quiz, current_user()), as_attachment=True,
                     download_name="quiz_report.pdf", mimetype="application/pdf")

@app.route("/profile")
@login_required
def profile():
    user = current_user()
    quizzes = Quiz.query.filter_by(user_id=user.id).order_by(Quiz.created_at.desc()).all()
    return render_template("profile.html", user=user, quizzes=quizzes)

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
