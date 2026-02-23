from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
import psycopg2
import psycopg2.extras
import sqlite3
import os
from datetime import date, timedelta

app = Flask(__name__)
app.secret_key = "my_super_secret_key_12345"
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

# -------------------------
# Database helpers
# -------------------------

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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                task TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                done INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN folder_id INTEGER")
        except:
            conn.rollback()
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
        except:
            conn.rollback()
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                done INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN folder_id INTEGER")
        except:
            pass
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
        except:
            pass

    conn.commit()
    cur.close()
    conn.close()

init_db()

# -------------------------
# User class
# -------------------------

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if user:
        return User(user["id"], user["username"])
    return None

# -------------------------
# Main page — list of calendars
# -------------------------

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

        calendar_stats[cal["id"]] = {
            "total": total,
            "done": done,
            "due": total - done,
            "overdue": overdue,
            "warning": warning,
            "ontrack": (total - done) - overdue - warning
        }

    cur.close()
    conn.close()
    return render_template("index.html", calendars=calendars, calendar_stats=calendar_stats)

# -------------------------
# Calendar CRUD
# -------------------------

@app.route("/calendar/create", methods=["POST"])
@login_required
def create_calendar():
    name = request.form.get("name")
    if name:
        conn, is_postgres = get_db()
        cur = conn.cursor()
        query(cur, is_postgres, "INSERT INTO folders (name, user_id) VALUES (%s, %s)", (name, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("home"))

@app.route("/calendar/delete/<int:calendar_id>")
@login_required
def delete_calendar(calendar_id):
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres, "DELETE FROM tasks WHERE folder_id = %s AND user_id = %s", (calendar_id, current_user.id))
    query(cur, is_postgres, "DELETE FROM folders WHERE id = %s AND user_id = %s", (calendar_id, current_user.id))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("home"))

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
    cur.close()
    conn.close()
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    return render_template("my_calendar.html", calendar=calendar, tasks=tasks, today=today, tomorrow=tomorrow)

# -------------------------
# Task API (JSON) — used by modal
# -------------------------

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
    tasks = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(t) for t in tasks])

@app.route("/api/task/add", methods=["POST"])
@login_required
def api_add_task():
    data = request.get_json()
    task_name = data.get("task")
    due_date = data.get("due_date")
    calendar_id = data.get("calendar_id")
    if not task_name or not calendar_id:
        return jsonify({"error": "Missing data"}), 400
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "INSERT INTO tasks (task, user_id, folder_id, due_date) VALUES (%s, %s, %s, %s)", (task_name, current_user.id, calendar_id, due_date))
    conn.commit()
    # Return the new task
    if is_postgres:
        cur.execute("SELECT * FROM tasks WHERE user_id = %s ORDER BY id DESC LIMIT 1", (current_user.id,))
    else:
        cur.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1", (current_user.id,))
    new_task = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify(dict(new_task))

@app.route("/api/task/edit/<int:task_id>", methods=["POST"])
@login_required
def api_edit_task(task_id):
    data = request.get_json()
    task_name = data.get("task")
    due_date = data.get("due_date")
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres, "UPDATE tasks SET task = %s, due_date = %s WHERE id = %s AND user_id = %s", (task_name, due_date, task_id, current_user.id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/task/delete/<int:task_id>", methods=["POST"])
@login_required
def api_delete_task(task_id):
    conn, is_postgres = get_db()
    cur = conn.cursor()
    query(cur, is_postgres, "DELETE FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/task/toggle/<int:task_id>", methods=["POST"])
@login_required
def api_toggle_task(task_id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    task = cur.fetchone()
    if task:
        new_status = 0 if task["done"] else 1
        query(cur, is_postgres, "UPDATE tasks SET done = %s WHERE id = %s", (new_status, task_id))
        conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "done": new_status})

# -------------------------
# Auth routes
# -------------------------

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
            conn.commit()
            cur.close()
            conn.close()
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
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user and bcrypt.check_password_hash(user["password"], password):
            login_user(User(user["id"], user["username"]))
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