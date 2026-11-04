import threading
import csv
import hmac
import io
import os
import calendar as cal
from datetime import datetime, date, time as dtime, timedelta, timezone
from functools import wraps
import smtplib
from email.mime.text import MIMEText

import psycopg2
from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, session, url_for)

from werkzeug.security import check_password_hash, generate_password_hash

from connection import get_db_connection

app = Flask(__name__)
app.secret_key = os.environ.get("SMS_SECRET_KEY", "change-this-secret-key")

IST = timezone(timedelta(hours=5, minutes=30))
LEAVE_TYPES = {"Sick Leave", "Casual Leave"}

# Email Settings - Auto-clean any accidental spaces
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "").strip()  # Remove ALL spaces

# ---------------------------------------------------------------- helpers
def now_ist():
    return datetime.now(IST).replace(tzinfo=None)

def get_conn():
    """One database connection per request, reused by every query in it."""
    if "db" not in g:
        g.db = get_db_connection()
    return g.db

@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def run(sql, params=(), fetch=None):
    conn = get_conn()
    if conn is None:
        raise RuntimeError("Database is unavailable. Please try again later.")
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        conn.commit()
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise

def run_insert(sql, params=()):
    conn = get_db_connection()
    if conn is None:
        raise RuntimeError("Database is unavailable. Please try again later.")
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        conn.close()

def fmt_time(dt):
    return dt.strftime("%I:%M:%S %p").lower() if dt else "--:--:--"

def fmt_duration(start, end):
    if not start or not end:
        return "00:00:00"
    secs = max(int((end - start).total_seconds()), 0)
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"

def login_required(role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if session.get("role") != role:
                if request.path.startswith("/api/"):
                    return jsonify(success=False, message="Please sign in again."), 401
                return redirect(url_for("login_page"))
            return fn(*a, **kw)
        return wrapper
    return deco

def student(fn):
    return login_required("Student")(fn)

def admin(fn):
    return login_required("Admin")(fn)

@app.errorhandler(psycopg2.Error)
@app.errorhandler(RuntimeError)
def db_error(e):
    app.logger.error("DB error: %s", e)
    msg = "Database error. Please try again later."
    if request.path.startswith("/api/"):
        return jsonify(success=False, message=msg), 500
    return msg, 500

def today_attendance(student_id):
    return run(
        "SELECT attendance_id, clock_in_time, clock_out_time FROM Attendance "
        "WHERE student_id=%s AND CAST(clock_in_time AS DATE)=%s "
        "ORDER BY clock_in_time DESC LIMIT 1",
        (student_id, now_ist().date()), "one")

_schedule_table_ready = False

def ensure_attendance_schedule_table():
    global _schedule_table_ready
    if _schedule_table_ready:
        return
    run("""
        CREATE TABLE IF NOT EXISTS StudentAttendanceSchedule (
            student_id INT NOT NULL PRIMARY KEY,
            start_time TIME(0) NOT NULL,
            end_time TIME(0) NOT NULL,
            CONSTRAINT fk_studentattendanceschedule_student
                FOREIGN KEY (student_id) REFERENCES Student(student_id)
                ON DELETE CASCADE
        )
    """)
    _schedule_table_ready = True

def active_attendance_for_day(day):
    rows = run("""
        SELECT s.student_id, s.firstname || ' ' || s.lastname AS name, a.clock_in_time
        FROM Attendance a
        JOIN Student s ON s.student_id=a.student_id
        WHERE CAST(a.clock_in_time AS DATE)=%s AND a.clock_out_time IS NULL
        ORDER BY a.clock_in_time
    """, (day,), "all")
    return [{
        "id": row.student_id,
        "name": row.name,
        "clockIn": fmt_time(row.clock_in_time),
    } for row in rows]


def send_student_credentials(student_email, firstname, password):
    """Sends login credentials to the newly created student."""
    try:
        app.logger.info(f"=== EMAIL START: Sending to {student_email} ===")
        app.logger.info(f"SMTP_SERVER: {SMTP_SERVER}")
        app.logger.info(f"SMTP_USER: {SMTP_USER}")
        app.logger.info(f"SMTP_PASS length: {len(SMTP_PASS)} characters")
        
        if not SMTP_USER or not SMTP_PASS:
            app.logger.error("ERROR: SMTP credentials are empty!")
            return False

        # Create email message
        msg = MIMEText(f"""Hello {firstname},

Your account has been successfully created on the Campus Portal.

Username (Email): {student_email}
Password: {password}

Please log in and change your password immediately.

Regards,
Campus Portal Administration
""")
        msg['Subject'] = "Welcome to Campus Portal - Login Details"
        msg['From'] = SMTP_USER
        msg['To'] = student_email

        # --- CHANGED: Use SMTP_SSL on Port 465 (More reliable on Render) ---
        app.logger.info("Connecting to Gmail via SSL on Port 465...")
        server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=15)
        
        app.logger.info("Logging in to Gmail...")
        server.login(SMTP_USER, SMTP_PASS)
        
        app.logger.info("Sending email...")
        server.send_message(msg)
        server.quit()
        
        app.logger.info(f"=== EMAIL SUCCESS: Sent to {student_email} ===")
        return True
        
    except smtplib.SMTPAuthenticationError:
        app.logger.error("GMAIL AUTH FAILED: Check SMTP_PASS")
        return False
    except Exception as e:
        app.logger.error(f"EMAIL FAILED: {str(e)}")
        return False
    
# ---------------------------------------------------------------- health check
@app.route("/health")
def health():
    return "ok"

