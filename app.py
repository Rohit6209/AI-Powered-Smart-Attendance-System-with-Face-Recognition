import os, io, csv, base64, sqlite3, pickle, smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps

import cv2
import numpy as np
import face_recognition
from flask import Flask, request, jsonify, session, Response
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))


def load_env(path=os.path.join(BASE, ".env")):
    """Load KEY=VALUE lines from .env (real environment variables win)."""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_secret_key():
    """SECRET_KEY from env, else a random key generated once and saved in secret.key."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(os.urandom(32).hex())
    return open(path).read().strip()


load_env()
app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = get_secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DB = os.environ.get("DATABASE", os.path.join(BASE, "attendance.db"))
TOLERANCE = 0.5        # lower = stricter matching
EAR_CLOSED = 0.21      # eye aspect ratio: below this = eye closed
SMTP_HOST = os.environ.get("SMTP_HOST")          # e.g. smtp.gmail.com
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")          # Gmail App Password


# ---------------- Database ----------------
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT);
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY, roll TEXT UNIQUE, name TEXT, email TEXT,
        parent_email TEXT, encoding BLOB, password TEXT);
    CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY, student_id INTEGER, subject TEXT,
        date TEXT, time TEXT, UNIQUE(student_id, subject, date));
    """)
    cols = [r[1] for r in con.execute("PRAGMA table_info(students)")]
    if "password" not in cols:
        con.execute("ALTER TABLE students ADD COLUMN password TEXT")
    if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        con.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)",
                    ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123")), "admin"))
    con.commit()
    con.close()


# ---------------- Auth ----------------
def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            if "user" not in session:
                return jsonify(error="Login required"), 401
            if roles and session["role"] not in roles:
                return jsonify(error="Permission denied"), 403
            return fn(*a, **kw)
        return wrap
    return deco


