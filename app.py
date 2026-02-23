from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from apscheduler.schedulers.background import BackgroundScheduler
import psycopg2
import psycopg2.extras
import sqlite3
import os
import calendar as cal_module
from datetime import date, timedelta

app = Flask(__name__)
app.secret_key = "my_super_secret_key_12345"
bcrypt = Bcrypt(app)

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
mail = Mail(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

def get_db():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        conn = psycopg2.connect(database_url)
        return conn, True
    else:
        conn = sqlite3.connect("todo.db")
        conn.row_factory = sqlite3.Row
        return conn, False

def query(cur, is_postgres, sql, params=()):
    if not is_postgres:
        sql = sql.replace("%s", "?")
    cur.execute(sql, params)

def init_db():
    conn, is_postgres = get_db()
    cur = conn.cursor()
    if is_postgres:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS folders (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, user_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY, task TEXT NOT NULL, user_id INTEGER NOT NULL, done INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS task_completions (
            id SERIAL PRIMARY KEY, task_id INTEGER NOT NULL, completed_date TEXT NOT NULL,
            UNIQUE(task_id, completed_date))""")
        conn.commit()  # commit tables before altering

        for col, definition in [
            ("folder_id",    "INTEGER"),
            ("due_date",     "TEXT"),
            ("recurrence",   "TEXT DEFAULT 'none'"),
            ("due_time",     "TEXT DEFAULT ''"),
            ("due_time_end", "TEXT DEFAULT ''"),
        ]:
            cur.execute(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {col} {definition}")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
        conn.commit()

    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, user_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, user_id INTEGER NOT NULL, done INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS task_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, completed_date TEXT NOT NULL,
            UNIQUE(task_id, completed_date))""")
        conn.commit()  # commit tables before altering

        for col, definition in [
            ("folder_id",    "INTEGER"),
            ("due_date",     "TEXT"),
            ("recurrence",   "TEXT DEFAULT 'none'"),
            ("due_time",     "TEXT DEFAULT ''"),
            ("due_time_end", "TEXT DEFAULT ''"),
        ]:
            try:
                cur.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")
            except:
                pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
        except:
            pass
        conn.commit()

    cur.close()
    conn.close()

init_db()

def find_weekday_in_month(year, month, js_day_of_week, occ_str):
    py_weekday = (js_day_of_week - 1) % 7
    days_in_month = cal_module.monthrange(year, month)[1]
    occurrences = [d for d in range(1, days_in_month + 1)
                   if date(year, month, d).weekday() == py_weekday]
    if not occurrences:
        return None
    if occ_str == 'last':
        return date(year, month, occurrences[-1]).isoformat()
    idx = int(occ_str) - 1
    return date(year, month, occurrences[min(idx, len(occurrences)-1)]).isoformat()

def get_next_date(due_date_str, recurrence):
    if not due_date_str or not recurrence or recurrence == 'none':
        return None
    try:
        d = date.fromisoformat(due_date_str)
        if recurrence == 'daily':
            return (d + timedelta(days=1)).isoformat()
        elif recurrence == 'weekly':
            return (d + timedelta(weeks=1)).isoformat()
        elif recurrence == 'monthly':
            month = d.month + 1; year = d.year
            if month > 12: month = 1; year += 1
            last_day = cal_module.monthrange(year, month)[1]
            return date(year, month, min(d.day, last_day)).isoformat()
        elif recurrence.startswith('monthly_weekday_'):
            parts = recurrence.split('_')
            occ_str = parts[2]; js_dow = int(parts[3])
            month = d.month + 1; year = d.year
            if month > 12: month = 1; year += 1
            return find_weekday_in_month(year, month, js_dow, occ_str)
    except Exception as e:
        print(f"Error getting next date: {e}")
    return None

def send_reminders():
    with app.app_context():
        try:
            if not os.environ.get('MAIL_USERNAME'):
                return
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            conn, is_postgres = get_db()
            if is_postgres:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            else:
                cur = conn.cursor()
            query(cur, is_postgres, """
                SELECT tasks.task, tasks.due_date, tasks.due_time, tasks.due_time_end,
                       users.email, users.username, folders.name as calendar_name
                FROM tasks JOIN users ON tasks.user_id = users.id
                JOIN folders ON tasks.folder_id = folders.id
                WHERE tasks.due_date = %s AND tasks.done = 0
                AND users.email IS NOT NULL AND users.email != ''
            """, (tomorrow,))
            tasks = cur.fetchall()
            cur.close(); conn.close()
            user_tasks = {}
            for task in tasks:
                email = task['email']
                if email not in user_tasks:
                    user_tasks[email] = {'username': task['username'], 'tasks': []}
                user_tasks[email]['tasks'].append(task)
            for email, data in user_tasks.items():
                try:
                    task_lines = []
                    for t in data['tasks']:
                        time_str = f" at {t['due_time']}" if t.get('due_time') else ""
                        if t.get('due_time_end'):
                            time_str += f"–{t['due_time_end']}"
                        task_lines.append(f"  • {t['task']}{time_str} ({t['calendar_name']})")
                    msg = Message(
                        subject=f"📅 {len(data['tasks'])} task(s) due tomorrow!",
                        sender=os.environ.get('MAIL_USERNAME'),
                        recipients=[email]
                    )
                    msg.body = f"Hi {data['username']},\n\nYou have {len(data['tasks'])} task(s) due tomorrow ({tomorrow}):\n\n" + \
                               '\n'.join(task_lines) + "\n\nLog in to your calendar to check them off!\n\nBest,\nYour Calendar App"
                    mail.send(msg)
                except Exception as e:
                    print(f"Failed to send to {email}: {e}")
        except Exception as e:
            print(f"Reminder job error: {e}")

if os.environ.get('MAIL_USERNAME'):
    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(send_reminders, 'cron', hour=8, minute=0)
    scheduler.start()

class User(UserMixin):
    def __init__(self, id, username, email=None):
        self.id = id; self.username = username; self.email = email

@login_manager.user_loader
def load_user(user_id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone(); cur.close(); conn.close()
    if user:
        return User(user["id"], user["username"], user["email"] if "email" in user.keys() else None)
    return None

@app.route("/")
@login_required
def home():
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM folders WHERE user_id = %s", (current_user.id,))
    calendars = cur.fetchall()
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    calendar_stats = {}
    for cal in calendars:
        query(cur, is_postgres, "SELECT COUNT(*) as total FROM tasks WHERE folder_id = %s AND user_id = %s", (cal["id"], current_user.id))
        total = cur.fetchone()["total"]
        query(cur, is_postgres, "SELECT COUNT(*) as done FROM tasks WHERE folder_id = %s AND user_id = %s AND done = 1", (cal["id"], current_user.id))
        done = cur.fetchone()["done"]
        query(cur, is_postgres, "SELECT COUNT(*) as overdue FROM tasks WHERE folder_id = %s AND user_id = %s AND done = 0 AND due_date IS NOT NULL AND due_date != '' AND due_date < %s", (cal["id"], current_user.id, today))
        overdue = cur.fetchone()["overdue"]
        query(cur, is_postgres, "SELECT COUNT(*) as warning FROM tasks WHERE folder_id = %s AND user_id = %s AND done = 0 AND due_date IS NOT NULL AND due_date != '' AND due_date >= %s AND due_date <= %s", (cal["id"], current_user.id, today, tomorrow))
        warning = cur.fetchone()["warning"]
        calendar_stats[cal["id"]] = {"total": total, "done": done, "due": total - done, "overdue": overdue, "warning": warning, "ontrack": (total - done) - overdue - warning}
    cur.close(); conn.close()
    return render_template("index.html", calendars=calendars, calendar_stats=calendar_stats)

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        query(cur, is_postgres, "UPDATE users SET email = %s WHERE id = %s", (email, current_user.id))
        conn.commit(); flash("Settings saved!", "success"); cur.close(); conn.close()
        return redirect(url_for("settings"))
    query(cur, is_postgres, "SELECT * FROM users WHERE id = %s", (current_user.id,))
    user = cur.fetchone(); cur.close(); conn.close()
    return render_template("settings.html", user=user)

@app.route("/calendar/create", methods=["POST"])
@login_required
def create_calendar():
    name = request.form.get("name")
    if name:
        conn, is_postgres = get_db()
        cur = conn.cursor()
        query(cur, is_postgres, "INSERT INTO folders (name, user_id) VALUES (%s, %s)", (name, current_user.id))
        conn.commit(); cur.close(); conn.close()
    return redirect(url_for("home"))

@app.route("/calendar/delete/<int:calendar_id>")
@login_required
def delete_calendar(calendar_id):
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres, "DELETE FROM task_completions WHERE task_id IN (SELECT id FROM tasks WHERE folder_id = %s AND user_id = %s)", (calendar_id, current_user.id))
    query(cur, is_postgres, "DELETE FROM tasks WHERE folder_id = %s AND user_id = %s", (calendar_id, current_user.id))
    query(cur, is_postgres, "DELETE FROM folders WHERE id = %s AND user_id = %s", (calendar_id, current_user.id))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for("home"))

