import os
import random
import secrets
import string
import smtplib
import ssl
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL")


from flask import Flask, render_template, request, redirect, url_for, flash, g

import psycopg2
import psycopg2.extras

app = Flask(__name__)




# ---- Config via environment variables ----

app.secret_key = os.environ.get("SECRET_KEY", "dev_only_change_me")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:yourpassword@localhost:5432/secret_santa"
)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "letmein")

EMAIL_USER = os.environ.get("EMAIL_USER")          # your Gmail address
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")  # Gmail app password
EMAIL_FROM = os.environ.get("EMAIL_FROM", EMAIL_USER)


# ---------- DB helpers ----------

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    cur = conn.cursor()

    # registrations: who joined
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS registrations (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            email TEXT,
            note TEXT,
            secret_id TEXT NOT NULL UNIQUE
        );
        """
    )

    # assignments: final giver->receiver mapping
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS assignments (
            id SERIAL PRIMARY KEY,
            giver_name TEXT NOT NULL,
            receiver_name TEXT NOT NULL
        );
        """
    )

    conn.commit()
    cur.close()

with app.app_context():
    try:
        init_db()
    except Exception as e:
        print("DB init error (can ignore if DB not ready yet):", e)

# ---------- Logic helpers ----------

def generate_secret_id():
    """Generate a short, human-readable secret ID like AB12-CD34."""
    alphabet = string.ascii_uppercase + string.digits
    part1 = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{part1}-{part2}"


def generate_assignments(names):
    """
    Generate Secret Santa assignments where:
    - No one gets themselves
    """
    if len(names) < 2:
        raise ValueError("Need at least 2 participants to generate assignments.")

    max_attempts = 10000

    for _ in range(max_attempts):
        receivers = names[:]
        random.shuffle(receivers)

        if all(giver != receiver for giver, receiver in zip(names, receivers)):
            return dict(zip(names, receivers))

    raise RuntimeError("Could not find a valid assignment. Try again.")


def send_email(to_email, subject, body):
    if not SENDGRID_API_KEY or not SENDGRID_FROM_EMAIL:
        print("SendGrid not configured. Skipping email.")
        return

    if not to_email:
        print("No email provided. Skipping send.")
        return

    try:
        message = Mail(
            from_email=SENDGRID_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=body,
        )

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)

        print("SendGrid status:", response.status_code)

    except Exception as e:
        print("SendGrid email failed:", e)


def send_assignment_email(name, email, receiver_name, receiver_note, event_name, secret_id):
    if not email:
        print(f"No email for {name}, skipping send.")
        return

    subject = f"[{event_name}] Your Secret Santa Friend 🎄"

    html_body = f"""
    <p>Hi {name},</p>

    <p>Here’s your Secret Santa assignment for our family Christmas game.</p>

    <p><strong>Your Christmas friend: {receiver_name}</strong></p>
    """

    if receiver_note:
        html_body += f"""
        <p>Their greetings / note:</p>
        <blockquote>{receiver_note}</blockquote>
        """

    html_body += f"""
    <p><strong>Your Secret ID</strong> (to view this again on the website): {secret_id}</p>

    <p>If anything looks wrong, just ping me on WhatsApp.</p>

    <p>– Amith</p>
    """

    print("---- Email (SIMULATED SEND) ----")
    print(f"To: {email}")
    print("Subject:", subject)
    print(html_body)
    print("--------------------------------")

    # Actual send (HTML)
    send_email(email, subject, html_body)

# ---------- Routes ----------

@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS cnt FROM assignments;")
    row = cur.fetchone()
    assignments_generated = row["cnt"] > 0

    cur.close()
    return render_template("index.html", assignments_generated=assignments_generated)


@app.route("/register", methods=["GET", "POST"])
def register():
    """
    Registration page:
    GET: show form
    POST: save name, email, note, generate secret ID
    """
    conn = get_db()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        note = request.form.get("note", "").strip()

        # Basic validation
        if not name:
          flash("Name is required.", "error")
          return redirect(url_for("register"))


        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Check if this name already exists
        cur.execute(
            "SELECT id, secret_id FROM registrations WHERE name = %s;",
            (name,),
        )
        existing = cur.fetchone()

        if existing is not None:
            cur.close()
            flash(
                "This name is already registered. If this is you, use your existing Secret ID. "
                "Otherwise contact the organizer.",
                "error",
            )
            return redirect(url_for("register_done", secret_id=existing["secret_id"]))

        # Generate unique secret ID
        secret_id = generate_secret_id()

        cur.execute(
            "SELECT 1 FROM registrations WHERE secret_id = %s;",
            (secret_id,),
        )
        while cur.fetchone() is not None:
            secret_id = generate_secret_id()
            cur.execute(
                "SELECT 1 FROM registrations WHERE secret_id = %s;",
                (secret_id,),
            )

        # Insert into DB
        cur.execute(
            """
            INSERT INTO registrations (name, email, note, secret_id)
            VALUES (%s, %s, %s, %s);
            """,
            (name, email, note, secret_id),
        )
        conn.commit()
        cur.close()

        flash("You are registered! Save your Secret ID.", "success")
        return redirect(url_for("register_done", secret_id=secret_id))

    # GET
    return render_template("register.html")