# ---------------------------------------------------------------- auth
@app.route("/")
def login_page():
    role = session.get("role")
    if role == "Admin":
        return redirect(url_for("dashboard_admin"))
    if role == "Student":
        return redirect(url_for("dashboard_student"))
    return render_template("login.html")

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify(success=False, message="Username and password are required.")
    row = run(
        "SELECT u.user_id, u.role, u.username, u.password_hash, s.student_id, s.firstname "
        "FROM users u LEFT JOIN Student s ON s.user_id = u.user_id "
        "WHERE u.username=%s", (username,), "one")
    ok, upgrade = verify_pw(row.password_hash, password) if row else (False, False)
    if not ok:
        return jsonify(success=False, message="Invalid username or password.")
    if upgrade:
        run("UPDATE users SET password_hash=%s WHERE user_id=%s", (hash_pw(password), row.user_id))
    session.clear()
    session.update(user_id=row.user_id, role=row.role, username=row.username,
                   student_id=row.student_id, name=row.firstname or row.username)
    return jsonify(success=True, role=row.role)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ---------------------------------------------------------------- student pages
@app.route("/dashboard_student")
@student
def dashboard_student():
    sid = session["student_id"]
    today_date = now_ist().date()

    # --- HOLIDAY CHECK LOGIC ---
    is_sunday = today_date.weekday() == 6
    today_holiday = run("SELECT name FROM Holiday WHERE holiday_date = %s", (today_date,), "one")
    
    is_holiday = is_sunday or (today_holiday is not None)
    
    if is_sunday:
        holiday_name = "Sunday (Weekly Off)"
    elif today_holiday:
        holiday_name = today_holiday.name
    else:
        holiday_name = None
    # ---------------------------

    counts = {r.status: r.n for r in run(
        "SELECT status, COUNT(*) AS n FROM Task WHERE student_id=%s GROUP BY status",
        (sid,), "all")}
    completed = counts.get("completed", 0)
    remaining = counts.get("pending", 0) + counts.get("in_progress", 0) + counts.get("pending_approval", 0)

    overdue_row = run("SELECT COUNT(*) AS n FROM Task WHERE student_id=%s AND status NOT IN ('completed', 'pending_approval') AND due_date < %s",
                      (sid, today_date), "one")
    overdue = overdue_row.n if overdue_row else 0

    lv = run("SELECT COUNT(*) AS total, "
             "SUM(CASE WHEN status='Pending' THEN 1 ELSE 0 END) AS pending "
             "FROM LeaveRequest WHERE student_id=%s", (sid,), "one")

    att = today_attendance(sid)
    if not att:
        att_status, att_sub, cin, cout, dur = "Not Started", "No clock-in yet", "--:--:--", "--:--:--", "00:00:00"
    elif att.clock_out_time is None:
        att_status = "In Progress"
        att_sub = f"Started at {fmt_time(att.clock_in_time)}"
        cin, cout, dur = fmt_time(att.clock_in_time), "--:--:--", "00:00:00"
    else:
        att_status = "Completed"
        att_sub = f"Worked {fmt_duration(att.clock_in_time, att.clock_out_time)}"
        cin, cout = fmt_time(att.clock_in_time), fmt_time(att.clock_out_time)
        dur = fmt_duration(att.clock_in_time, att.clock_out_time)

    start_of_week = today_date - timedelta(days=today_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    week_att = run(
        "SELECT clock_in_time, clock_out_time FROM Attendance WHERE student_id=%s AND CAST(clock_in_time AS DATE) BETWEEN %s AND %s",
        (sid, start_of_week, end_of_week), "all"
    )

    weekly_totals = [0] * 7
    total_week_secs = 0
    for r in week_att:
        if r.clock_in_time and r.clock_out_time:
            day_idx = r.clock_in_time.weekday()
            secs = (r.clock_out_time - r.clock_in_time).total_seconds()
            weekly_totals[day_idx] += secs
            total_week_secs += secs

    week_h = int(total_week_secs // 3600)
    week_m = int((total_week_secs % 3600) // 60)
    week_duration_str = f"{week_h}h {week_m:02d}m"
    week_progress_pct = min(100, int((total_week_secs / 144000) * 100))

    month_start = today_date.replace(day=1)
    present_days = run("SELECT COUNT(DISTINCT CAST(clock_in_time AS DATE)) AS n FROM Attendance WHERE student_id=%s AND CAST(clock_in_time AS DATE) >= %s", (sid, month_start), "one").n
    holidays = run("SELECT name, holiday_date FROM Holiday WHERE holiday_date>=%s ORDER BY holiday_date LIMIT 3", (today_date,), "all")
    recent = run("SELECT title, due_date, status FROM Task WHERE student_id=%s ORDER BY created_at DESC LIMIT 5", (sid,), "all")

    # --- UPCOMING BIRTHDAYS (NEXT 2 MONTHS) ---
    students_with_dob = run("SELECT firstname, lastname, dob FROM Student WHERE dob IS NOT NULL", fetch="all")
    upcoming_birthdays = []
    target_date = today_date + timedelta(days=60) # Look ahead 60 days (approx 2 months)

    for s in students_with_dob:
        try:
            # Set birthday to current year
            bday = s.dob.replace(year=today_date.year)
        except ValueError:
            # Handle Feb 29 on non-leap years
            bday = s.dob.replace(year=today_date.year, day=28)

        # If birthday already passed this year, check next year
        if bday < today_date:
            try:
                bday = s.dob.replace(year=today_date.year + 1)
            except ValueError:
                bday = s.dob.replace(year=today_date.year + 1, day=28)

        # If birthday is within the next 60 days
        if today_date <= bday <= target_date:
            upcoming_birthdays.append({
                "name": f"{s.firstname} {s.lastname}",
                "date": bday
            })

    # Sort by date and keep top 5
    upcoming_birthdays.sort(key=lambda x: x["date"])
    upcoming_birthdays = upcoming_birthdays[:5]
    # ------------------------------------------

    # --- TODAY'S ON LEAVES ---
    today_leaves = run("""
        SELECT s.firstname || ' ' || s.lastname AS name, l.leave_type, l.start_date, l.end_date
        FROM LeaveRequest l
        JOIN Student s ON s.student_id = l.student_id
        WHERE l.status = 'Approved' AND %s BETWEEN l.start_date AND l.end_date
        ORDER BY s.firstname
    """, (today_date,), "all")
    today_leaves_list = [{"name": t.name, "type": t.leave_type} for t in today_leaves]
    # --------------------------------

    return render_template(
        "dashboard_student.html", username=session["name"], student_id=sid,
        remaining_tasks=remaining, completed_tasks=completed, overdue_tasks=overdue,
        total_leaves=lv.total or 0, pending_leaves=lv.pending or 0,
        att_status=att_status, att_sub=att_sub, clock_in=cin, clock_out=cout,
        duration=dur, upcoming_holidays=holidays, recent_tasks=recent,
        present_days=present_days,
        weekly_totals=weekly_totals, week_duration_str=week_duration_str,
        week_progress_pct=week_progress_pct, today_weekday=today_date.weekday(),
        today_date=today_date,
        is_holiday=is_holiday, holiday_name=holiday_name,
        upcoming_birthdays=upcoming_birthdays,
        today_leaves=today_leaves_list
    )

@app.route("/calendar")
@student
def calendar_page():
    rows = run("SELECT holiday_date, name FROM Holiday", fetch="all")
    return render_template("calendar.html",
                           holidays={r.holiday_date.isoformat(): r.name for r in rows})

@app.route("/leave")
@student
def leave_page():
    leaves = run("SELECT leave_type, start_date, end_date, status FROM LeaveRequest "
                 "WHERE student_id=%s ORDER BY applied_on DESC", (session["student_id"],), "all")
    return render_template(
        "leave.html", leaves=leaves, total=len(leaves),
        pending=sum(1 for l in leaves if l.status == "Pending"),
        approved=sum(1 for l in leaves if l.status == "Approved"))

@app.route("/tasks")
@student
def tasks_page():
    return render_template("tasks.html")

def attendance_records(sid):
    rows = run("SELECT clock_in_time, clock_out_time, clock_in_lat, clock_in_lng, clock_out_lat, clock_out_lng FROM Attendance WHERE student_id=%s "
               "ORDER BY clock_in_time DESC", (sid,), "all")
    return [{"date": r.clock_in_time, "clock_in_time": r.clock_in_time,
             "clock_out_time": r.clock_out_time,
             "working_hours": fmt_duration(r.clock_in_time, r.clock_out_time),
             "in_lat": float(r.clock_in_lat) if r.clock_in_lat else None,
             "in_lng": float(r.clock_in_lng) if r.clock_in_lng else None,
             "out_lat": float(r.clock_out_lat) if r.clock_out_lat else None,
             "out_lng": float(r.clock_out_lng) if r.clock_out_lng else None} for r in rows]

@app.route("/attendance")
@student
def attendance_page():
    records = attendance_records(session["student_id"])
    total_secs = sum((r["clock_out_time"] - r["clock_in_time"]).total_seconds()
                     for r in records if r["clock_out_time"])
    today = now_ist().date()
    today_rec = next((r for r in records if r["date"].date() == today), None)
    return render_template(
        "attendance.html", records=records,
        days_recorded=len({r["date"].date() for r in records}),
        total_hours=f"{int(total_secs // 3600)}h {int(total_secs % 3600 // 60)}m",
        today_duration=today_rec["working_hours"] if today_rec else "00:00:00")

@app.route("/attendance/export")
@student
def attendance_export():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Clock In (IST)", "Clock Out (IST)", "Duration"])
    for r in attendance_records(session["student_id"]):
        w.writerow([r["date"].strftime("%Y-%m-%d"), fmt_time(r["clock_in_time"]),
                    fmt_time(r["clock_out_time"]), r["working_hours"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=attendance.csv"})

@app.route("/student_profile")
@student
def student_profile():
    student = run("""
        SELECT s.student_id, s.firstname, s.lastname, s.email, s.mobile_no,
               s.gender, s.dob, s.city, u.username
        FROM Student s
        INNER JOIN users u ON s.user_id = u.user_id
        WHERE s.student_id = %s
    """, (session["student_id"],), "one")

    if not student:
        return "Profile not found", 404

    p = compute_performance(session["student_id"])

    return render_template("student_profile.html",
                           student=student, p=p, username=session["name"])

# ---------------------------------------------------------------- student APIs
@app.route("/api/clock_in", methods=["POST"])
@student
def api_clock_in():
    sid = session["student_id"]
    today = now_ist().date()
    
    # 1. Block Sundays automatically
    if today.weekday() == 6: 
        return jsonify(success=False, message="Today is Sunday. Clock-in is disabled.")
    
    # 2. Block specific database holidays
    today_holiday = run("SELECT name FROM Holiday WHERE holiday_date = %s", (today,), "one")
    if today_holiday:
        return jsonify(success=False, message=f"Today is a holiday ({today_holiday.name}). Clock-in is disabled.")
    
    if today_attendance(sid):
        return jsonify(success=False, message="You have already clocked in today.")
    
    # GET LOCATION DATA FROM FRONTEND
    data = request.get_json(silent=True) or {}
    lat = data.get("lat")
    lng = data.get("lng")
    
    now = now_ist()
    ensure_attendance_schedule_table()
    schedule = run(
        "SELECT start_time FROM StudentAttendanceSchedule WHERE student_id=%s",
        (sid,), "one")
    if schedule and now.time() < schedule.start_time:
        return jsonify(
            success=False,
            message=f"Clock-in opens at {schedule.start_time.strftime('%I:%M %p').lower()}.")
    
    # SAVE LOCATION TO DATABASE
    run("""
        INSERT INTO Attendance (student_id, clock_in_time, clock_in_lat, clock_in_lng) 
        VALUES (%s, %s, %s, %s)
    """, (sid, now, lat, lng))
    
    return jsonify(success=True, time=fmt_time(now))

@app.route("/api/clock_out", methods=["POST"])
@student
def api_clock_out():
    sid = session["student_id"]
    att = today_attendance(sid)
    if not att:
        return jsonify(success=False, message="Clock in first.")
    if att.clock_out_time:
        return jsonify(success=False, message="You have already clocked out today.")
    
    # GET LOCATION DATA FROM FRONTEND
    data = request.get_json(silent=True) or {}
    lat = data.get("lat")
    lng = data.get("lng")
    
    now = now_ist()
    
    # SAVE LOCATION TO DATABASE
    run("""
        UPDATE Attendance 
        SET clock_out_time=%s, clock_out_lat=%s, clock_out_lng=%s 
        WHERE attendance_id=%s
    """, (now, lat, lng, att.attendance_id))
    
    return jsonify(success=True, time=fmt_time(now),
                   duration=fmt_duration(att.clock_in_time, now))

@app.route("/api/attendance_status", methods=["GET"])
@student
def api_attendance_status():
    att = today_attendance(session["student_id"])
    if not att:
        return jsonify(status="Not Started", clock_in="--:--:--", duration="00:00:00")
    if not att.clock_out_time:
        return jsonify(status="In Progress", clock_in=fmt_time(att.clock_in_time), duration="00:00:00")
    return jsonify(
        status="Completed",
        clock_in=fmt_time(att.clock_in_time),
        duration=fmt_duration(att.clock_in_time, att.clock_out_time)
    )

@app.route("/api/update_profile", methods=["POST"])
@student
def api_update_profile():
    d = request.get_json(silent=True) or {}
    sid = session["student_id"]

    firstname = (d.get("firstname") or "").strip()
    lastname = (d.get("lastname") or "").strip()
    email = (d.get("email") or "").strip()
    mobile_no = (d.get("mobile_no") or "").strip()
    gender = (d.get("gender") or "").strip()
    dob_str = (d.get("dob") or "").strip()
    city = (d.get("city") or "").strip()

    if not firstname or not lastname or not email or not city:
        return jsonify(success=False, message="Name, email, and city are required."), 400

    if "@" not in email:
        return jsonify(success=False, message="Invalid email address."), 400

    try:
        dob = date.fromisoformat(dob_str) if dob_str else None
    except ValueError:
        return jsonify(success=False, message="Invalid date of birth."), 400

    try:
        existing = run("SELECT student_id FROM Student WHERE email=%s AND student_id<>%s", (email, sid), "one")
        if existing:
            return jsonify(success=False, message="That email address is already in use by another account."), 400

        run("""
            UPDATE Student
            SET firstname=%s, lastname=%s, email=%s, mobile_no=%s, gender=%s, dob=%s, city=%s
            WHERE student_id=%s
        """, (firstname, lastname, email, mobile_no or None, gender or None, dob, city, sid))

        run("UPDATE users SET username=%s WHERE user_id=(SELECT user_id FROM Student WHERE student_id=%s)", (email, sid))

        session["name"] = firstname
        return jsonify(success=True)
    except psycopg2.IntegrityError:
        return jsonify(success=False, message="Database error updating profile."), 400

@app.route("/api/add_leave", methods=["POST"])
@student
def api_add_leave():
    d = request.get_json(silent=True) or {}
    if d.get("leave_type") not in LEAVE_TYPES:
        return jsonify(success=False, message="Choose a valid leave type."), 400
    try:
        start = date.fromisoformat(d.get("start_date", ""))
        end = date.fromisoformat(d.get("end_date", ""))
    except ValueError:
        return jsonify(success=False, message="Enter valid dates."), 400
    if end < start:
        return jsonify(success=False, message="End date cannot be before start date."), 400
    if start < now_ist().date():
        return jsonify(success=False, message="Start date cannot be in the past."), 400
    run("INSERT INTO LeaveRequest (student_id, start_date, end_date, leave_type, reason) "
        "VALUES (%s, %s, %s, %s, %s)",
        (session["student_id"], start, end, d["leave_type"], (d.get("reason") or "").strip()))
    return jsonify(success=True)

# ================================================================ TASKS API
@app.route("/api/students", methods=["GET"])
@student
def api_students_list():
    rows = run(
        "SELECT firstname || ' ' || lastname AS name FROM Student ORDER BY firstname, lastname",
        fetch="all")
    return jsonify([{"name": r.name} for r in rows])

@app.route("/api/tasks", methods=["GET"])
@student
def api_tasks_list():
    sid = session["student_id"]
    rows = run("""
        SELECT task_id, title, status, due_date, created_at, updated_at,
               assigned_to, estimated_hours, hours_logged
        FROM Task WHERE student_id=%s ORDER BY created_at DESC
    """, (sid,), "all")

    result = []
    for r in rows:
        result.append({
            "id": r.task_id,
            "code": f"TASK-{r.task_id:03d}",
            "title": r.title,
            "status": r.status,
            "due": r.due_date.isoformat() if r.due_date else "",
            "start": r.created_at.strftime("%Y-%m-%d") if r.created_at else "",
            "completedOn": r.updated_at.strftime("%Y-%m-%d") if r.status == 'completed' and r.updated_at else "",
            "assignee": r.assigned_to or "",
            "estimate": float(r.estimated_hours) if r.estimated_hours else 0,
            "hoursLogged": float(r.hours_logged) if r.hours_logged else 0
        })
    return jsonify(result)

@app.route("/api/tasks", methods=["POST"])
@student
def api_tasks_create():
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify(success=False, message="Task title is required."), 400

    sid = session["student_id"]
    run("""
        INSERT INTO Task (student_id, title, due_date, status, assigned_to)
        VALUES (%s, %s, %s, 'pending', %s)
    """, (
        sid,
        title,
        d.get("due") or now_ist().date(),
        d.get("assignee")
    ))
    return jsonify(success=True)

@app.route("/api/tasks/<int:tid>", methods=["PUT"])
@student
def api_tasks_update(tid):
    d = request.get_json(silent=True) or {}
    sid = session["student_id"]

    n = run("""
        UPDATE Task
        SET title=%s, due_date=%s, assigned_to=%s
        WHERE task_id=%s AND student_id=%s
    """, (
        d.get("title"), d.get("due"), d.get("assignee"), tid, sid
    ))

    if not n:
        return jsonify(success=False, message="Task not found."), 404
    return jsonify(success=True)

@app.route("/api/tasks/<int:tid>/log_time", methods=["POST"])
@student
def api_tasks_log_time(tid):
    d = request.get_json(silent=True) or {}
    hours = float(d.get("hours", 0))
    if hours <= 0:
        return jsonify(success=False, message="Invalid hours."), 400

    n = run("""
        UPDATE Task
        SET hours_logged = COALESCE(hours_logged, 0) + %s,
            status = 'pending_approval',
            updated_at = %s
        WHERE task_id=%s AND student_id=%s
    """, (hours, now_ist(), tid, session["student_id"]))

    if not n:
        return jsonify(success=False, message="Task not found."), 404
    return jsonify(success=True)

# ---------------------------------------------------------------- admin
@app.route("/dashboard_admin")
@admin
def dashboard_admin():
    return render_template("dashboard_admin.html")

@app.route("/api/admin/summary")
@admin
def admin_summary():
    today = now_ist().date()
    r = run("SELECT "
            "(SELECT COUNT(*) FROM Student) AS students, "
            "(SELECT COUNT(DISTINCT student_id) FROM Attendance "
            " WHERE CAST(clock_in_time AS DATE)=%s) AS present, "
            "(SELECT COUNT(*) FROM LeaveRequest WHERE status='Pending') AS leaves, "
            "(SELECT COUNT(*) FROM Task WHERE status NOT IN ('completed', 'pending_approval')) AS tasks",
            (today,), "one")
    return jsonify(students=r.students, present=r.present, leaves=r.leaves,
                   tasks=r.tasks, activeStudents=active_attendance_for_day(today))

@app.route("/api/admin/active-attendance")
@admin
def admin_active_attendance():
    return jsonify(activeStudents=active_attendance_for_day(now_ist().date()))

@app.route("/api/admin/students")
@admin
def admin_students():
    rows = run("SELECT student_id, firstname || ' ' || lastname AS name FROM Student "
               "ORDER BY firstname", fetch="all")
    return jsonify([{"id": r.student_id, "name": r.name} for r in rows])

@app.route("/api/admin/leaves")
@admin
def admin_leaves():
    rows = run("SELECT l.leave_id, s.firstname || ' ' || s.lastname AS name, l.leave_type, "
               "l.start_date, l.end_date, l.reason, l.status FROM LeaveRequest l "
               "JOIN Student s ON s.student_id=l.student_id ORDER BY l.applied_on DESC",
               fetch="all")
    return jsonify([{"id": r.leave_id, "student": r.name, "type": r.leave_type,
                     "start": r.start_date.isoformat(), "end": r.end_date.isoformat(),
                     "reason": r.reason or "", "status": r.status} for r in rows])

@app.route("/api/admin/leaves/<int:leave_id>", methods=["POST"])
@admin
def admin_decide_leave(leave_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("Approved", "Rejected"):
        return jsonify(success=False, message="Invalid status."), 400
    run("UPDATE LeaveRequest SET status=%s WHERE leave_id=%s", (status, leave_id))
    return jsonify(success=True)

@app.route("/api/admin/attendance", methods=["GET"])
@admin
def admin_attendance():
    month = request.args.get("month") or now_ist().strftime("%Y-%m")
    try:
        y, m = map(int, month.split("-"))
        days = cal.monthrange(y, m)[1]
    except ValueError:
        return jsonify(success=False, message="Invalid month."), 400

    ensure_attendance_schedule_table()
    start_date, end_date = date(y, m, 1), date(y, m, days)
    status_rows = run(
        "SELECT student_id, attendance_date, status FROM AttendanceStatus "
        "WHERE attendance_date BETWEEN %s AND %s", (start_date, end_date), "all")
    records = {}
    for r in status_rows:
        records.setdefault(str(r.student_id), {})[str(r.attendance_date.day)] = r.status
    attendance_rows = run("""
        SELECT a.student_id, a.clock_in_time, a.clock_out_time,
               a.clock_in_lat, a.clock_in_lng, a.clock_out_lat, a.clock_out_lng,
               s.start_time, s.end_time
        FROM Attendance a
        LEFT JOIN StudentAttendanceSchedule s ON s.student_id=a.student_id
        WHERE CAST(a.clock_in_time AS DATE) BETWEEN %s AND %s
        ORDER BY a.clock_in_time DESC
    """, (start_date, end_date), "all")
    clock_times = {}
    for r in attendance_rows:
        attendance_day = r.clock_in_time.date()
        student_key = str(r.student_id)
        day_key = str(attendance_day.day)
        student_times = clock_times.setdefault(student_key, {})
        if day_key in student_times:
            continue

        clock_in = r.clock_in_time.time()
        if r.end_time and r.clock_out_time and r.clock_out_time.time() < r.end_time:
            automatic_status = "Half-day"
        elif r.start_time and clock_in > r.start_time:
            automatic_status = "Late"
        else:
            automatic_status = "Present"

        student_times[day_key] = {
            "clockIn": fmt_time(r.clock_in_time),
            "clockOut": fmt_time(r.clock_out_time) if r.clock_out_time else "",
            "inLat": float(r.clock_in_lat) if r.clock_in_lat else None,
            "inLng": float(r.clock_in_lng) if r.clock_in_lng else None,
            "outLat": float(r.clock_out_lat) if r.clock_out_lat else None,
            "outLng": float(r.clock_out_lng) if r.clock_out_lng else None,
        }
        records.setdefault(student_key, {}).setdefault(day_key, automatic_status)

    schedules = run(
        "SELECT student_id, start_time, end_time FROM StudentAttendanceSchedule",
        fetch="all")
    return jsonify(
        days=days, year=y, month=m, records=records, clockTimes=clock_times,
        schedules=[{
            "studentId": r.student_id,
            "startTime": r.start_time.strftime("%H:%M"),
            "endTime": r.end_time.strftime("%H:%M"),
        } for r in schedules])

@app.route("/api/admin/attendance/schedules", methods=["POST"])
@admin
def admin_attendance_schedule():
    d = request.get_json(silent=True) or {}
    try:
        sid = int(d.get("student_id"))
        start_time = dtime.fromisoformat(d.get("start_time", ""))
        end_time = dtime.fromisoformat(d.get("end_time", ""))
    except (ValueError, TypeError):
        return jsonify(success=False, message="Choose a student and valid schedule times."), 400
    if start_time >= end_time:
        return jsonify(success=False, message="End time must be after start time."), 400

    ensure_attendance_schedule_table()
    if not run("SELECT student_id FROM Student WHERE student_id=%s", (sid,), "one"):
        return jsonify(success=False, message="Student not found."), 404

    updated = run(
        "UPDATE StudentAttendanceSchedule SET start_time=%s, end_time=%s WHERE student_id=%s",
        (start_time, end_time, sid))
    if not updated:
        run(
            "INSERT INTO StudentAttendanceSchedule (student_id, start_time, end_time) "
            "VALUES (%s, %s, %s)", (sid, start_time, end_time))
    return jsonify(success=True)

@app.route("/api/admin/tasks", methods=["GET", "POST"])
@admin
def admin_tasks():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        title = (d.get("title") or "").strip()
        try:
            due = date.fromisoformat(d.get("due_date", ""))
            sid = int(d.get("student_id"))
        except (ValueError, TypeError):
            return jsonify(success=False, message="Choose a student and a valid due date."), 400
        if not title or len(title) > 255:
            return jsonify(success=False, message="Enter a title (max 255 characters)."), 400
        run("INSERT INTO Task (student_id, title, description, due_date) VALUES (%s, %s, %s, %s)",
            (sid, title, (d.get("description") or "").strip() or None, due))
        return jsonify(success=True)
    rows = run("SELECT t.task_id, t.title, t.due_date, t.status, "
               "s.firstname || ' ' || s.lastname AS name FROM Task t "
               "JOIN Student s ON s.student_id=t.student_id ORDER BY t.due_date DESC",
               fetch="all")
    return jsonify([{"id": r.task_id, "title": r.title, "student": r.name,
                     "due": r.due_date.isoformat(), "status": r.status} for r in rows])

@app.route("/api/admin/tasks/<int:tid>/approve", methods=["POST"])
@admin
def admin_approve_task(tid):
    run("UPDATE Task SET status='completed', updated_at=%s WHERE task_id=%s", (now_ist(), tid))
    return jsonify(success=True)

@app.route("/api/admin/tasks/<int:tid>/reject", methods=["POST"])
@admin
def admin_reject_task(tid):
    run("UPDATE Task SET status='in_progress', updated_at=%s WHERE task_id=%s", (now_ist(), tid))
    return jsonify(success=True)

@app.route("/api/admin/tasks/<int:task_id>/delete", methods=["POST"])
@admin
def admin_delete_task(task_id):
    run("DELETE FROM Task WHERE task_id=%s", (task_id,))
    return jsonify(success=True)

@app.route("/api/admin/holidays", methods=["GET", "POST", "DELETE"])
@admin
def admin_holidays():
    if request.method == "GET":
        rows = run("SELECT holiday_date, name FROM Holiday", fetch="all")
        return jsonify({r.holiday_date.isoformat(): r.name for r in rows})
    d = request.get_json(silent=True) or {}
    try:
        day = date.fromisoformat(d.get("date", ""))
    except ValueError:
        return jsonify(success=False, message="Invalid date."), 400
    if request.method == "DELETE":
        run("DELETE FROM Holiday WHERE holiday_date=%s", (day,))
    else:
        name = (d.get("name") or "").strip()
        if not name or len(name) > 100:
            return jsonify(success=False, message="Enter a holiday name (max 100)."), 400
        if not run("UPDATE Holiday SET name=%s WHERE holiday_date=%s", (name, day)):
            run("INSERT INTO Holiday (holiday_date, name) VALUES (%s, %s)", (day, name))
    return jsonify(success=True)

# ================================================================ SECURITY
def hash_pw(p):
    return generate_password_hash(p, method="pbkdf2:sha256")

def verify_pw(stored, p):
    if isinstance(stored, (bytes, bytearray, memoryview)):
        stored = bytes(stored).decode("utf-8")
    if stored.startswith(("pbkdf2:", "scrypt:")):
        return check_password_hash(stored, p), False
    ok = hmac.compare_digest(stored.encode("utf-8"), p.encode("utf-8"))
    return ok, ok

def password_problem(p):
    if len(p) < 8:
        return "Password must be at least 8 characters."
    if len(p) > 128:
        return "Password is too long (max 128)."
    return None

def any_user(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("role"):
            return jsonify(success=False, message="Please sign in again."), 401
        return fn(*a, **kw)
    return wrapper

def run_tx(fn):
    conn = get_conn()
    if conn is None:
        raise RuntimeError("Database is unavailable. Please try again later.")
    try:
        result = fn(conn.cursor())
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise

@app.route("/api/change_password", methods=["POST"])
@any_user
def api_change_password():
    d = request.get_json(silent=True) or {}
    current = d.get("current_password") or ""
    new = d.get("new_password") or ""
    row = run("SELECT password_hash FROM users WHERE user_id=%s", (session["user_id"],), "one")
    if not row or not verify_pw(row.password_hash, current)[0]:
        return jsonify(success=False, message="Current password is incorrect."), 400
    if new != (d.get("confirm_password") or ""):
        return jsonify(success=False, message="New passwords do not match."), 400
    if new == current:
        return jsonify(success=False, message="New password must be different."), 400
    problem = password_problem(new)
    if problem:
        return jsonify(success=False, message=problem), 400
    run("UPDATE users SET password_hash=%s WHERE user_id=%s", (hash_pw(new), session["user_id"]))
    return jsonify(success=True)

@app.route("/security")
@student
def security_page():
    return render_template("security.html")

# ================================================================ PERFORMANCE
def grade_for(score):
    for cutoff, g_ in ((90, "A"), (75, "B"), (60, "C"), (40, "D")):
        if score >= cutoff:
            return g_
    return "F"

def compute_performance(sid):
    today = now_ist().date()
    start = today - timedelta(days=29)
    logins = run("SELECT clock_in_time FROM Attendance WHERE student_id=%s "
                 "AND CAST(clock_in_time AS DATE) BETWEEN %s AND %s", (sid, start, today), "all")
    att_dates = {r.clock_in_time.date() for r in logins}
    holidays = {h.holiday_date for h in run(
        "SELECT holiday_date FROM Holiday WHERE holiday_date BETWEEN %s AND %s", (start, today), "all")}
    leave_rows = run("SELECT start_date, end_date FROM LeaveRequest WHERE student_id=%s "
                     "AND status='Approved'", (sid,), "all")
    leave_days = set()
    for l in leave_rows:
        d = l.start_date
        while d <= l.end_date:
            leave_days.add(d)
            d += timedelta(days=1)

    expected = []
    for i in range(30):
        d = start + timedelta(days=i)
        if d.weekday() >= 5 or d in holidays or d in leave_days:
            continue
        if d == today and d not in att_dates:
            continue
        expected.append(d)
    attended = sum(1 for d in expected if d in att_dates)
    att_pct = round(attended / len(expected) * 100, 2) if expected else 100.0
    absent = len(expected) - attended

    counts = {r.status: r.n for r in run(
        "SELECT status, COUNT(*) AS n FROM Task WHERE student_id=%s GROUP BY status", (sid,), "all")}
    done = counts.get("completed", 0)
    total_tasks = sum(counts.values())
    remaining = total_tasks - done
    task_pct = round(done / total_tasks * 100, 2) if total_tasks else None
    overall = att_pct if task_pct is None else round(0.6 * att_pct + 0.4 * task_pct, 2)
    grade = grade_for(overall)

    avg_login = None
    if logins:
        secs = sum(r.clock_in_time.hour * 3600 + r.clock_in_time.minute * 60 + r.clock_in_time.second
                   for r in logins) // len(logins)
        avg_login = dtime(secs // 3600, secs % 3600 // 60, secs % 60)
    total_leaves = len(leave_rows)

    vals = (overall, grade, att_pct, avg_login, done, remaining, total_leaves, absent)
    n = run("UPDATE Performance SET overall_percentage=%s, grade=%s, total_attendance_percentage=%s, "
            "avg_login_time=%s, total_tasks_completed=%s, remaining_tasks=%s, total_leaves=%s, "
            "absent_without_leave=%s WHERE student_id=%s", vals + (sid,))
    if not n:
        run("INSERT INTO Performance (overall_percentage, grade, total_attendance_percentage, "
            "avg_login_time, total_tasks_completed, remaining_tasks, total_leaves, "
            "absent_without_leave, student_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", vals + (sid,))
    return {"overall": overall, "grade": grade, "attendance_pct": att_pct, "task_pct": task_pct,
            "completed": done, "remaining": remaining, "leaves": total_leaves, "absent": absent,
            "expected": len(expected), "attended": attended,
            "avg_login": avg_login.strftime("%I:%M %p").lower() if avg_login else "—"}

@app.route("/performance")
@student
def performance_page():
    return render_template("performance.html", p=compute_performance(session["student_id"]))

# ================================================================ TIMETABLE
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

@app.route("/timetable")
@student
def timetable_page():
    rows = run("SELECT timetable_id, day_of_week, time_slots FROM TimeTable "
               "WHERE student_id=%s ORDER BY time_slots", (session["student_id"],), "all")
    grouped = [(d, [r for r in rows if r.day_of_week == d]) for d in DAYS]
    return render_template("timetable.html", grouped=grouped, days=DAYS,
                           today=now_ist().strftime("%A"))

@app.route("/api/timetable/add", methods=["POST"])
@student
def api_timetable_add():
    d = request.get_json(silent=True) or {}
    slot = (d.get("time_slots") or "").strip()
    if d.get("day_of_week") not in DAYS:
        return jsonify(success=False, message="Choose a valid day."), 400
    if not slot or len(slot) > 255:
        return jsonify(success=False, message="Enter a time slot (max 255 characters)."), 400
    run("INSERT INTO TimeTable (student_id, day_of_week, time_slots) VALUES (%s, %s, %s)",
        (session["student_id"], d["day_of_week"], slot))
    return jsonify(success=True)

@app.route("/api/timetable/delete", methods=["POST"])
@student
def api_timetable_delete():
    tid = (request.get_json(silent=True) or {}).get("timetable_id")
    n = run("DELETE FROM TimeTable WHERE timetable_id=%s AND student_id=%s",
            (tid, session["student_id"]))
    if not n:
        return jsonify(success=False, message="Entry not found."), 404
    return jsonify(success=True)

# ================================================================ ADMIN: STUDENTS
def clean_student(d, creating):
    v = {k: (d.get(k) or "").strip() for k in
         ("firstname", "lastname", "email", "mobile_no", "gender", "dob", "city")}

    for k, label, mx in (("firstname", "First name", 50), ("lastname", "Last name", 50),
                         ("city", "City", 50)):
        if not v[k] or len(v[k]) > mx:
            return None, f"{label} is required (max {mx} characters)."
    if "@" not in v["email"] or len(v["email"]) > 100:
        return None, "Enter a valid email address."
    if v["mobile_no"] and not (v["mobile_no"].isdigit() and 7 <= len(v["mobile_no"]) <= 15):
        return None, "Mobile number must be 7-15 digits."
    if v["gender"] not in ("", "Male", "Female", "Other"):
        return None, "Invalid gender."

    if v["dob"]:
        try:
            v["dob"] = date.fromisoformat(v["dob"])
        except ValueError:
            return None, "Invalid date of birth."
    else:
        if creating:
            return None, "Date of birth is required to generate the password."
        v["dob"] = None

    return v, None

@app.route("/api/admin/student-list")
@admin
def admin_student_list():
    rows = run("SELECT s.student_id, u.username, s.firstname, s.lastname, s.email, s.mobile_no, "
               "s.gender, s.dob, s.city FROM Student s JOIN users u ON u.user_id=s.user_id "
               "ORDER BY s.firstname, s.lastname", fetch="all")
    return jsonify([{"id": r.student_id, "username": r.username, "firstname": r.firstname,
                     "lastname": r.lastname, "email": r.email, "mobile_no": r.mobile_no or "",
                     "gender": r.gender or "", "dob": r.dob.isoformat() if r.dob else "",
                     "city": r.city} for r in rows])

@app.route("/api/admin/student-list", methods=["POST"])
@admin
def admin_student_create():
    v, err = clean_student(request.get_json(silent=True) or {}, creating=True)
    if err:
        return jsonify(success=False, message=err), 400

    # Auto-generate Username and Password
    v["username"] = v["email"]
    birth_year = v["dob"].strftime("%Y") if v["dob"] else "2000"
    v["password"] = v["firstname"] + birth_year

    def work(cur):
        cur.execute("SELECT 1 FROM users WHERE username=%s", (v["username"],))
        if cur.fetchone():
            raise psycopg2.IntegrityError("Username exists")

        cur.execute("INSERT INTO users (username, password_hash, role) "
                    "VALUES (%s, %s, 'Student') RETURNING user_id",
                    (v["username"], hash_pw(v["password"])))
        uid = cur.fetchone()[0]

        cur.execute("INSERT INTO Student (user_id, firstname, lastname, email, mobile_no, gender, dob, city) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uid, v["firstname"], v["lastname"], v["email"], v["mobile_no"] or None,
                     v["gender"] or None, v["dob"], v["city"]))
    try:
        run_tx(work)
    except psycopg2.IntegrityError:
        return jsonify(success=False, message="That email address is already registered."), 400
    
    # --- SEND EMAIL WITH CREDENTIALS ---
    # Run email in background so the "Save" button doesn't freeze
    threading.Thread(target=send_student_credentials, args=(v["email"], v["firstname"], v["password"])).start()
    return jsonify(success=True)

@app.route("/api/admin/student-list/<int:sid>", methods=["PUT"])
@admin
def admin_student_update(sid):
    v, err = clean_student(request.get_json(silent=True) or {}, creating=False)
    if err:
        return jsonify(success=False, message=err), 400

    # Auto-update Username to match Email
    v["username"] = v["email"]

    # --- NEW PASSWORD LOGIC ---
    # Recalculate password based on the NEW firstname and NEW birth year
    birth_year = v["dob"].strftime("%Y") if v["dob"] else "2000"
    new_password = v["firstname"] + birth_year
    new_password_hash = hash_pw(new_password)
    # --------------------------

    def work(cur):
        # Update BOTH username and password_hash in the users table
        cur.execute("""
            UPDATE users 
            SET username=%s, password_hash=%s 
            WHERE user_id=(SELECT user_id FROM Student WHERE student_id=%s)
        """, (v["username"], new_password_hash, sid))

        # Update Student details
        cur.execute("""
            UPDATE Student 
            SET firstname=%s, lastname=%s, email=%s, mobile_no=%s, gender=%s, 
                dob=%s, city=%s 
            WHERE student_id=%s
        """, (v["firstname"], v["lastname"], v["email"], v["mobile_no"] or None,
              v["gender"] or None, v["dob"], v["city"], sid))

        if cur.rowcount == 0:
            return False
        return True

    try:
        found = run_tx(work)
    except psycopg2.IntegrityError:
        return jsonify(success=False, message="That email already exists."), 400
        
    if not found:
        return jsonify(success=False, message="Student not found."), 404
        
    # Return the new password so the Admin can see it in the network tab or console if needed
    return jsonify(success=True, new_password=new_password)

@app.route("/api/admin/student-list/<int:sid>", methods=["DELETE"])
@admin
def admin_student_delete(sid):
    n = run("DELETE FROM users WHERE user_id=(SELECT user_id FROM Student WHERE student_id=%s)", (sid,))
    if not n:
        return jsonify(success=False, message="Student not found."), 404
    return jsonify(success=True)

if __name__ == "__main__":
    app.run(debug=True)