@app.route("/api/calendar/rename/<int:calendar_id>", methods=["POST"])
@login_required
def rename_calendar(calendar_id):
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres, "UPDATE folders SET name = %s WHERE id = %s AND user_id = %s", (name, calendar_id, current_user.id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True, "name": name})

@app.route("/calendar/<int:calendar_id>")
@login_required
def view_calendar(calendar_id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM folders WHERE id = %s AND user_id = %s", (calendar_id, current_user.id))
    calendar = cur.fetchone()
    if not calendar:
        return redirect(url_for("home"))
    query(cur, is_postgres, "SELECT * FROM tasks WHERE folder_id = %s AND user_id = %s", (calendar_id, current_user.id))
    tasks = cur.fetchall()
    task_ids = [t['id'] for t in tasks]
    completions = {}
    if task_ids:
        placeholders = ','.join(['%s'] * len(task_ids)) if is_postgres else ','.join(['?'] * len(task_ids))
        cur.execute(f"SELECT task_id, completed_date FROM task_completions WHERE task_id IN ({placeholders})", task_ids)
        for row in cur.fetchall():
            tid = row['task_id']
            if tid not in completions:
                completions[tid] = []
            completions[tid].append(row['completed_date'])
    cur.close(); conn.close()
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    return render_template("my_calendar.html", calendar=calendar, tasks=tasks,
                           completions=completions, today=today, tomorrow=tomorrow)

@app.route("/api/tasks")
@login_required
def api_tasks():
    calendar_id = request.args.get("calendar_id")
    date_str = request.args.get("date")
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM tasks WHERE folder_id = %s AND user_id = %s AND due_date = %s", (calendar_id, current_user.id, date_str))
    tasks = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(t) for t in tasks])

