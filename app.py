"""QuizMind AI — simple Flask quiz app.
Keep API keys and email credentials in .env; do not put secrets in source code.
"""
import os, json, io, smtplib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from email.message import EmailMessage
from functools import wraps
from xml.sax.saxutils import escape

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch

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
    created_at = db.Column(db.DateTime,default=lambda: datetime.now(timezone.utc))

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
    """Create a PDF report safely, even if AI text contains HTML/XML symbols."""
    questions = json.loads(quiz.questions_json)
    answers = json.loads(quiz.answers_json or "{}")

    memory = io.BytesIO()
    doc = SimpleDocTemplate(
        memory, pagesize=A4,
        rightMargin=42, leftMargin=42,
        topMargin=42, bottomMargin=42
    )
    styles = getSampleStyleSheet()

    # Escape AI-generated text so symbols like < and & cannot break the PDF.
    safe = lambda value: escape(str(value))

    story = [
        Paragraph("QuizMind AI - Quiz Report", styles["Title"]),
        Paragraph(f"Name: {safe(user.name)} | Email: {safe(user.email)}",
                  styles["Normal"]),
        Paragraph(
            f"Topic: {safe(quiz.topic)} | Difficulty: {safe(quiz.difficulty.title())}",
            styles["Normal"]
        ),
        Paragraph(f"Score: {quiz.score}/{quiz.total}", styles["Heading2"]),
        Spacer(1, 12)
    ]

    for i, q in enumerate(questions):
        chosen = answers.get(str(i), None)
        correct_index = q["answer"]
        is_right = chosen is not None and int(chosen) == correct_index

        story.append(Paragraph(
            f"{i + 1}. {safe(q['question'])}", styles["Heading3"]
        ))

        if chosen is None:
            your_answer = "Not answered"
        else:
            your_answer = q["options"][int(chosen)]

        status = "CORRECT" if is_right else "INCORRECT"
        story.append(Paragraph(
            f"Your answer: {safe(your_answer)} - {status}",
            styles["Normal"]
        ))
        story.append(Paragraph(
            f"Correct answer: {safe(q['options'][correct_index])}",
            styles["Normal"]
        ))

        options_text = " | ".join(str(opt) for opt in q["options"])
        story.append(Paragraph(
            f"Options: {safe(options_text)}", styles["Normal"]
        ))
        story.append(Paragraph(
            f"Explanation: {safe(q['explanation'])}",
            styles["BodyText"]
        ))
        story.append(Spacer(1, 10))

    doc.build(story)
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

@app.context_processor
def inject_timezone():
    return {
        "timezone": timezone,
        "ZoneInfo": ZoneInfo
    }

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
