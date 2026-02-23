from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
import psycopg2
import psycopg2.extras
import sqlite3
import os

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
                folder_id INTEGER NOT NULL,
                done INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (folder_id) REFERENCES folders (id)
            )
        """)
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
                folder_id INTEGER NOT NULL,
                done INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (folder_id) REFERENCES folders (id)
            )
        """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

# -------------------------
# User class for Flask-Login
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
# Routes
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
    folders = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("index.html", folders=folders)

@app.route("/folder/create", methods=["POST"])
@login_required
def create_folder():
    name = request.form.get("name")
    if name:
        conn, is_postgres = get_db()
        cur = conn.cursor()
        query(cur, is_postgres, "INSERT INTO folders (name, user_id) VALUES (%s, %s)", (name, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("home"))

@app.route("/folder/<int:folder_id>")
@login_required
def folder(folder_id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    # Get the folder (make sure it belongs to this user)
    query(cur, is_postgres, "SELECT * FROM folders WHERE id = %s AND user_id = %s", (folder_id, current_user.id))
    folder = cur.fetchone()
    if not folder:
        return redirect(url_for("home"))
    # Get tasks in this folder
    query(cur, is_postgres, "SELECT * FROM tasks WHERE folder_id = %s AND user_id = %s", (folder_id, current_user.id))
    todos = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("folder.html", folder=folder, todos=todos)

@app.route("/folder/delete/<int:folder_id>")
@login_required
def delete_folder(folder_id):
    conn, is_postgres = get_db()
    cur = conn.cursor()
    # Delete all tasks in the folder first
    query(cur, is_postgres, "DELETE FROM tasks WHERE folder_id = %s AND user_id = %s", (folder_id, current_user.id))
    # Then delete the folder
    query(cur, is_postgres, "DELETE FROM folders WHERE id = %s AND user_id = %s", (folder_id, current_user.id))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("home"))

@app.route("/add/<int:folder_id>", methods=["POST"])
@login_required
def add(folder_id):
    task = request.form.get("task")
    if task:
        conn, is_postgres = get_db()
        cur = conn.cursor()
        query(cur, is_postgres, "INSERT INTO tasks (task, user_id, folder_id) VALUES (%s, %s, %s)", (task, current_user.id, folder_id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("folder", folder_id=folder_id))

@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    # Get folder_id before deleting so we can redirect back
    query(cur, is_postgres, "SELECT * FROM tasks WHERE id = %s AND user_id = %s", (id, current_user.id))
    task = cur.fetchone()
    folder_id = task["folder_id"] if task else None
    query(cur, is_postgres, "DELETE FROM tasks WHERE id = %s AND user_id = %s", (id, current_user.id))
    conn.commit()
    cur.close()
    conn.close()
    if folder_id:
        return redirect(url_for("folder", folder_id=folder_id))
    return redirect(url_for("home"))

@app.route("/toggle/<int:id>")
@login_required
def toggle(id):
    conn, is_postgres = get_db()
    if is_postgres:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    query(cur, is_postgres, "SELECT * FROM tasks WHERE id = %s AND user_id = %s", (id, current_user.id))
    task = cur.fetchone()
    if task:
        new_status = 0 if task["done"] else 1
        folder_id = task["folder_id"]
        query(cur, is_postgres, "UPDATE tasks SET done = %s WHERE id = %s", (new_status, id))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for("folder", folder_id=folder_id))
    cur.close()
    conn.close()
    return redirect(url_for("home"))

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