@app.post("/api/login")
def login():
    d = request.json
    con = db()
    u = con.execute("SELECT * FROM users WHERE username=?", (d.get("username"),)).fetchone()
    con.close()
    if not u or not check_password_hash(u["password"], d.get("password", "")):
        return jsonify(error="Wrong username or password"), 401
    session["user"], session["role"] = u["username"], u["role"]
    return jsonify(user=u["username"], role=u["role"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    if session.get("role") not in ("admin", "teacher"):
        return jsonify(error="Not logged in"), 401
    return jsonify(user=session["user"], role=session["role"])


@app.post("/api/teachers")
@login_required("admin")
def add_teacher():
    d = request.json
    try:
        con = db()
        con.execute("INSERT INTO users(username,password,role) VALUES(?,?,'teacher')",
                    (d["username"], generate_password_hash(d["password"])))
        con.commit(); con.close()
    except sqlite3.IntegrityError:
        return jsonify(error="Username already exists"), 400
    return jsonify(ok=True)


# ---------------- Face helpers ----------------
def decode(b64):
    raw = base64.b64decode(b64.split(",")[-1])
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def ear(eye):
    p = [np.array(x) for x in eye]
    return (np.linalg.norm(p[1] - p[5]) + np.linalg.norm(p[2] - p[4])) / (2.0 * np.linalg.norm(p[0] - p[3]))


def biggest_face(img):
    locs = face_recognition.face_locations(img, model="hog")
    if not locs:
        return None
    return max(locs, key=lambda l: (l[2] - l[0]) * (l[1] - l[3]))


@app.post("/api/students")
@login_required("admin", "teacher")
def register_student():
    d = request.json
    encs = []
    for f in d.get("frames", []):
        img = decode(f)
        loc = biggest_face(img)
        if loc:
            encs.append(face_recognition.face_encodings(img, [loc])[0])
    if len(encs) < 3:
        return jsonify(error="Could not capture a clear face. Look at the camera in good light and try again."), 400
    mean_enc = np.mean(encs, axis=0)
    try:
        con = db()
        pw = (d.get("password") or d["roll"]).strip()   # if empty, the roll number is the password
        con.execute("INSERT INTO students(roll,name,email,parent_email,encoding,password) VALUES(?,?,?,?,?,?)",
                    (d["roll"], d["name"], d.get("email", ""), d.get("parent_email", ""),
                     pickle.dumps(mean_enc), generate_password_hash(pw)))
        con.commit(); con.close()
    except sqlite3.IntegrityError:
        return jsonify(error="A student with this roll number is already registered"), 400
    return jsonify(ok=True, faces_used=len(encs))


@app.get("/api/students")
@login_required("admin", "teacher")
def list_students():
    con = db()
    rows = con.execute("SELECT id,roll,name,email,parent_email FROM students ORDER BY roll").fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/students/<int:sid>")
@login_required("admin")
def delete_student(sid):
    con = db()
    con.execute("DELETE FROM attendance WHERE student_id=?", (sid,))
    con.execute("DELETE FROM students WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify(ok=True)


# ---------------- Recognition + liveness ----------------
@app.post("/api/recognize")
@login_required("admin", "teacher")
def recognize():
    d = request.json
    subject = (d.get("subject") or "").strip()
    frames = d.get("frames", [])
    if not subject:
        return jsonify(status="error", message="Enter a subject first"), 400

    ears, last_img, last_loc = [], None, None
    for f in frames:
        img = decode(f)
        loc = biggest_face(img)
        if not loc:
            continue
        lm = face_recognition.face_landmarks(img, [loc])[0]
        ears.append((ear(lm["left_eye"]) + ear(lm["right_eye"])) / 2)
        last_img, last_loc = img, loc

    if len(ears) < 3:
        return jsonify(status="noface", message="No face detected")

    # Liveness: a blink means the eyes are seen both open and closed
    if not (min(ears) < EAR_CLOSED and max(ears) > EAR_CLOSED + 0.04):
        return jsonify(status="spoof", message="No blink detected. Attendance from a photo or screen is not allowed.")

    enc = face_recognition.face_encodings(last_img, [last_loc])[0]
    con = db()
    studs = con.execute("SELECT id,roll,name,encoding FROM students").fetchall()
    if not studs:
        con.close()
        return jsonify(status="error", message="No students are registered yet")
    known = [pickle.loads(s["encoding"]) for s in studs]
    dist = face_recognition.face_distance(known, enc)
    i = int(np.argmin(dist))
    if dist[i] > TOLERANCE:
        con.close()
        return jsonify(status="unknown", message="Face not recognised. Register this student first.")

    s, now = studs[i], datetime.now()
    cur = con.execute("INSERT OR IGNORE INTO attendance(student_id,subject,date,time) VALUES(?,?,?,?)",
                      (s["id"], subject, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")))
    con.commit(); con.close()
    return jsonify(status="marked" if cur.rowcount else "already",
                   name=s["name"], roll=s["roll"], confidence=round((1 - float(dist[i])) * 100, 1),
                   message=f"{s['name']} ({s['roll']}) - " + ("Marked present" if cur.rowcount else "Already marked today"))


# ---------------- Reports ----------------
def fetch_log(date="", subject=""):
    q = """SELECT a.date,a.time,a.subject,s.roll,s.name FROM attendance a
           JOIN students s ON s.id=a.student_id WHERE 1=1"""
    args = []
    if date:
        q += " AND a.date=?"; args.append(date)
    if subject:
        q += " AND a.subject=?"; args.append(subject)
    con = db()
    rows = con.execute(q + " ORDER BY a.date DESC, a.time DESC", args).fetchall()
    con.close()
    return [dict(r) for r in rows]


def summary_data():
    con = db()
    total = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT date,subject FROM attendance)").fetchone()[0]
    rows = con.execute("""SELECT s.id,s.roll,s.name,s.email,s.parent_email,COUNT(a.id) present
        FROM students s LEFT JOIN attendance a ON a.student_id=s.id GROUP BY s.id ORDER BY s.roll""").fetchall()
    trend = []
    for k in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=k)).strftime("%Y-%m-%d")
        c = con.execute("SELECT COUNT(*) FROM attendance WHERE date=?", (day,)).fetchone()[0]
        trend.append({"date": day[5:], "count": c})
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["total"] = total
        d["percent"] = round(r["present"] * 100 / total, 1) if total else 0
        out.append(d)
    return out, trend


@app.get("/api/attendance")
@login_required("admin", "teacher")
def attendance():
    return jsonify(fetch_log(request.args.get("date", ""), request.args.get("subject", "")))


@app.get("/api/summary")
@login_required("admin", "teacher")
def summary():
    students, trend = summary_data()
    return jsonify(students=students, trend=trend)


@app.get("/api/export")
@login_required("admin", "teacher")
def export_csv():
    rows = fetch_log(request.args.get("date", ""), request.args.get("subject", ""))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Time", "Subject", "Roll", "Name"])
    for r in rows:
        w.writerow([r["date"], r["time"], r["subject"], r["roll"], r["name"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=attendance.csv"})


# ---------------- Low attendance alerts ----------------
def send_mail(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_USER, to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(SMTP_HOST, 465) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


@app.post("/api/alerts")
@login_required("admin", "teacher")
def alerts():
    limit = float(request.json.get("threshold", 75))
    students, _ = summary_data()
    low = [s for s in students if s["total"] and s["percent"] < limit]
    sent, errors = 0, []
    for s in low:
        body = (f"Dear {s['name']} ({s['roll']}),\n\nYour attendance is {s['percent']}% "
                f"({s['present']}/{s['total']} classes), below the required {limit:.0f}%.\n"
                "Please attend classes regularly.\n\n- College Attendance System")
        for to in filter(None, [s["email"], s["parent_email"]]):
            if not SMTP_HOST:
                continue
            try:
                send_mail(to, "Low Attendance Alert", body); sent += 1
            except Exception as e:
                errors.append(f"{to}: {e}")
    return jsonify(low=[f"{s['name']} ({s['percent']}%)" for s in low], emails_sent=sent,
                   smtp_configured=bool(SMTP_HOST), errors=errors)


@app.post("/api/students/<int:sid>/password")
@login_required("admin", "teacher")
def reset_student_password(sid):
    new = (request.json.get("password") or "").strip()
    if len(new) < 4:
        return jsonify(error="Password must be at least 4 characters"), 400
    con = db()
    con.execute("UPDATE students SET password=? WHERE id=?", (generate_password_hash(new), sid))
    con.commit(); con.close()
    return jsonify(ok=True)


# ---------------- Student portal APIs (own data only) ----------------
def student_only(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if session.get("role") != "student":
            return jsonify(error="Student login required"), 401
        return fn(*a, **kw)
    return wrap


@app.post("/api/student/login")
def student_login():
    d = request.json
    con = db()
    s = con.execute("SELECT * FROM students WHERE roll=?", ((d.get("roll") or "").strip(),)).fetchone()
    con.close()
    if not s or not s["password"] or not check_password_hash(s["password"], d.get("password", "")):
        return jsonify(error="Wrong roll number or password"), 401
    session.clear()
    session["user"], session["role"], session["sid"] = s["roll"], "student", s["id"]
    return jsonify(ok=True)


@app.get("/api/student/me")
@student_only
def student_me():
    sid = session["sid"]
    con = db()
    s = con.execute("SELECT roll,name,email,parent_email FROM students WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close(); session.clear()
        return jsonify(error="Student not found"), 401
    total = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT date,subject FROM attendance)").fetchone()[0]
    present = con.execute("SELECT COUNT(*) FROM attendance WHERE student_id=?", (sid,)).fetchone()[0]
    tot_sub = {r["subject"]: r["c"] for r in con.execute(
        "SELECT subject, COUNT(DISTINCT date) c FROM attendance GROUP BY subject")}
    pres_sub = {r["subject"]: r["c"] for r in con.execute(
        "SELECT subject, COUNT(*) c FROM attendance WHERE student_id=? GROUP BY subject", (sid,))}
    con.close()
    subjects = [{"subject": k, "present": pres_sub.get(k, 0), "total": v,
                 "percent": round(pres_sub.get(k, 0) * 100 / v, 1)} for k, v in sorted(tot_sub.items())]
    return jsonify(profile=dict(s), present=present, total=total,
                   percent=round(present * 100 / total, 1) if total else 0, subjects=subjects)


def own_log(sid, subject=""):
    q = "SELECT date,time,subject FROM attendance WHERE student_id=?"
    args = [sid]
    if subject:
        q += " AND subject=?"; args.append(subject)
    con = db()
    rows = con.execute(q + " ORDER BY date DESC, time DESC", args).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/student/attendance")
@student_only
def student_attendance():
    return jsonify(own_log(session["sid"], request.args.get("subject", "")))


@app.get("/api/student/export")
@student_only
def student_export():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Time", "Subject"])
    for r in own_log(session["sid"], request.args.get("subject", "")):
        w.writerow([r["date"], r["time"], r["subject"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=my_attendance.csv"})


@app.post("/api/student/password")
@student_only
def student_password():
    d = request.json
    new = d.get("new", "")
    if len(new) < 6:
        return jsonify(error="New password must be at least 6 characters"), 400
    con = db()
    s = con.execute("SELECT password FROM students WHERE id=?", (session["sid"],)).fetchone()
    if not s or not check_password_hash(s["password"], d.get("old", "")):
        con.close()
        return jsonify(error="Old password is incorrect"), 400
    con.execute("UPDATE students SET password=? WHERE id=?", (generate_password_hash(new), session["sid"]))
    con.commit(); con.close()
    return jsonify(ok=True)


# ---------------- Pages ----------------
@app.get("/")
def student_page():
    return app.send_static_file("student.html")


@app.get("/admin")
def admin_page():
    return app.send_static_file("admin.html")


init_db()  # runs for every server (waitress, gunicorn, flask)

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)  # development only; use server.py otherwise
