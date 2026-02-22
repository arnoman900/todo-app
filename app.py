from flask import Flask, render_template, request, redirect
import sqlite3

app = Flask(__name__)
app.secret_key = "my_super_secret_key_12345"
app.jinja_env.globals.update(enumerate=enumerate)

# This connects to (or creates) the database file
def get_db():
    conn = sqlite3.connect("todo.db")
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn

# This creates the tasks table if it doesn't exist yet
def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.route("/")
def home():
    conn = get_db()
    todos = conn.execute("SELECT * FROM tasks").fetchall()
    conn.close()
    return render_template("index.html", todos=todos)

@app.route("/add", methods=["POST"])
def add():
    task = request.form.get("task")
    if task:
        conn = get_db()
        conn.execute("INSERT INTO tasks (task) VALUES (?)", (task,))
        conn.commit()
        conn.close()
    return redirect("/")

@app.route("/delete/<int:id>")
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect("/")

if __name__ == "__main__":
    init_db()  # set up the database when the app starts
    app.run(debug=False)