@app.route("/register/done/<secret_id>")
def register_done(secret_id):
    """
    Page shown after successful registration, shows the Secret ID.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT name, secret_id FROM registrations WHERE secret_id = %s;",
        (secret_id,),
    )
    row = cur.fetchone()
    cur.close()

    if row is None:
        flash("Invalid Secret ID.", "error")
        return redirect(url_for("index"))

    return render_template("register_done.html", name=row["name"], secret_id=row["secret_id"])


@app.route("/reveal", methods=["GET", "POST"])
def reveal():
    conn = get_db()

    if request.method == "POST":
        secret_id = request.form.get("secret_id", "").strip().upper()

        if not secret_id:
            flash("Please enter your Secret ID.", "error")
            return redirect(url_for("reveal"))

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Find registration by secret_id
        cur.execute(
            "SELECT name FROM registrations WHERE secret_id = %s;",
            (secret_id,),
        )
        reg = cur.fetchone()

        if reg is None:
            cur.close()
            flash("Invalid Secret ID.", "error")
            return redirect(url_for("reveal"))

        name = reg["name"]

        # Check if assignments exist
        cur.execute("SELECT COUNT(*) AS cnt FROM assignments;")
        row = cur.fetchone()
        assignments_generated = row["cnt"] > 0

        if not assignments_generated:
            cur.close()
            flash("Assignments have not been generated yet. Please come back later.", "info")
            return redirect(url_for("reveal"))

        # Get this person's receiver
        cur.execute(
            "SELECT receiver_name FROM assignments WHERE giver_name = %s;",
            (name,),
        )
        assn = cur.fetchone()

        if assn is None:
            cur.close()
            flash("No assignment found for you. Contact the organizer.", "error")
            return redirect(url_for("reveal"))

        receiver_name = assn["receiver_name"]

        # Fetch receiver note
        cur.execute(
            "SELECT note FROM registrations WHERE name = %s;",
            (receiver_name,),
        )
        note_row = cur.fetchone()
        cur.close()

        receiver_note = note_row["note"] if note_row and note_row["note"] is not None else ""

        return render_template(
            "reveal.html",
            name=name,
            receiver_name=receiver_name,
            receiver_note=receiver_note,
            secret_id=secret_id,
        )

    # GET
    return render_template("reveal_form.html")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = get_db()

    if request.method == "POST":
        admin_password = request.form.get("admin_password", "")

        if admin_password != ADMIN_PASSWORD:
            flash("Wrong admin password.", "error")
            return redirect(url_for("admin"))

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Clear previous assignments so we can regenerate with all current participants
        cur.execute("DELETE FROM assignments;")
        conn.commit()


        # Get all registered participant names
        cur.execute("SELECT name FROM registrations;")
        reg_rows = cur.fetchall()
        participant_names = [r["name"] for r in reg_rows]

        if len(participant_names) < 2:
            cur.close()
            flash("Not enough registered participants to generate assignments.", "error")
            return redirect(url_for("admin"))

        # Generate mapping
        try:
            mapping = generate_assignments(participant_names)
        except Exception as e:
            cur.close()
            flash(f"Error generating assignments: {e}", "error")
            return redirect(url_for("admin"))

        # Insert assignments
        for giver_name, receiver_name in mapping.items():
            cur.execute(
                "INSERT INTO assignments (giver_name, receiver_name) VALUES (%s, %s);",
                (giver_name, receiver_name),
            )

        conn.commit()

        # Now send emails
        event_name = "Mom-side Secret Santa 2025"

        for giver_name, receiver_name in mapping.items():
            # Fetch giver email + secret_id
            cur.execute(
                "SELECT email, secret_id FROM registrations WHERE name = %s;",
                (giver_name,),
            )
            giver_row = cur.fetchone()

            # Fetch receiver note
            cur.execute(
                "SELECT note FROM registrations WHERE name = %s;",
                (receiver_name,),
            )
            rec_row = cur.fetchone()

            receiver_note = rec_row["note"] if rec_row and rec_row["note"] is not None else ""

            send_assignment_email(
                name=giver_name,
                email=giver_row["email"],
                receiver_name=receiver_name,
                receiver_note=receiver_note,
                event_name=event_name,
                secret_id=giver_row["secret_id"],
            )

        cur.close()
        flash("Assignments generated and emails sent (check logs / inbox).", "success")
        return redirect(url_for("admin"))

    # GET
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Registered participants
    cur.execute("SELECT name FROM registrations ORDER BY name;")
    reg_rows = cur.fetchall()
    registered_names = [r["name"] for r in reg_rows]

    # Assignments status
    cur.execute("SELECT COUNT(*) AS cnt FROM assignments;")
    row = cur.fetchone()
    assignments_generated = row["cnt"] > 0

    # Current assignments (if any)
    cur.execute("SELECT giver_name, receiver_name FROM assignments;")
    assn_rows = cur.fetchall()
    assignments = {r["giver_name"]: r["receiver_name"] for r in assn_rows}

    cur.close()

    return render_template(
        "admin.html",
        registered_names=registered_names,
        assignments_generated=assignments_generated,
        assignments=assignments,
    )


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)