@app.route("/api/task/add", methods=["POST"])
@login_required
def api_add_task():
    data = request.get_json()
    task_name = data.get("task")
    due_date = data.get("due_date")
    due_time = data.get("due_time", "")
    due_time_end = data.get("due_time_end", "")
    calendar_id = data.get("calendar_id")
    recurrence = data.get("recurrence", "none")
    if not task_name or not calendar_id:
        return jsonify({"error": "Missing data"}), 400
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres,
          "INSERT INTO tasks (task, user_id, folder_id, due_date, due_time, due_time_end, recurrence) VALUES (%s, %s, %s, %s, %s, %s, %s)",
          (task_name, current_user.id, calendar_id, due_date, due_time, due_time_end, recurrence))
    conn.commit()
    if is_postgres:
        cur.execute("SELECT * FROM tasks WHERE user_id = %s ORDER BY id DESC LIMIT 1", (current_user.id,))
    else:
        cur.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1", (current_user.id,))
    new_task = cur.fetchone(); cur.close(); conn.close()
    return jsonify(dict(new_task))

@app.route("/api/task/edit/<int:task_id>", methods=["POST"])
@login_required
def api_edit_task(task_id):
    data = request.get_json()
    task_name = data.get("task")
    due_date = data.get("due_date")
    due_time = data.get("due_time", "")
    due_time_end = data.get("due_time_end", "")
    recurrence = data.get("recurrence", "none")
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres,
          "UPDATE tasks SET task = %s, due_date = %s, due_time = %s, due_time_end = %s, recurrence = %s WHERE id = %s AND user_id = %s",
          (task_name, due_date, due_time, due_time_end, recurrence, task_id, current_user.id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

@app.route("/api/task/delete/<int:task_id>", methods=["POST"])
@login_required
def api_delete_task(task_id):
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres, "DELETE FROM task_completions WHERE task_id = %s", (task_id,))
    query(cur, is_postgres, "DELETE FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

@app.route("/api/task/toggle/<int:task_id>", methods=["POST"])
@login_required
def api_toggle_task(task_id):
    data = request.get_json() or {}
    toggle_date = data.get("date")
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    task = cur.fetchone()
    if not task:
        cur.close(); conn.close()
        return jsonify({"error": "Not found"}), 404
    is_recurring = task["recurrence"] and task["recurrence"] != "none"
    result = {"success": True}
    if is_recurring and toggle_date:
        query(cur, is_postgres, "SELECT id FROM task_completions WHERE task_id = %s AND completed_date = %s", (task_id, toggle_date))
        existing = cur.fetchone()
        if existing:
            query(cur, is_postgres, "DELETE FROM task_completions WHERE task_id = %s AND completed_date = %s", (task_id, toggle_date))
            result["done"] = 0
        else:
            try:
                query(cur, is_postgres, "INSERT INTO task_completions (task_id, completed_date) VALUES (%s, %s)", (task_id, toggle_date))
            except:
                conn.rollback()
            result["done"] = 1
            result["completed_date"] = toggle_date
    else:
        new_status = 0 if task["done"] else 1
        query(cur, is_postgres, "UPDATE tasks SET done = %s WHERE id = %s", (new_status, task_id))
        result["done"] = new_status
        if new_status == 1 and is_recurring:
            next_date = get_next_date(task["due_date"], task["recurrence"])
            if next_date:
                query(cur, is_postgres,
                      "INSERT INTO tasks (task, user_id, folder_id, due_date, due_time, due_time_end, recurrence) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                      (task["task"], current_user.id, task["folder_id"], next_date,
                       task.get("due_time", ""), task.get("due_time_end", ""), task["recurrence"]))
                if is_postgres:
                    cur.execute("SELECT * FROM tasks WHERE user_id = %s ORDER BY id DESC LIMIT 1", (current_user.id,))
                else:
                    cur.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1", (current_user.id,))
                new_task = cur.fetchone()
                result["new_task"] = dict(new_task)
    conn.commit(); cur.close(); conn.close()
    return jsonify(result)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
        try:
            conn, is_postgres = get_db()
            cur = conn.cursor()
            query(cur, is_postgres, "INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
            conn.commit(); cur.close(); conn.close()
            flash("Account created! Please log in.", "success")
            return redirect(url_for("login"))
        except:
            flash("Username already exists. Try another.", "error")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        conn, is_postgres = get_db()
        if is_postgres:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cur = conn.cursor()
        query(cur, is_postgres, "SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone(); cur.close(); conn.close()
        if user and bcrypt.check_password_hash(user["password"], password):
            login_user(User(user["id"], user["username"], user["email"] if "email" in user.keys() else None))
            return redirect(url_for("home"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=False)