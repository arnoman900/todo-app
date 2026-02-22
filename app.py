from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
import psycopg2
import psycopg2.extras
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
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
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
    conn.commit()
    cur.close()
    conn.close()

# -------------------------
# User class for Flask-Login
# -------------------------

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
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
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM tasks WHERE user_id = %s", (current_user.id,))
    todos = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("index.html", todos=todos)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
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
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
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

@app.route("/add", methods=["POST"])
@login_required
def add():
    task = request.form.get("task")
    if task:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO tasks (task, user_id) VALUES (%s, %s)", (task, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("home"))

@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id = %s AND user_id = %s", (id, current_user.id))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("home"))

@app.route("/toggle/<int:id>")
@login_required
def toggle(id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM tasks WHERE id = %s AND user_id = %s", (id, current_user.id))
    task = cur.fetchone()
    if task:
        new_status = 0 if task["done"] else 1
        cur.execute("UPDATE tasks SET done = %s WHERE id = %s", (new_status, id))
        conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("home"))

if __name__ == "__main__":
    init_db()
    app.run(debug=False)