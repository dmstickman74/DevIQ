import csv
import io
import json
import sqlite3
import os
import shutil
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "crm.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS saved_filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT 'contacts',
        filters TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS marketing_lists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        target TEXT NOT NULL DEFAULT 'contacts',
        filters TEXT NOT NULL,
        created_by TEXT DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS custom_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id TEXT,
        company_id TEXT,
        type TEXT NOT NULL,
        subject TEXT,
        body TEXT,
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ca_contact ON custom_activities(contact_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ca_company ON custom_activities(company_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        email TEXT DEFAULT '',
        role TEXT NOT NULL DEFAULT 'viewer',
        password_hash TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        last_login TEXT
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT DEFAULT '',
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        details TEXT,
        timestamp TEXT DEFAULT (datetime('now'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp)")
    db.execute("""CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS sales_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year TEXT NOT NULL,
        rep_id TEXT DEFAULT '',
        publication TEXT DEFAULT '',
        target_amount REAL NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS scheduled_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id TEXT,
        company_id TEXT,
        type TEXT NOT NULL DEFAULT 'task',
        subject TEXT NOT NULL,
        description TEXT DEFAULT '',
        due_date TEXT NOT NULL,
        due_time TEXT DEFAULT '',
        assigned_to TEXT DEFAULT 'admin',
        priority TEXT DEFAULT 'normal',
        status TEXT DEFAULT 'pending',
        completed_at TEXT,
        created_by TEXT DEFAULT 'admin',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_activities(due_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sched_status ON scheduled_activities(status)")
    # Migrate: add columns that may be missing on older databases
    c = db.cursor()
    for tbl, col, col_def in [
        ('marketing_lists', 'created_by', "TEXT DEFAULT 'admin'"),
    ]:
        c.execute(f"PRAGMA table_info({tbl})")
        existing = {row[1] for row in c.fetchall()}
        if col not in existing:
            db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_def}")
    c = db.cursor()
    c.execute("SELECT COUNT(*) FROM admin_users")
    if c.fetchone()[0] == 0:
        pw_hash = hashlib.sha256("admin".encode()).hexdigest()
        db.execute("INSERT INTO admin_users (username, display_name, email, role, password_hash) VALUES (?, ?, ?, ?, ?)",
                   ("admin", "Administrator", "", "admin", pw_hash))
    db.commit()
    db.close()

init_db()

def apply_advanced_filters(cursor, table, filters_json, base_where=None, base_params=None):
    where = list(base_where or [])
    params = list(base_params or [])

    if not filters_json:
        return where, params

    filters = json.loads(filters_json) if isinstance(filters_json, str) else filters_json

    col_fields = {"firstname","lastname","email","phone","company","jobtitle",
                  "city","state","country","owner_name","lifecyclestage","hs_lead_status"}

    for f in filters:
        field = f.get("field", "")
        op = f.get("op", "contains")
        val = f.get("value", "")
        if not field:
            continue
        if not val and op not in ("is_empty", "is_not_empty"):
            continue

        is_col = field in col_fields
        if is_col:
            ref = field
        else:
            ref = f"json_extract(all_properties, '$.{field}')"

        if op == "equals":
            where.append(f"{ref} = ?")
            params.append(val)
        elif op == "not_equals":
            where.append(f"({ref} IS NULL OR {ref} != ?)")
            params.append(val)
        elif op == "contains":
            where.append(f"{ref} LIKE ?")
            params.append(f"%{val}%")
        elif op == "not_contains":
            where.append(f"({ref} IS NULL OR {ref} NOT LIKE ?)")
            params.append(f"%{val}%")
        elif op == "starts_with":
            where.append(f"{ref} LIKE ?")
            params.append(f"{val}%")
        elif op == "is_empty":
            where.append(f"({ref} IS NULL OR {ref} = '')")
        elif op == "is_not_empty":
            where.append(f"{ref} IS NOT NULL AND {ref} != ''")
        elif op == "one_of":
            values = val if isinstance(val, list) else [v.strip() for v in val.split(",") if v.strip()]
            if values:
                placeholders = ",".join(["?"] * len(values))
                where.append(f"{ref} IN ({placeholders})")
                params.extend(values)
        elif op == "not_one_of":
            values = val if isinstance(val, list) else [v.strip() for v in val.split(",") if v.strip()]
            if values:
                placeholders = ",".join(["?"] * len(values))
                where.append(f"({ref} IS NULL OR {ref} NOT IN ({placeholders}))")
                params.extend(values)

    return where, params


@app.route("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "index.html"))

@app.route("/logo.png")
def logo():
    return send_file(os.path.join(os.path.dirname(__file__), "logo.png"), mimetype="image/png")

@app.route("/api/stats")
def stats():
    db = get_db()
    c = db.cursor()
    result = {}
    c.execute("SELECT COUNT(*) FROM contacts")
    result["contacts"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM companies")
    result["companies"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM activities")
    result["activities"] = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT owner_name) FROM contacts WHERE owner_name != ''")
    result["owners"] = c.fetchone()[0]
    c.execute("SELECT type, COUNT(*) as cnt FROM activities GROUP BY type ORDER BY cnt DESC")
    result["activity_breakdown"] = {r["type"]: r["cnt"] for r in c.fetchall()}
    db.close()
    return jsonify(result)

def _parse_sm_date(d):
    """Parse MM/DD/YY HH:MM:SS to ISO date string."""
    if not d:
        return None
    try:
        parts = d.split(" ")[0].split("/")
        m, day, y = int(parts[0]), int(parts[1]), int(parts[2])
        y = y + 2000 if y < 100 else y
        return f"{y:04d}-{m:02d}-{day:02d}"
    except Exception:
        return None

@app.route("/api/dashboard")
def dashboard():
    db = get_db()
    c = db.cursor()
    result = {}
    c.execute("SELECT COUNT(*) FROM contacts")
    result["total_contacts"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM companies")
    result["total_companies"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM sm_contracts")
    result["total_contracts"] = c.fetchone()[0]

    c.execute("""SELECT a.type, a.subject, a.timestamp, a.owner_name,
                 a.contact_id, c.firstname, c.lastname, c.company
                 FROM activities a LEFT JOIN contacts c ON a.contact_id = c.id
                 ORDER BY a.timestamp DESC LIMIT 15""")
    result["recent_activities"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT id, contact_id, company_id, type, subject, description,
                 due_date, due_time, assigned_to, priority, status
                 FROM scheduled_activities WHERE status = 'pending'
                 ORDER BY due_date ASC, due_time ASC LIMIT 15""")
    result["upcoming_tasks"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT id, contact_id, company_id, type, subject, due_date, status, completed_at
                 FROM scheduled_activities WHERE status = 'completed'
                 ORDER BY completed_at DESC LIMIT 10""")
    result["completed_tasks"] = [dict(r) for r in c.fetchall()]

    overdue = c.execute("""SELECT COUNT(*) FROM scheduled_activities
                           WHERE status = 'pending' AND due_date < date('now')""").fetchone()[0]
    result["overdue_tasks"] = overdue

    c.execute("SELECT id, name, description, created_at, created_by FROM marketing_lists ORDER BY created_at DESC LIMIT 5")
    result["recent_lists"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT ph.publication, ph.issue_date, ph.account_name, ph.ad_cost,
                 ph.type_ad, ph.ad_size, sc.id as account_id
                 FROM sm_page_history ph
                 LEFT JOIN sm_companies sc ON ph.account_name = sc.company
                 WHERE ph.canceled = 0
                 ORDER BY ph.created_date DESC LIMIT 10""")
    recent_sales = []
    for r in c.fetchall():
        row = dict(r)
        row["issue_date_iso"] = _parse_sm_date(row["issue_date"])
        recent_sales.append(row)
    result["recent_sales"] = recent_sales

    db.close()
    return jsonify(result)

# ── Scheduled Activities ──
@app.route("/api/scheduled-activities")
def list_scheduled_activities():
    db = get_db()
    status = request.args.get("status", "")
    assigned = request.args.get("assigned_to", "")
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if assigned:
        where.append("assigned_to = ?")
        params.append(assigned)
    rows = db.execute(f"""SELECT sa.*, c.firstname, c.lastname, c.company as contact_company
                          FROM scheduled_activities sa
                          LEFT JOIN contacts c ON sa.contact_id = c.id
                          WHERE {' AND '.join(where)}
                          ORDER BY CASE WHEN sa.status='pending' THEN 0 ELSE 1 END,
                                   sa.due_date ASC, sa.due_time ASC""", params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/scheduled-activities", methods=["POST"])
def create_scheduled_activity():
    data = request.get_json()
    db = get_db()
    db.execute("""INSERT INTO scheduled_activities
                  (contact_id, company_id, type, subject, description, due_date, due_time,
                   assigned_to, priority, created_by)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
               (data.get("contact_id", ""), data.get("company_id", ""),
                data.get("type", "task"), data["subject"], data.get("description", ""),
                data["due_date"], data.get("due_time", ""),
                data.get("assigned_to", "admin"), data.get("priority", "normal"),
                data.get("created_by", "admin")))
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    log_audit(db, "create", "scheduled_activity", str(aid), data.get("subject", ""), "admin")
    db.close()
    return jsonify({"id": aid})

@app.route("/api/scheduled-activities/<int:aid>", methods=["PUT"])
def update_scheduled_activity(aid):
    data = request.get_json()
    db = get_db()
    sets, params = [], []
    for k in ("subject", "description", "due_date", "due_time", "assigned_to",
              "priority", "status", "type", "contact_id", "company_id"):
        if k in data:
            sets.append(f"{k} = ?")
            params.append(data[k])
    if data.get("status") == "completed":
        sets.append("completed_at = datetime('now')")
    if sets:
        params.append(aid)
        db.execute(f"UPDATE scheduled_activities SET {', '.join(sets)} WHERE id = ?", params)
        db.commit()
        log_audit(db, "update", "scheduled_activity", str(aid), json.dumps(data), "admin")
    db.close()
    return jsonify({"ok": True})

@app.route("/api/scheduled-activities/<int:aid>", methods=["DELETE"])
def delete_scheduled_activity(aid):
    db = get_db()
    db.execute("DELETE FROM scheduled_activities WHERE id = ?", (aid,))
    db.commit()
    log_audit(db, "delete", "scheduled_activity", str(aid), "", "admin")
    db.close()
    return jsonify({"ok": True})

# ── Create Contact / Company ──
@app.route("/api/contacts", methods=["POST"])
def create_contact():
    data = request.get_json()
    db = get_db()
    cid = data.get("id") or f"local-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    cols = ["id"]
    vals = [cid]
    for k in ("firstname", "lastname", "email", "phone", "company", "jobtitle",
              "city", "state", "country", "owner_name", "lifecyclestage", "hs_lead_status"):
        if k in data:
            cols.append(k)
            vals.append(data[k])
    cols.append("createdate")
    vals.append(datetime.now().isoformat())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    db.execute(f"INSERT INTO contacts ({col_str}) VALUES ({placeholders})", vals)
    db.commit()
    log_audit(db, "create", "contact", cid, f"{data.get('firstname','')} {data.get('lastname','')}", "admin")
    db.close()
    return jsonify({"id": cid})

@app.route("/api/companies", methods=["POST"])
def create_company():
    data = request.get_json()
    db = get_db()
    cid = data.get("id") or f"local-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    cols = ["id"]
    vals = [cid]
    for k in ("name", "domain", "industry", "city", "state", "country",
              "phone", "owner_name"):
        if k in data:
            cols.append(k)
            vals.append(data[k])
    cols.append("createdate")
    vals.append(datetime.now().isoformat())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    db.execute(f"INSERT INTO companies ({col_str}) VALUES ({placeholders})", vals)
    db.commit()
    log_audit(db, "create", "company", cid, data.get("name", ""), "admin")
    db.close()
    return jsonify({"id": cid})

# ── Sales by Issue Report ──
@app.route("/api/reports/sales-by-issue")
def report_sales_by_issue():
    db = get_db()
    pub = request.args.get("publication", "")
    year = request.args.get("year", "")
    issue = request.args.get("issue", "")
    rep = request.args.get("rep", "")
    ad_type = request.args.get("ad_type", "")
    where, params = ["canceled = 0"], []
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if year:
        where.append("issue_date LIKE ?")
        params.append(f"%/{year[2:]} %")
    if issue:
        where.append("issue_date = ?")
        params.append(issue)
    if rep:
        where.append("rep1 = ?")
        params.append(rep)
    if ad_type:
        where.append("type_ad = ?")
        params.append(ad_type)
    likelihood = request.args.get("likelihood", "")
    if likelihood == "confirmed":
        where.append("likelihood = 1")
    elif likelihood == "proposal":
        where.append("likelihood = 10")
    sort = request.args.get("sort", "publication,issue_date,account_name")
    allowed_sorts = {"publication","issue_date","account_name","agency_name","type_ad","ad_size","color","ad_cost","net_cost","rep1"}
    sort_parts = []
    for s in sort.split(","):
        s = s.strip()
        desc = False
        if s.startswith("-"):
            s = s[1:]
            desc = True
        if s in allowed_sorts:
            sort_parts.append(f"{s} {'DESC' if desc else 'ASC'}")
    order_by = ", ".join(sort_parts) if sort_parts else "publication, issue_date, account_name"
    rows = db.execute(f"""SELECT publication, issue_date, account_name, agency_name,
                          type_ad, ad_size, color, ad_cost, net_cost, rep1,
                          contract_num, order_num
                          , likelihood
                          FROM sm_page_history
                          WHERE {' AND '.join(where)}
                          ORDER BY {order_by}""", params).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        row["issue_date_iso"] = _parse_sm_date(row["issue_date"])
        row["booking_status"] = "Confirmed" if row.get("likelihood") == 1 else "Proposal"
        result.append(row)
    db.close()
    return jsonify(result)

@app.route("/api/reports/issue-dates")
def report_issue_dates():
    db = get_db()
    pub = request.args.get("publication", "")
    year = request.args.get("year", "")
    where, params = ["canceled = 0"], []
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if year:
        where.append("issue_date LIKE ?")
        params.append(f"%/{year[2:]} %")
    rows = db.execute(f"""SELECT DISTINCT issue_date FROM sm_page_history
                          WHERE {' AND '.join(where)}
                          ORDER BY issue_date""", params).fetchall()
    result = []
    for r in rows:
        result.append({"raw": r["issue_date"], "iso": _parse_sm_date(r["issue_date"])})
    db.close()
    return jsonify(result)

@app.route("/api/reports/revenue-summary")
def report_revenue_summary():
    db = get_db()
    c = db.cursor()
    year = request.args.get("year", "26")
    if len(year) == 4:
        year = year[2:]
    pub = request.args.get("publication", "")
    likelihood = request.args.get("likelihood", "")
    where = ["canceled = 0", "issue_date LIKE ?"]
    params = [f"%/{year} %"]
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if likelihood == "confirmed":
        where.append("likelihood = 1")
    elif likelihood == "proposal":
        where.append("likelihood = 10")
    rows = c.execute(f"""SELECT publication, issue_date, COUNT(*) as insertion_count,
                        SUM(ad_cost) as total_revenue, SUM(net_cost) as total_net
                        FROM sm_page_history
                        WHERE {' AND '.join(where)}
                        GROUP BY publication, issue_date
                        ORDER BY publication, issue_date""",
                     params).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        row["issue_date_iso"] = _parse_sm_date(row["issue_date"])
        result.append(row)
    db.close()
    return jsonify(result)

@app.route("/api/reports/rep-performance")
def report_rep_performance():
    db = get_db()
    year = request.args.get("year", "26")
    if len(year) == 4:
        year = year[2:]
    pub = request.args.get("publication", "")
    likelihood = request.args.get("likelihood", "")
    where = ["canceled = 0", "issue_date LIKE ?", "rep1 != ''"]
    params = [f"%/{year} %"]
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if likelihood == "confirmed":
        where.append("likelihood = 1")
    elif likelihood == "proposal":
        where.append("likelihood = 10")
    rows = db.execute(f"""SELECT rep1 as rep, publication, COUNT(*) as insertions,
                         SUM(ad_cost) as revenue
                         FROM sm_page_history
                         WHERE {' AND '.join(where)}
                         GROUP BY rep1, publication
                         ORDER BY rep1, publication""",
                      params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

# ── Ad Sales Summary (replaces all-time stats) ──
@app.route("/api/sales/summary")
def sales_summary():
    db = get_db()
    c = db.cursor()
    result = {}
    now = datetime.now()
    yr2 = f"{now.year % 100:02d}"
    result["current_year"] = now.year

    c.execute("SELECT COALESCE(SUM(ad_cost),0) FROM sm_page_history WHERE canceled=0 AND issue_date LIKE ?",
              (f"%/{yr2} %",))
    result["ytd_revenue"] = c.fetchone()[0]
    prev_yr = f"{(now.year - 1) % 100:02d}"
    c.execute("SELECT COALESCE(SUM(ad_cost),0) FROM sm_page_history WHERE canceled=0 AND issue_date LIKE ?",
              (f"%/{prev_yr} %",))
    result["prev_year_revenue"] = c.fetchone()[0]

    c.execute("""SELECT publication, issue_date, COUNT(*) as cnt, SUM(ad_cost) as revenue
                 FROM sm_page_history WHERE canceled=0 AND issue_date LIKE ?
                 GROUP BY publication, issue_date ORDER BY issue_date, publication""",
              (f"%/{yr2} %",))
    upcoming = []
    for r in c.fetchall():
        row = dict(r)
        row["issue_date_iso"] = _parse_sm_date(row["issue_date"])
        upcoming.append(row)
    result["issues_this_year"] = upcoming

    c.execute("""SELECT rep1 as rep, SUM(ad_cost) as revenue, COUNT(*) as insertions
                 FROM sm_page_history WHERE canceled=0 AND issue_date LIKE ? AND rep1 != ''
                 GROUP BY rep1 ORDER BY revenue DESC""",
              (f"%/{yr2} %",))
    result["rep_totals"] = [dict(r) for r in c.fetchall()]

    db.close()
    return jsonify(result)

HIDDEN_CONTACT_FIELDS = {
    "are_you_bringing_a_guest_to_the_wednesday_welcome_reception_",
    "conference_confirmation_id", "n2022_expo_vaccination_consent",
    "n2024_mbr_survey_link", "exhibitor_bootcamp",
    "headshot__high_resolution__300dpi__jpeg_or_png_preferred_",
    "product_image__high_resolution__300dpi__jpeg_or_png_preferred_",
    "questions_and_answers__2100_characters_maximum__",
    "tmpbirthdaycode", "gender_", "country_new_", "message",
    "ip_city", "ip_country", "ip_country_code", "ip_state", "ip_state_code",
}

def _is_hidden_contact_field(f):
    if "spark" in f.lower():
        return True
    if f.lower().startswith("asla"):
        return True
    if f.startswith("hs_v2_") or f == "hs_predictivecontactscore_v2":
        return True
    if f.startswith("hs_email_optout_"):
        return True
    if f.startswith("koalify_"):
        return True
    if f.startswith("zoom_webinar_"):
        return True
    if f in HIDDEN_CONTACT_FIELDS:
        return True
    return False

@app.route("/api/fields/contacts")
def contact_fields():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT all_properties FROM contacts LIMIT 200")
    field_set = set()
    for r in c.fetchall():
        props = json.loads(r["all_properties"] or "{}")
        field_set.update(props.keys())
    col_fields = ["firstname","lastname","email","phone","company","jobtitle",
                   "city","state","country","owner_name","lifecyclestage","hs_lead_status"]
    all_fields = sorted(f for f in (set(col_fields) | field_set) if not _is_hidden_contact_field(f))
    db.close()
    return jsonify(all_fields)

@app.route("/api/fields/companies")
def company_fields():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT all_properties FROM companies LIMIT 200")
    field_set = set()
    for r in c.fetchall():
        props = json.loads(r["all_properties"] or "{}")
        field_set.update(props.keys())
    col_fields = ["name","domain","industry","city","state","country","phone","owner_name"]
    all_fields = sorted(set(col_fields) | field_set)
    db.close()
    return jsonify(all_fields)

@app.route("/api/field-values/<field>")
def field_values(field):
    db = get_db()
    c = db.cursor()
    col_fields = {"firstname","lastname","email","phone","company","jobtitle",
                  "city","state","country","owner_name","lifecyclestage","hs_lead_status"}
    values = set()
    if field in col_fields:
        c.execute(f"SELECT DISTINCT {field} FROM contacts WHERE {field} IS NOT NULL AND {field} != '' ORDER BY {field} LIMIT 500")
        values = {r[0] for r in c.fetchall()}
    else:
        c.execute("SELECT all_properties FROM contacts")
        for r in c.fetchall():
            props = json.loads(r["all_properties"] or "{}")
            v = props.get(field)
            if v and isinstance(v, str) and len(v) < 200:
                values.add(v)
            if len(values) >= 500:
                break
    db.close()
    return jsonify(sorted(values))

@app.route("/api/contacts")
def list_contacts():
    db = get_db()
    c = db.cursor()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "lastname")
    order = request.args.get("order", "asc")
    filters_json = request.args.get("filters", "")

    if order not in ("asc", "desc"):
        order = "asc"
    allowed_sorts = {"firstname","lastname","email","company","owner_name","city","state","createdate","lastmodifieddate"}
    if sort not in allowed_sorts:
        sort = "lastname"

    where = []
    params = []

    fts_match = None
    if search:
        terms = search.replace('"', '').split()
        if len(terms) > 1:
            and_match = " AND ".join(f'"{t}"*' for t in terms)
            cnt = c.execute("SELECT COUNT(*) FROM contacts_fts WHERE contacts_fts MATCH ?", (and_match,)).fetchone()[0]
            if cnt > 0:
                fts_match = and_match
            else:
                fts_match = " OR ".join(f'"{t}"*' for t in terms)
        else:
            fts_match = f'"{terms[0]}"*'
        where.append("id IN (SELECT id FROM contacts_fts WHERE contacts_fts MATCH ?)")
        params.append(fts_match)

    if filters_json:
        where, params = apply_advanced_filters(c, "contacts", filters_json, where, params)

    where_clause = " WHERE " + " AND ".join(where) if where else ""

    c.execute(f"SELECT COUNT(*) FROM contacts{where_clause}", params)
    total = c.fetchone()[0]

    extra_fields_param = request.args.get("extra_fields", "")
    extra_fields = [f.strip() for f in extra_fields_param.split(",") if f.strip()] if extra_fields_param else []
    need_props = bool(extra_fields)

    select_cols = """id, firstname, lastname, email, phone, company, jobtitle,
                         city, state, country, owner_name, lifecyclestage, hs_lead_status,
                         createdate, lastmodifieddate"""
    if need_props:
        select_cols += ", all_properties"

    if fts_match:
        order_clause = f"(SELECT rank FROM contacts_fts WHERE contacts_fts.id = contacts.id AND contacts_fts MATCH ?) ASC"
        order_params = [fts_match]
    else:
        order_clause = f"{sort} {order}"
        order_params = []

    offset = (page - 1) * per_page
    c.execute(f"""SELECT {select_cols}
                  FROM contacts{where_clause}
                  ORDER BY {order_clause}
                  LIMIT ? OFFSET ?""", params + order_params + [per_page, offset])

    contacts = []
    for r in c.fetchall():
        row = dict(r)
        if need_props:
            props = json.loads(row.pop("all_properties", "{}"))
            for ef in extra_fields:
                row[ef] = props.get(ef, "")
        cid = row["id"]
        c2 = db.cursor()
        c2.execute("SELECT COUNT(*) FROM activities WHERE contact_id = ?", (cid,))
        row["activity_count"] = c2.fetchone()[0]
        c2.execute("SELECT MAX(timestamp) FROM activities WHERE contact_id = ?", (cid,))
        row["last_activity"] = c2.fetchone()[0]
        contacts.append(row)

    db.close()
    return jsonify({"contacts": contacts, "total": total, "page": page, "per_page": per_page,
                     "total_pages": (total + per_page - 1) // per_page})

@app.route("/api/contacts/export")
def export_contacts():
    db = get_db()
    c = db.cursor()
    search = request.args.get("search", "").strip()
    filters_json = request.args.get("filters", "")

    where = []
    params = []
    if search:
        terms = search.replace('"', '').split()
        if len(terms) > 1:
            and_match = " AND ".join(f'"{t}"*' for t in terms)
            cnt = c.execute("SELECT COUNT(*) FROM contacts_fts WHERE contacts_fts MATCH ?", (and_match,)).fetchone()[0]
            fts_match = and_match if cnt > 0 else " OR ".join(f'"{t}"*' for t in terms)
        else:
            fts_match = f'"{terms[0]}"*'
        where.append("id IN (SELECT id FROM contacts_fts WHERE contacts_fts MATCH ?)")
        params.append(fts_match)
    if filters_json:
        where, params = apply_advanced_filters(c, "contacts", filters_json, where, params)

    where_clause = " WHERE " + " AND ".join(where) if where else ""
    c.execute(f"SELECT id, all_properties FROM contacts{where_clause} ORDER BY lastname ASC", params)

    rows = []
    all_keys = set()
    for r in c.fetchall():
        props = json.loads(r["all_properties"] or "{}")
        props["hubspot_id"] = r["id"]
        all_keys.update(props.keys())
        rows.append(props)

    priority = ["hubspot_id","firstname","lastname","email","phone","company","jobtitle",
                "city","state","zip","country","owner_name","owner_email"]
    ordered_keys = [k for k in priority if k in all_keys]
    ordered_keys += sorted(all_keys - set(priority))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ordered_keys, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    db.close()
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=devnex_contacts_export.csv"}
    )

@app.route("/api/companies/export")
def export_companies():
    db = get_db()
    c = db.cursor()
    search = request.args.get("search", "").strip()
    filters_json = request.args.get("filters", "")

    where = []
    params = []
    if search:
        where.append("(name LIKE ? OR domain LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_clause = " WHERE " + " AND ".join(where) if where else ""
    c.execute(f"SELECT id, all_properties FROM companies{where_clause} ORDER BY name ASC", params)

    rows = []
    all_keys = set()
    for r in c.fetchall():
        props = json.loads(r["all_properties"] or "{}")
        props["hubspot_id"] = r["id"]
        all_keys.update(props.keys())
        rows.append(props)

    priority = ["hubspot_id","name","domain","industry","phone","city","state","country","owner_name"]
    ordered_keys = [k for k in priority if k in all_keys]
    ordered_keys += sorted(all_keys - set(priority))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ordered_keys, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    db.close()
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=devnex_companies_export.csv"}
    )

@app.route("/api/contacts/<contact_id>")
def get_contact(contact_id):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    row = c.fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Not found"}), 404

    contact = dict(row)
    props = json.loads(contact.pop("all_properties", "{}"))
    contact["properties"] = props

    c.execute("""SELECT co.* FROM companies co
                 JOIN contact_companies cc ON co.id = cc.company_id
                 WHERE cc.contact_id = ?""", (contact_id,))
    companies = []
    for r in c.fetchall():
        comp = dict(r)
        comp["properties"] = json.loads(comp.pop("all_properties", "{}"))
        companies.append(comp)
    contact["companies"] = companies

    db.close()
    return jsonify(contact)

@app.route("/api/contacts/<contact_id>/activities")
def get_activities(contact_id):
    db = get_db()
    c = db.cursor()
    atype = request.args.get("type", "").strip()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 25))

    where = "WHERE contact_id = ?"
    params = [contact_id]
    if atype:
        where += " AND type = ?"
        params.append(atype)

    c.execute(f"SELECT COUNT(*) FROM activities {where}", params)
    total_hs = c.fetchone()[0]

    ca_where = "WHERE contact_id = ?"
    ca_params = [contact_id]
    if atype:
        ca_where += " AND type = ?"
        ca_params.append(atype)
    c.execute(f"SELECT COUNT(*) FROM custom_activities {ca_where}", ca_params)
    total_ca = c.fetchone()[0]
    total = total_hs + total_ca

    offset = (page - 1) * per_page
    c.execute(f"""SELECT id, type, timestamp, subject, body, direction, status,
                         owner_name, from_email, to_email, 'hubspot' as source
                  FROM activities {where}
                  UNION ALL
                  SELECT id, type, created_at as timestamp, subject, body, NULL as direction,
                         NULL as status, created_by as owner_name, NULL as from_email,
                         NULL as to_email, 'custom' as source
                  FROM custom_activities {ca_where}
                  ORDER BY timestamp DESC
                  LIMIT ? OFFSET ?""", params + ca_params + [per_page, offset])

    activities = [dict(r) for r in c.fetchall()]

    c.execute("SELECT type, COUNT(*) as cnt FROM activities WHERE contact_id = ? GROUP BY type", (contact_id,))
    type_counts = {r["type"]: r["cnt"] for r in c.fetchall()}
    c.execute("SELECT type, COUNT(*) as cnt FROM custom_activities WHERE contact_id = ? GROUP BY type", (contact_id,))
    for r in c.fetchall():
        type_counts[r["type"]] = type_counts.get(r["type"], 0) + r["cnt"]

    db.close()
    return jsonify({"activities": activities, "total": total, "page": page,
                     "per_page": per_page, "type_counts": type_counts})


@app.route("/api/activities", methods=["POST"])
def create_activity():
    db = get_db()
    data = request.get_json()
    contact_id = data.get("contact_id")
    company_id = data.get("company_id")
    atype = data.get("type", "notes")
    subject = data.get("subject", "")
    body = data.get("body", "")
    created_by = data.get("created_by", "")

    if not contact_id and not company_id:
        return jsonify({"error": "contact_id or company_id required"}), 400

    if contact_id and not company_id:
        c = db.cursor()
        c.execute("SELECT company_id FROM contact_companies WHERE contact_id = ? LIMIT 1", (contact_id,))
        row = c.fetchone()
        if row:
            company_id = row["company_id"]

    db.execute("""INSERT INTO custom_activities (contact_id, company_id, type, subject, body, created_by)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (contact_id, company_id, atype, subject, body, created_by))
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": aid, "ok": True})


@app.route("/api/companies/<company_id>/activities")
def get_company_activities(company_id):
    db = get_db()
    c = db.cursor()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 25))
    atype = request.args.get("type", "").strip()

    c.execute("SELECT contact_id FROM contact_companies WHERE company_id = ?", (company_id,))
    contact_ids = [r["contact_id"] for r in c.fetchall()]

    if not contact_ids:
        db.close()
        return jsonify({"activities": [], "total": 0, "page": 1, "per_page": per_page, "type_counts": {}})

    placeholders = ",".join(["?"] * len(contact_ids))

    hs_where = f"contact_id IN ({placeholders})"
    hs_params = list(contact_ids)
    type_filter = " AND type = ?" if atype else ""

    c.execute(f"SELECT COUNT(*) FROM activities WHERE {hs_where}{type_filter}",
              hs_params + ([atype] if atype else []))
    total_hs = c.fetchone()[0]

    ca_sql = f"(contact_id IN ({placeholders}) OR company_id = ?)"
    ca_params_base = list(contact_ids) + [company_id]
    c.execute(f"SELECT COUNT(*) FROM custom_activities WHERE {ca_sql}{type_filter}",
              ca_params_base + ([atype] if atype else []))
    total_ca = c.fetchone()[0]
    total = total_hs + total_ca

    offset = (page - 1) * per_page
    sql = f"""
        SELECT id, type, timestamp, subject, body, direction, status,
               owner_name, from_email, to_email, 'hubspot' as source, contact_id
        FROM activities WHERE {hs_where}{type_filter}
        UNION ALL
        SELECT id, type, created_at as timestamp, subject, body, NULL as direction,
               NULL as status, created_by as owner_name, NULL as from_email,
               NULL as to_email, 'custom' as source, contact_id
        FROM custom_activities WHERE {ca_sql}{type_filter}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """
    sql_params = hs_params + ([atype] if atype else []) + ca_params_base + ([atype] if atype else []) + [per_page, offset]
    c.execute(sql, sql_params)
    activities = [dict(r) for r in c.fetchall()]

    c.execute(f"SELECT type, COUNT(*) as cnt FROM activities WHERE contact_id IN ({placeholders}) GROUP BY type", contact_ids)
    type_counts = {r["type"]: r["cnt"] for r in c.fetchall()}
    c.execute(f"SELECT type, COUNT(*) as cnt FROM custom_activities WHERE {ca_sql} GROUP BY type", ca_params_base)
    for r in c.fetchall():
        type_counts[r["type"]] = type_counts.get(r["type"], 0) + r["cnt"]

    db.close()
    return jsonify({"activities": activities, "total": total, "page": page,
                     "per_page": per_page, "type_counts": type_counts})

@app.route("/api/companies")
def list_companies():
    db = get_db()
    c = db.cursor()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    search = request.args.get("search", "").strip()

    where = []
    params = []
    if search:
        where.append("(name LIKE ? OR domain LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_clause = " WHERE " + " AND ".join(where) if where else ""
    c.execute(f"SELECT COUNT(*) FROM companies{where_clause}", params)
    total = c.fetchone()[0]

    offset = (page - 1) * per_page
    c.execute(f"""SELECT id, name, domain, industry, city, state, country, phone, owner_name
                  FROM companies{where_clause}
                  ORDER BY name ASC
                  LIMIT ? OFFSET ?""", params + [per_page, offset])

    companies = []
    for r in c.fetchall():
        row = dict(r)
        c2 = db.cursor()
        c2.execute("SELECT COUNT(*) FROM contact_companies WHERE company_id = ?", (row["id"],))
        row["contact_count"] = c2.fetchone()[0]
        companies.append(row)

    db.close()
    return jsonify({"companies": companies, "total": total, "page": page, "per_page": per_page})

@app.route("/api/companies/<company_id>")
def get_company(company_id):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    row = c.fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Not found"}), 404

    company = dict(row)
    company["properties"] = json.loads(company.pop("all_properties", "{}"))

    c.execute("""SELECT ct.id, ct.firstname, ct.lastname, ct.email, ct.phone,
                        ct.jobtitle, ct.owner_name
                 FROM contacts ct
                 JOIN contact_companies cc ON ct.id = cc.contact_id
                 WHERE cc.company_id = ?""", (company_id,))
    company["contacts"] = [dict(r) for r in c.fetchall()]

    sm_row = c.execute("""
        SELECT cm.sm_company_name, sc.id as sm_account_id
        FROM company_mapping cm
        LEFT JOIN sm_companies sc ON sc.company = cm.sm_company_name
        WHERE cm.hubspot_company_id = ?
        LIMIT 1
    """, (company_id,)).fetchone()
    if sm_row:
        sm_name = sm_row["sm_company_name"]
        company["sm_account_id"] = sm_row["sm_account_id"]
        company["sm_company_name"] = sm_name
        company["sm_revenue"] = c.execute(
            "SELECT COALESCE(SUM(ad_cost), 0) FROM sm_page_history WHERE account_name = ? AND canceled = 0",
            (sm_name,)).fetchone()[0]
        company["sm_insertions"] = c.execute(
            "SELECT COUNT(*) FROM sm_page_history WHERE account_name = ? AND canceled = 0",
            (sm_name,)).fetchone()[0]

    db.close()
    return jsonify(company)

@app.route("/api/owners")
def list_owners():
    db = get_db()
    c = db.cursor()
    c.execute("""SELECT owner_name, COUNT(*) as cnt
                 FROM contacts WHERE owner_name != ''
                 GROUP BY owner_name ORDER BY cnt DESC""")
    owners = [{"name": r["owner_name"], "count": r["cnt"]} for r in c.fetchall()]
    db.close()
    return jsonify(owners)

# Saved filters
@app.route("/api/saved-filters", methods=["GET"])
def list_saved_filters():
    target = request.args.get("target", "contacts")
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM saved_filters WHERE target = ? ORDER BY name ASC", (target,))
    filters = [{"id": r["id"], "name": r["name"], "target": r["target"],
                "filters": json.loads(r["filters"])} for r in c.fetchall()]
    db.close()
    return jsonify(filters)

@app.route("/api/saved-filters", methods=["POST"])
def save_filter():
    data = request.get_json()
    db = get_db()
    db.execute("INSERT INTO saved_filters (name, target, filters) VALUES (?, ?, ?)",
               (data["name"], data.get("target", "contacts"), json.dumps(data["filters"])))
    db.commit()
    fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": fid, "name": data["name"]})

@app.route("/api/saved-filters/<int:filter_id>", methods=["DELETE"])
def delete_filter(filter_id):
    db = get_db()
    db.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

# Marketing Lists
@app.route("/api/lists/preview", methods=["POST"])
def preview_list_count():
    data = request.get_json()
    filters_list = data.get("filters", [])
    db = get_db()
    c = db.cursor()
    where, params = apply_advanced_filters(c, "contacts", filters_list)
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    c.execute(f"SELECT COUNT(*) FROM contacts{where_clause}", params)
    count = c.fetchone()[0]
    db.close()
    return jsonify({"count": count})

@app.route("/api/lists", methods=["GET"])
def list_marketing_lists():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM marketing_lists ORDER BY name ASC")
    lists = []
    for r in c.fetchall():
        item = {"id": r["id"], "name": r["name"], "description": r["description"],
                "target": r["target"], "filters": json.loads(r["filters"]),
                "created_by": r["created_by"] if "created_by" in r.keys() else "admin",
                "created_at": r["created_at"], "updated_at": r["updated_at"]}
        filters_list = item["filters"]
        where, params = apply_advanced_filters(c, "contacts", filters_list)
        where_clause = " WHERE " + " AND ".join(where) if where else ""
        c2 = db.cursor()
        c2.execute(f"SELECT COUNT(*) FROM contacts{where_clause}", params)
        item["count"] = c2.fetchone()[0]
        lists.append(item)
    db.close()
    return jsonify(lists)

@app.route("/api/lists", methods=["POST"])
def create_marketing_list():
    data = request.get_json()
    db = get_db()
    db.execute("INSERT INTO marketing_lists (name, description, target, filters, created_by) VALUES (?, ?, ?, ?, ?)",
               (data["name"], data.get("description", ""), data.get("target", "contacts"),
                json.dumps(data["filters"]), data.get("created_by", "admin")))
    db.commit()
    lid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": lid, "name": data["name"]})

@app.route("/api/lists/<int:list_id>", methods=["GET"])
def get_marketing_list(list_id):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM marketing_lists WHERE id = ?", (list_id,))
    r = c.fetchone()
    if not r:
        db.close()
        return jsonify({"error": "Not found"}), 404
    item = {"id": r["id"], "name": r["name"], "description": r["description"],
            "target": r["target"], "filters": json.loads(r["filters"]),
            "created_at": r["created_at"], "updated_at": r["updated_at"]}
    filters_list = item["filters"]
    where, params = apply_advanced_filters(c, "contacts", filters_list)
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    c.execute(f"SELECT COUNT(*) FROM contacts{where_clause}", params)
    item["count"] = c.fetchone()[0]
    db.close()
    return jsonify(item)

@app.route("/api/lists/<int:list_id>/contacts", methods=["GET"])
def list_contacts_in_list(list_id):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT filters FROM marketing_lists WHERE id = ?", (list_id,))
    r = c.fetchone()
    if not r:
        db.close()
        return jsonify({"error": "Not found"}), 404

    filters_list = json.loads(r["filters"])
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    sort = request.args.get("sort", "lastname")
    order = request.args.get("order", "asc")
    if order not in ("asc", "desc"):
        order = "asc"
    allowed_sorts = {"firstname","lastname","email","company","owner_name","city","state","createdate","lastmodifieddate"}
    if sort not in allowed_sorts:
        sort = "lastname"

    where, params = apply_advanced_filters(c, "contacts", filters_list)
    where_clause = " WHERE " + " AND ".join(where) if where else ""

    c.execute(f"SELECT COUNT(*) FROM contacts{where_clause}", params)
    total = c.fetchone()[0]

    offset = (page - 1) * per_page
    c.execute(f"""SELECT id, firstname, lastname, email, phone, company, jobtitle,
                         city, state, country, owner_name, lifecyclestage, hs_lead_status,
                         createdate, lastmodifieddate
                  FROM contacts{where_clause}
                  ORDER BY {sort} {order}
                  LIMIT ? OFFSET ?""", params + [per_page, offset])

    contacts = []
    for row in c.fetchall():
        d = dict(row)
        cid = d["id"]
        c2 = db.cursor()
        c2.execute("SELECT COUNT(*) FROM activities WHERE contact_id = ?", (cid,))
        d["activity_count"] = c2.fetchone()[0]
        c2.execute("SELECT MAX(timestamp) FROM activities WHERE contact_id = ?", (cid,))
        d["last_activity"] = c2.fetchone()[0]
        contacts.append(d)

    db.close()
    return jsonify({"contacts": contacts, "total": total, "page": page, "per_page": per_page,
                     "total_pages": (total + per_page - 1) // per_page})

@app.route("/api/lists/<int:list_id>/export", methods=["GET"])
def export_marketing_list(list_id):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT name, filters FROM marketing_lists WHERE id = ?", (list_id,))
    r = c.fetchone()
    if not r:
        db.close()
        return jsonify({"error": "Not found"}), 404

    list_name = r["name"]
    filters_list = json.loads(r["filters"])
    where, params = apply_advanced_filters(c, "contacts", filters_list)
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    c.execute(f"SELECT id, all_properties FROM contacts{where_clause} ORDER BY lastname ASC", params)

    rows = []
    all_keys = set()
    for row in c.fetchall():
        props = json.loads(row["all_properties"] or "{}")
        props["hubspot_id"] = row["id"]
        all_keys.update(props.keys())
        rows.append(props)

    priority = ["hubspot_id","firstname","lastname","email","phone","company","jobtitle",
                "city","state","zip","country","owner_name","owner_email"]
    ordered_keys = [k for k in priority if k in all_keys]
    ordered_keys += sorted(all_keys - set(priority))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ordered_keys, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    db.close()
    safe_name = "".join(c if c.isalnum() or c in '-_ ' else '' for c in list_name).strip().replace(' ', '_')
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=devnex_list_{safe_name}.csv"}
    )

@app.route("/api/lists/<int:list_id>", methods=["PUT"])
def update_marketing_list(list_id):
    data = request.get_json()
    db = get_db()
    sets = []
    params = []
    if "name" in data:
        sets.append("name = ?")
        params.append(data["name"])
    if "description" in data:
        sets.append("description = ?")
        params.append(data["description"])
    if "filters" in data:
        sets.append("filters = ?")
        params.append(json.dumps(data["filters"]))
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(list_id)
    db.execute(f"UPDATE marketing_lists SET {', '.join(sets)} WHERE id = ?", params)
    db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/lists/<int:list_id>", methods=["DELETE"])
def delete_marketing_list(list_id):
    db = get_db()
    db.execute("DELETE FROM marketing_lists WHERE id = ?", (list_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════
#  SALES / SPACEMASTER  API
# ═══════════════════════════════════════════════════════════

@app.route("/api/sales/stats")
def sales_stats():
    db = get_db()
    c = db.cursor()
    stats = {}
    stats["total_accounts"] = c.execute("SELECT COUNT(*) FROM sm_companies").fetchone()[0]
    stats["total_contacts"] = c.execute("SELECT COUNT(*) FROM sm_directory").fetchone()[0]
    stats["total_contracts"] = c.execute("SELECT COUNT(*) FROM sm_contracts").fetchone()[0]
    stats["total_insertions"] = c.execute("SELECT COUNT(*) FROM sm_page_history").fetchone()[0]
    stats["active_insertions"] = c.execute(
        "SELECT COUNT(*) FROM sm_page_history WHERE canceled = 0 AND is_open = 1").fetchone()[0]
    stats["total_revenue"] = c.execute(
        "SELECT COALESCE(SUM(ad_cost), 0) FROM sm_page_history WHERE canceled = 0").fetchone()[0]
    stats["publications"] = c.execute("SELECT COUNT(*) FROM sm_publications").fetchone()[0]
    stats["reps"] = [dict(r) for r in c.execute("SELECT * FROM sm_reps ORDER BY rep_id").fetchall()]
    db.close()
    return jsonify(stats)


@app.route("/api/sales/accounts")
def sales_accounts():
    db = get_db()
    c = db.cursor()
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    sort = request.args.get("sort", "company")
    order = request.args.get("order", "asc")
    rep = request.args.get("rep", "")
    pub = request.args.get("pub", "")

    where = []
    params = []
    if q:
        where.append("c.company LIKE ?")
        params.append(f"%{q}%")

    joins = ""
    if rep or pub:
        joins = " LEFT JOIN sm_page_history ph ON ph.account_name = c.company"
        if rep:
            where.append("ph.rep1 = ?")
            params.append(rep)
        if pub:
            where.append("ph.publication = ?")
            params.append(pub)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sort_map = {
        "company": "c.company",
        "insertions": "insertion_count",
        "revenue": "total_revenue",
        "last_issue": "last_issue_date",
    }
    sort_col = sort_map.get(sort, "c.company")
    order_dir = "DESC" if order == "desc" else "ASC"

    count_sql = f"""SELECT COUNT(DISTINCT c.id) FROM sm_companies c {joins} {where_sql}"""
    total = c.execute(count_sql, params).fetchone()[0]

    data_sql = f"""
        SELECT c.company, c.id, c.credit_hold,
            (SELECT COUNT(*) FROM sm_page_history p WHERE p.account_name = c.company AND p.canceled = 0) as insertion_count,
            (SELECT COALESCE(SUM(p.ad_cost), 0) FROM sm_page_history p WHERE p.account_name = c.company AND p.canceled = 0) as total_revenue,
            (SELECT MAX(p.issue_date) FROM sm_page_history p WHERE p.account_name = c.company) as last_issue_date,
            (SELECT COUNT(*) FROM sm_contracts ct WHERE ct.account_name = c.company) as contract_count,
            (SELECT COUNT(*) FROM sm_directory d WHERE d.company = c.company) as contact_count,
            (SELECT cm.hubspot_company_id FROM company_mapping cm WHERE cm.sm_company_name = c.company LIMIT 1) as hubspot_company_id
        FROM sm_companies c {joins}
        {where_sql}
        GROUP BY c.id
        ORDER BY {sort_col} {order_dir}
        LIMIT ? OFFSET ?
    """
    rows = c.execute(data_sql, params + [per_page, (page - 1) * per_page]).fetchall()

    db.close()
    return jsonify({
        "accounts": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


@app.route("/api/sales/accounts/<int:account_id>")
def sales_account_detail(account_id):
    db = get_db()
    c = db.cursor()
    company = c.execute("SELECT * FROM sm_companies WHERE id = ?", (account_id,)).fetchone()
    if not company:
        db.close()
        return jsonify({"error": "not found"}), 404

    name = company["company"]
    contacts = [dict(r) for r in c.execute(
        "SELECT * FROM sm_directory WHERE company = ? ORDER BY default_contact DESC, name", (name,)).fetchall()]

    contracts = [dict(r) for r in c.execute(
        "SELECT * FROM sm_contracts WHERE account_name = ? ORDER BY contract_start DESC", (name,)).fetchall()]

    categories = [r[0] for r in c.execute(
        "SELECT DISTINCT product_category FROM sm_account_categories WHERE account_name = ? ORDER BY product_category", (name,)).fetchall()]

    revenue = c.execute(
        "SELECT COALESCE(SUM(ad_cost), 0) FROM sm_page_history WHERE account_name = ? AND canceled = 0", (name,)).fetchone()[0]
    insertion_count = c.execute(
        "SELECT COUNT(*) FROM sm_page_history WHERE account_name = ? AND canceled = 0", (name,)).fetchone()[0]

    by_pub = [dict(r) for r in c.execute("""
        SELECT publication, COUNT(*) as count, COALESCE(SUM(ad_cost), 0) as revenue
        FROM sm_page_history WHERE account_name = ? AND canceled = 0
        GROUP BY publication ORDER BY revenue DESC
    """, (name,)).fetchall()]

    by_year = [dict(r) for r in c.execute("""
        SELECT substr(issue_date, 7, 2) as year, COUNT(*) as count, COALESCE(SUM(ad_cost), 0) as revenue
        FROM sm_page_history WHERE account_name = ? AND canceled = 0 AND issue_date != ''
        GROUP BY year ORDER BY year DESC LIMIT 10
    """, (name,)).fetchall()]

    hs_data = {}
    hs_contacts = []
    hs_row = c.execute("""
        SELECT cm.hubspot_company_id, co.name, co.domain, co.city, co.state, co.phone, co.owner_name
        FROM company_mapping cm
        JOIN companies co ON co.id = cm.hubspot_company_id
        WHERE cm.sm_company_name = ?
        LIMIT 1
    """, (name,)).fetchone()
    if hs_row:
        hs_data = dict(hs_row)
        hs_id = hs_row["hubspot_company_id"]
        hs_contacts = [dict(r) for r in c.execute("""
            SELECT ct.id, ct.firstname, ct.lastname, ct.email, ct.jobtitle, ct.phone
            FROM contacts ct
            WHERE json_extract(ct.all_properties, '$.associatedcompanyid') = ?
            LIMIT 50
        """, (hs_id,)).fetchall()]

    # Merge: tag contact sources
    for ct in contacts:
        ct["source"] = "spacemaster"
    for ct in hs_contacts:
        ct["source"] = "hubspot"

    db.close()
    result = {
        "company": dict(company),
        "contacts": contacts,
        "hs_contacts": hs_contacts,
        "contracts": contracts,
        "categories": categories,
        "total_revenue": revenue,
        "insertion_count": insertion_count,
        "revenue_by_pub": by_pub,
        "revenue_by_year": by_year,
    }
    result["company"].update({
        "domain": hs_data.get("domain", ""),
        "owner_name": hs_data.get("owner_name", ""),
        "hubspot_company_id": hs_data.get("hubspot_company_id", ""),
        "hs_city": hs_data.get("city", ""),
        "hs_state": hs_data.get("state", ""),
        "hs_phone": hs_data.get("phone", ""),
    })
    return jsonify(result)


@app.route("/api/sales/accounts/<int:account_id>/contracts")
def sales_account_contracts(account_id):
    db = get_db()
    c = db.cursor()
    company = c.execute("SELECT company FROM sm_companies WHERE id = ?", (account_id,)).fetchone()
    if not company:
        db.close()
        return jsonify({"error": "not found"}), 404

    name = company["company"]
    pub = request.args.get("pub", "")
    ad_type = request.args.get("type", "")
    status = request.args.get("status", "")
    rep = request.args.get("rep", "")
    sort = request.args.get("sort", "contract_start")
    order = request.args.get("order", "desc")

    allowed_sorts = {"publication", "type_ad", "rate_card_num", "rate", "contract_start", "contract_end", "status", "rep1"}
    if sort not in allowed_sorts:
        sort = "contract_start"
    if order not in ("asc", "desc"):
        order = "desc"

    where = ["account_name = ?"]
    params = [name]
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if ad_type:
        where.append("type_ad = ?")
        params.append(ad_type)
    if status:
        where.append("status = ?")
        params.append(status)
    if rep:
        where.append("(rep1 = ? OR rep2 = ?)")
        params.extend([rep, rep])

    where_sql = " AND ".join(where)
    rows = c.execute(f"SELECT * FROM sm_contracts WHERE {where_sql} ORDER BY {sort} {order}", params).fetchall()
    db.close()
    return jsonify({"contracts": [dict(r) for r in rows]})


@app.route("/api/sales/accounts/<int:account_id>/insertions")
def sales_account_insertions(account_id):
    db = get_db()
    c = db.cursor()
    company = c.execute("SELECT company FROM sm_companies WHERE id = ?", (account_id,)).fetchone()
    if not company:
        db.close()
        return jsonify({"error": "not found"}), 404

    name = company["company"]
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    pub = request.args.get("pub", "")
    year = request.args.get("year", "")
    ad_type = request.args.get("type", "")
    rep = request.args.get("rep", "")
    status = request.args.get("status", "")
    sort = request.args.get("sort", "issue_date")
    order = request.args.get("order", "desc")

    allowed_sorts = {"issue_date", "publication", "type_ad", "ad_size", "ad_cost", "bill_cost", "rep1", "category", "color"}
    if sort not in allowed_sorts:
        sort = "issue_date"
    if order not in ("asc", "desc"):
        order = "desc"
    # MM/DD/YY format sorts wrong lexicographically; reorder to YY/MM/DD for issue_date
    sort_expr = f"substr(issue_date,7,2)||substr(issue_date,1,2)||substr(issue_date,4,2)" if sort == "issue_date" else sort

    where = ["account_name = ?"]
    params = [name]
    if status == "canceled":
        where.append("canceled = 1")
    elif status == "closed":
        where.append("canceled = 0 AND is_open = 0")
    elif status == "open":
        where.append("canceled = 0 AND is_open = 1")
    else:
        where.append("canceled = 0")
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if year:
        where.append("substr(issue_date, 7, 2) = ?")
        params.append(year)
    if ad_type:
        where.append("type_ad = ?")
        params.append(ad_type)
    if rep:
        where.append("rep1 = ?")
        params.append(rep)
    likelihood = request.args.get("likelihood", "")
    if likelihood == "confirmed":
        where.append("likelihood = 1")
    elif likelihood == "proposal":
        where.append("likelihood = 10")

    where_sql = " AND ".join(where)
    total = c.execute(f"SELECT COUNT(*) FROM sm_page_history WHERE {where_sql}", params).fetchone()[0]

    rows = c.execute(f"""
        SELECT page_history_id, invoice_num, type_ad, issue_date, ad_size, color,
               ad_cost, net_cost, gross_cost, bill_cost, publication, rep1,
               category, headline, prod_status_id, position, placement, materials,
               mat_on_hand, mat_expected, mat_due_date, is_open, is_frozen, canceled,
               likelihood
        FROM sm_page_history
        WHERE {where_sql}
        ORDER BY {sort_expr} {order}
        LIMIT ? OFFSET ?
    """, params + [per_page, (page - 1) * per_page]).fetchall()

    db.close()
    return jsonify({
        "insertions": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


@app.route("/api/sales/contracts/<int:contract_id>")
def sales_contract_detail(contract_id):
    db = get_db()
    c = db.cursor()
    row = c.execute("SELECT * FROM sm_contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "not found"}), 404
    contract = dict(row)
    insertions = [dict(r) for r in c.execute("""
        SELECT page_history_id, issue_date, type_ad, ad_size, color, category,
               ad_cost, net_cost, gross_cost, headline, is_open, canceled, rep1, publication
        FROM sm_page_history
        WHERE contract_num = ? AND account_name = ?
        ORDER BY issue_date DESC
    """, (contract["contract_id"], contract["account_name"])).fetchall()]
    db.close()
    return jsonify({"contract": contract, "insertions": insertions})


@app.route("/api/sales/insertions/<int:insertion_id>")
def sales_insertion_detail(insertion_id):
    db = get_db()
    c = db.cursor()
    row = c.execute("SELECT * FROM sm_page_history WHERE page_history_id = ?", (insertion_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "not found"}), 404
    db.close()
    return jsonify({"insertion": dict(row)})


@app.route("/api/sales/publications")
def sales_publications():
    db = get_db()
    rows = db.execute("SELECT * FROM sm_publications ORDER BY publication").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sales/lookups")
def sales_lookups():
    db = get_db()
    c = db.cursor()
    pubs = [dict(r) for r in c.execute("SELECT * FROM sm_publications ORDER BY publication").fetchall()]
    reps = [dict(r) for r in c.execute("SELECT rep_id, rep, territory_id, territory FROM sm_reps ORDER BY rep_id").fetchall()]
    categories = [r[0] for r in c.execute("SELECT category FROM sm_product_categories ORDER BY category").fetchall()]

    rate_cards = {}
    ad_types_by_pub = {}
    ad_sizes_by_pub = {}
    ad_colors_by_pub = {}
    for r in c.execute("SELECT DISTINCT publication, rate_card_num, type_ad, ad_size, color FROM sm_rate_card ORDER BY publication").fetchall():
        pub = r[0]
        rate_cards.setdefault(pub, set()).add(r[1])
        ad_types_by_pub.setdefault(pub, set()).add(r[2])
        ad_sizes_by_pub.setdefault(pub, set()).add(r[3])
        ad_colors_by_pub.setdefault(pub, set()).add(r[4])
    rate_cards = {k: sorted(v) for k, v in rate_cards.items()}
    ad_types_by_pub = {k: sorted(v) for k, v in ad_types_by_pub.items()}
    ad_sizes_by_pub = {k: sorted(v) for k, v in ad_sizes_by_pub.items()}
    ad_colors_by_pub = {k: sorted(v) for k, v in ad_colors_by_pub.items()}

    issue_dates = {}
    for r in c.execute("SELECT cover_date, publication FROM sm_issue_dates ORDER BY id DESC").fetchall():
        issue_dates.setdefault(r[1], []).append(r[0])
    db.close()
    return jsonify({
        "publications": pubs,
        "reps": reps,
        "categories": categories,
        "rate_cards": rate_cards,
        "ad_types_by_pub": ad_types_by_pub,
        "ad_sizes_by_pub": ad_sizes_by_pub,
        "ad_colors_by_pub": ad_colors_by_pub,
        "issue_dates": issue_dates,
    })


@app.route("/api/sales/rate-card-pricing")
def rate_card_pricing():
    db = get_db()
    pub = request.args.get("publication", "")
    rc = request.args.get("rate_card", "")
    rows = db.execute(
        "SELECT ad_size, type_ad, color, ad_cost, rate FROM sm_rate_card WHERE publication = ? AND rate_card_num = ? ORDER BY ad_size",
        (pub, rc)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sales/production")
def sales_production():
    db = get_db()
    c = db.cursor()
    pub = request.args.get("pub", "")
    issue = request.args.get("issue", "")
    status = request.args.get("status", "")
    rep = request.args.get("rep", "")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    where = ["ph.canceled = 0"]
    params = []
    if pub:
        where.append("ph.publication = ?")
        params.append(pub)
    if issue:
        where.append("ph.issue_date = ?")
        params.append(issue)
    if status:
        where.append("ph.prod_status_id = ?")
        params.append(int(status))
    if rep:
        where.append("ph.rep1 = ?")
        params.append(rep)

    where_sql = " AND ".join(where)
    total = c.execute(f"SELECT COUNT(*) FROM sm_page_history ph WHERE {where_sql}", params).fetchone()[0]

    rows = c.execute(f"""
        SELECT ph.page_history_id, ph.account_name, ph.publication, ph.issue_date,
               ph.ad_size, ph.color, ph.type_ad, ph.ad_cost, ph.rep1,
               ph.materials, ph.mat_on_hand, ph.mat_expected, ph.mat_due_date,
               ph.position, ph.placement, ph.headline, ph.category,
               ph.is_open, ph.is_frozen, ph.prod_status_id,
               COALESCE(ps.status, '') as prod_status
        FROM sm_page_history ph
        LEFT JOIN sm_prod_statuses ps ON ps.id = ph.prod_status_id
        WHERE {where_sql}
        ORDER BY substr(ph.issue_date,7,2)||substr(ph.issue_date,1,2)||substr(ph.issue_date,4,2) DESC, ph.account_name
        LIMIT ? OFFSET ?
    """, params + [per_page, (page - 1) * per_page]).fetchall()

    db.close()
    return jsonify({
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


@app.route("/api/sales/issues")
def sales_issues():
    db = get_db()
    pub = request.args.get("pub", "")
    where = ""
    params = []
    if pub:
        where = "WHERE publication = ?"
        params = [pub]
    rows = db.execute(f"""
        SELECT cover_date, publication, closing_date
        FROM sm_issue_dates {where}
        ORDER BY cover_date DESC
    """, params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sales/prod-statuses")
def sales_prod_statuses():
    db = get_db()
    rows = db.execute("SELECT * FROM sm_prod_statuses ORDER BY id").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sales/contracts", methods=["POST"])
def create_contract():
    db = get_db()
    data = request.get_json()
    fields = ["contract_id", "account_name", "agency_name", "bill_name", "publication",
              "rate_card_num", "rate", "contract_start", "contract_end", "status",
              "terms", "agency_discount", "credit_hold", "type_ad",
              "rep1", "territory1", "rep2", "territory2", "notes"]
    vals = {f: data.get(f, "") for f in fields}
    cols = ", ".join(vals.keys())
    placeholders = ", ".join(["?"] * len(vals))
    db.execute(f"INSERT INTO sm_contracts ({cols}) VALUES ({placeholders})", list(vals.values()))
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": aid, "ok": True})


@app.route("/api/sales/contracts/<int:contract_id>", methods=["PUT"])
def update_contract(contract_id):
    db = get_db()
    data = request.get_json()
    fields = ["account_name", "agency_name", "bill_name", "publication",
              "rate_card_num", "rate", "contract_start", "contract_end", "status",
              "terms", "agency_discount", "credit_hold", "type_ad",
              "rep1", "territory1", "rep2", "territory2", "notes"]
    sets = []
    params = []
    for f in fields:
        if f in data:
            sets.append(f"{f} = ?")
            params.append(data[f])
    if not sets:
        db.close()
        return jsonify({"error": "no fields"}), 400
    params.append(contract_id)
    db.execute(f"UPDATE sm_contracts SET {', '.join(sets)} WHERE id = ?", params)
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/sales/contracts/<int:contract_id>", methods=["DELETE"])
def delete_contract(contract_id):
    db = get_db()
    db.execute("DELETE FROM sm_contracts WHERE id = ?", (contract_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/sales/insertions", methods=["POST"])
def create_insertion():
    db = get_db()
    data = request.get_json()
    fields = ["contract_num", "account_name", "agency_name", "bill_name", "publication",
              "type_ad", "issue_date", "ad_size", "color", "category", "headline",
              "ad_cost", "net_cost", "gross_cost", "bill_cost",
              "rep1", "territory1", "rep2", "territory2",
              "position_request", "placement", "comments", "url_address",
              "is_open", "canceled", "rate_card_num", "rate"]
    vals = {f: data.get(f, "") for f in fields}
    if "is_open" not in data:
        vals["is_open"] = 1
    if "canceled" not in data:
        vals["canceled"] = 0
    cols = ", ".join(vals.keys())
    placeholders = ", ".join(["?"] * len(vals))
    db.execute(f"INSERT INTO sm_page_history ({cols}) VALUES ({placeholders})", list(vals.values()))
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": aid, "ok": True})


@app.route("/api/sales/insertions/<int:insertion_id>", methods=["PUT"])
def update_insertion(insertion_id):
    db = get_db()
    data = request.get_json()
    fields = ["contract_num", "account_name", "agency_name", "bill_name", "publication",
              "type_ad", "issue_date", "ad_size", "color", "category", "headline",
              "ad_cost", "net_cost", "gross_cost", "bill_cost",
              "rep1", "territory1", "rep2", "territory2",
              "position_request", "placement", "comments", "url_address",
              "is_open", "canceled", "rate_card_num", "rate"]
    sets = []
    params = []
    for f in fields:
        if f in data:
            sets.append(f"{f} = ?")
            params.append(data[f])
    if not sets:
        db.close()
        return jsonify({"error": "no fields"}), 400
    params.append(insertion_id)
    db.execute(f"UPDATE sm_page_history SET {', '.join(sets)} WHERE page_history_id = ?", params)
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/sales/insertions/<int:insertion_id>", methods=["DELETE"])
def delete_insertion(insertion_id):
    db = get_db()
    db.execute("DELETE FROM sm_page_history WHERE page_history_id = ?", (insertion_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════
#  ADMIN API
# ═══════════════════════════════════════════════════════════

def log_audit(db, action, entity_type=None, entity_id=None, details=None, user="admin"):
    db.execute("INSERT INTO audit_log (user, action, entity_type, entity_id, details) VALUES (?, ?, ?, ?, ?)",
               (user, action, entity_type, str(entity_id) if entity_id else None,
                json.dumps(details) if isinstance(details, dict) else details))


# ── Lookup Tables (generic CRUD) ──

LOOKUP_TABLES = {
    "publications": {
        "table": "sm_publications", "pk": "publication",
        "cols": ["publication", "pub_num", "gl_code"],
        "label": "Publication",
    },
    "ad_types": {
        "table": "sm_ad_types", "pk": "ad_type",
        "cols": ["ad_type"],
        "label": "Ad Type",
    },
    "ad_sizes": {
        "table": "sm_ad_sizes", "pk": "ad_size",
        "cols": ["ad_size"],
        "label": "Ad Size",
    },
    "ad_colors": {
        "table": "sm_ad_colors", "pk": "color",
        "cols": ["color"],
        "label": "Ad Color",
    },
    "reps": {
        "table": "sm_reps", "pk": "rep_id",
        "cols": ["rep_id", "rep", "territory_id", "territory", "commission"],
        "label": "Rep",
    },
    "categories": {
        "table": "sm_product_categories", "pk": "category",
        "cols": ["category"],
        "label": "Product Category",
    },
    "prod_statuses": {
        "table": "sm_prod_statuses", "pk": "id",
        "cols": ["id", "status"],
        "label": "Production Status",
    },
}


@app.route("/api/admin/lookups/<table_key>")
def admin_lookup_list(table_key):
    if table_key not in LOOKUP_TABLES:
        return jsonify({"error": "unknown table"}), 404
    cfg = LOOKUP_TABLES[table_key]
    db = get_db()
    rows = db.execute(f"SELECT * FROM {cfg['table']} ORDER BY {cfg['pk']}").fetchall()
    db.close()
    return jsonify({"rows": [dict(r) for r in rows], "config": cfg})


@app.route("/api/admin/lookups/<table_key>", methods=["POST"])
def admin_lookup_create(table_key):
    if table_key not in LOOKUP_TABLES:
        return jsonify({"error": "unknown table"}), 404
    cfg = LOOKUP_TABLES[table_key]
    data = request.get_json()
    db = get_db()
    cols = [c for c in cfg["cols"] if c in data]
    vals = [data[c] for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    db.execute(f"INSERT INTO {cfg['table']} ({', '.join(cols)}) VALUES ({placeholders})", vals)
    log_audit(db, "create", cfg["label"], data.get(cfg["pk"]), data)
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


@app.route("/api/admin/lookups/<table_key>/<path:pk_val>", methods=["PUT"])
def admin_lookup_update(table_key, pk_val):
    if table_key not in LOOKUP_TABLES:
        return jsonify({"error": "unknown table"}), 404
    cfg = LOOKUP_TABLES[table_key]
    data = request.get_json()
    db = get_db()
    sets = []
    params = []
    for c in cfg["cols"]:
        if c in data and c != cfg["pk"]:
            sets.append(f"{c} = ?")
            params.append(data[c])
    if not sets:
        db.close()
        return jsonify({"error": "no fields"}), 400
    params.append(pk_val)
    db.execute(f"UPDATE {cfg['table']} SET {', '.join(sets)} WHERE {cfg['pk']} = ?", params)
    log_audit(db, "update", cfg["label"], pk_val, data)
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


@app.route("/api/admin/lookups/<table_key>/<path:pk_val>", methods=["DELETE"])
def admin_lookup_delete(table_key, pk_val):
    if table_key not in LOOKUP_TABLES:
        return jsonify({"error": "unknown table"}), 404
    cfg = LOOKUP_TABLES[table_key]
    db = get_db()
    db.execute(f"DELETE FROM {cfg['table']} WHERE {cfg['pk']} = ?", (pk_val,))
    log_audit(db, "delete", cfg["label"], pk_val)
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


def _lookups_cache_clear():
    global _lookups
    _lookups = None


# ── Issue Dates ──

@app.route("/api/admin/issue-dates")
def admin_issue_dates():
    db = get_db()
    pub = request.args.get("pub", "")
    where = "WHERE publication = ?" if pub else ""
    params = [pub] if pub else []
    rows = db.execute(f"SELECT id, cover_date, publication, closing_date FROM sm_issue_dates {where} ORDER BY publication, id DESC", params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/issue-dates", methods=["POST"])
def admin_issue_date_create():
    data = request.get_json()
    db = get_db()
    db.execute("INSERT INTO sm_issue_dates (cover_date, publication, closing_date) VALUES (?, ?, ?)",
               (data.get("cover_date", ""), data.get("publication", ""), data.get("closing_date", "")))
    log_audit(db, "create", "Issue Date", None, data)
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    _lookups_cache_clear()
    return jsonify({"id": aid, "ok": True})


@app.route("/api/admin/issue-dates/bulk", methods=["POST"])
def admin_issue_dates_bulk():
    data = request.get_json()
    pub = data.get("publication", "")
    dates = data.get("dates", [])
    if not pub or not dates:
        return jsonify({"error": "publication and dates required"}), 400
    db = get_db()
    created = 0
    for d in dates:
        cover = d.get("cover_date", "")
        closing = d.get("closing_date", "")
        existing = db.execute("SELECT id FROM sm_issue_dates WHERE publication = ? AND cover_date = ?", (pub, cover)).fetchone()
        if not existing:
            db.execute("INSERT INTO sm_issue_dates (cover_date, publication, closing_date) VALUES (?, ?, ?)",
                       (cover, pub, closing))
            created += 1
    log_audit(db, "bulk_create", "Issue Date", pub, {"count": created})
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True, "created": created})


@app.route("/api/admin/issue-dates/<int:date_id>", methods=["PUT"])
def admin_issue_date_update(date_id):
    data = request.get_json()
    db = get_db()
    sets, params = [], []
    for f in ["cover_date", "publication", "closing_date"]:
        if f in data:
            sets.append(f"{f} = ?")
            params.append(data[f])
    if sets:
        params.append(date_id)
        db.execute(f"UPDATE sm_issue_dates SET {', '.join(sets)} WHERE id = ?", params)
        log_audit(db, "update", "Issue Date", date_id, data)
        db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


@app.route("/api/admin/issue-dates/<int:date_id>", methods=["DELETE"])
def admin_issue_date_delete(date_id):
    db = get_db()
    db.execute("DELETE FROM sm_issue_dates WHERE id = ?", (date_id,))
    log_audit(db, "delete", "Issue Date", date_id)
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


# ── Rate Cards ──

@app.route("/api/admin/rate-cards")
def admin_rate_cards():
    db = get_db()
    pub = request.args.get("pub", "")
    rc = request.args.get("rc", "")
    where, params = [], []
    if pub:
        where.append("publication = ?")
        params.append(pub)
    if rc:
        where.append("rate_card_num = ?")
        params.append(rc)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(f"SELECT * FROM sm_rate_card {where_sql} ORDER BY publication, rate_card_num, ad_size", params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/rate-cards", methods=["POST"])
def admin_rate_card_create():
    data = request.get_json()
    db = get_db()
    fields = ["publication", "rate_card_num", "ad_size", "rate", "type_ad", "color", "ad_cost"]
    vals = {f: data.get(f, "") for f in fields}
    cols = ", ".join(vals.keys())
    placeholders = ", ".join(["?"] * len(vals))
    db.execute(f"INSERT INTO sm_rate_card ({cols}) VALUES ({placeholders})", list(vals.values()))
    log_audit(db, "create", "Rate Card", None, data)
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    _lookups_cache_clear()
    return jsonify({"id": aid, "ok": True})


@app.route("/api/admin/rate-cards/<int:rc_id>", methods=["PUT"])
def admin_rate_card_update(rc_id):
    data = request.get_json()
    db = get_db()
    fields = ["publication", "rate_card_num", "ad_size", "rate", "type_ad", "color", "ad_cost"]
    sets, params = [], []
    for f in fields:
        if f in data:
            sets.append(f"{f} = ?")
            params.append(data[f])
    if sets:
        params.append(rc_id)
        db.execute(f"UPDATE sm_rate_card SET {', '.join(sets)} WHERE id = ?", params)
        log_audit(db, "update", "Rate Card", rc_id, data)
        db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


@app.route("/api/admin/rate-cards/<int:rc_id>", methods=["DELETE"])
def admin_rate_card_delete(rc_id):
    db = get_db()
    db.execute("DELETE FROM sm_rate_card WHERE id = ?", (rc_id,))
    log_audit(db, "delete", "Rate Card", rc_id)
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True})


@app.route("/api/admin/rate-cards/clone", methods=["POST"])
def admin_rate_card_clone():
    data = request.get_json()
    src_pub = data.get("source_publication", "")
    src_rc = data.get("source_rate_card", "")
    new_rc = data.get("new_rate_card", "")
    pct = float(data.get("price_adjustment_pct", 0))
    if not src_pub or not src_rc or not new_rc:
        return jsonify({"error": "source_publication, source_rate_card, new_rate_card required"}), 400
    db = get_db()
    rows = db.execute("SELECT * FROM sm_rate_card WHERE publication = ? AND rate_card_num = ?",
                      (src_pub, src_rc)).fetchall()
    if not rows:
        db.close()
        return jsonify({"error": "source rate card not found"}), 404
    created = 0
    for r in rows:
        new_cost = round(r["ad_cost"] * (1 + pct / 100), 2) if r["ad_cost"] else 0
        db.execute("INSERT INTO sm_rate_card (publication, rate_card_num, ad_size, rate, type_ad, color, ad_cost) VALUES (?,?,?,?,?,?,?)",
                   (src_pub, new_rc, r["ad_size"], r["rate"], r["type_ad"], r["color"], new_cost))
        created += 1
    log_audit(db, "clone", "Rate Card", new_rc, {"source": src_rc, "pub": src_pub, "pct": pct, "count": created})
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True, "created": created})


# ── User Management ──

@app.route("/api/admin/users")
def admin_users():
    db = get_db()
    rows = db.execute("SELECT id, username, display_name, email, role, active, created_at, last_login FROM admin_users ORDER BY username").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/users", methods=["POST"])
def admin_user_create():
    data = request.get_json()
    username = data.get("username", "").strip()
    display_name = data.get("display_name", "").strip()
    email = data.get("email", "").strip()
    role = data.get("role", "viewer")
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    if role not in ("admin", "sales_rep", "viewer"):
        return jsonify({"error": "invalid role"}), 400
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    db = get_db()
    try:
        db.execute("INSERT INTO admin_users (username, display_name, email, role, password_hash) VALUES (?, ?, ?, ?, ?)",
                   (username, display_name or username, email, role, pw_hash))
        log_audit(db, "create", "User", username, {"role": role})
        db.commit()
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        db.close()
        return jsonify({"error": "username already exists"}), 409
    db.close()
    return jsonify({"id": uid, "ok": True})


@app.route("/api/admin/users/<int:user_id>", methods=["PUT"])
def admin_user_update(user_id):
    data = request.get_json()
    db = get_db()
    sets, params = [], []
    for f in ["display_name", "email", "role", "active"]:
        if f in data:
            if f == "role" and data[f] not in ("admin", "sales_rep", "viewer"):
                db.close()
                return jsonify({"error": "invalid role"}), 400
            sets.append(f"{f} = ?")
            params.append(data[f])
    if "password" in data and data["password"]:
        sets.append("password_hash = ?")
        params.append(hashlib.sha256(data["password"].encode()).hexdigest())
    if sets:
        params.append(user_id)
        db.execute(f"UPDATE admin_users SET {', '.join(sets)} WHERE id = ?", params)
        log_audit(db, "update", "User", user_id, {k: v for k, v in data.items() if k != "password"})
        db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def admin_user_delete(user_id):
    db = get_db()
    row = db.execute("SELECT username FROM admin_users WHERE id = ?", (user_id,)).fetchone()
    if row and row["username"] == "admin":
        db.close()
        return jsonify({"error": "cannot delete the default admin user"}), 400
    db.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))
    log_audit(db, "delete", "User", user_id)
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Audit Log ──

@app.route("/api/admin/audit-log")
def admin_audit_log():
    db = get_db()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    entity_type = request.args.get("type", "")
    action = request.args.get("action", "")
    where, params = [], []
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    if action:
        where.append("action = ?")
        params.append(action)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM audit_log {where_sql}", params).fetchone()[0]
    rows = db.execute(f"SELECT * FROM audit_log {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                      params + [per_page, (page - 1) * per_page]).fetchall()
    db.close()
    return jsonify({
        "entries": [dict(r) for r in rows],
        "total": total, "page": page, "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


# ── System Settings ──

@app.route("/api/admin/settings")
def admin_settings_get():
    db = get_db()
    rows = db.execute("SELECT key, value FROM system_settings ORDER BY key").fetchall()
    db.close()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/admin/settings", methods=["PUT"])
def admin_settings_update():
    data = request.get_json()
    db = get_db()
    for key, value in data.items():
        db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, str(value)))
    log_audit(db, "update", "Settings", None, data)
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Sales Targets ──

@app.route("/api/admin/targets")
def admin_targets():
    db = get_db()
    year = request.args.get("year", "")
    where, params = [], []
    if year:
        where.append("year = ?")
        params.append(year)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(f"SELECT * FROM sales_targets {where_sql} ORDER BY year DESC, rep_id, publication", params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/targets", methods=["POST"])
def admin_target_create():
    data = request.get_json()
    db = get_db()
    db.execute("INSERT INTO sales_targets (year, rep_id, publication, target_amount) VALUES (?, ?, ?, ?)",
               (data.get("year", ""), data.get("rep_id", ""), data.get("publication", ""),
                float(data.get("target_amount", 0))))
    log_audit(db, "create", "Sales Target", None, data)
    db.commit()
    tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": tid, "ok": True})


@app.route("/api/admin/targets/<int:target_id>", methods=["PUT"])
def admin_target_update(target_id):
    data = request.get_json()
    db = get_db()
    sets, params = [], []
    for f in ["year", "rep_id", "publication", "target_amount"]:
        if f in data:
            sets.append(f"{f} = ?")
            params.append(data[f])
    if sets:
        params.append(target_id)
        db.execute(f"UPDATE sales_targets SET {', '.join(sets)} WHERE id = ?", params)
        log_audit(db, "update", "Sales Target", target_id, data)
        db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/admin/targets/<int:target_id>", methods=["DELETE"])
def admin_target_delete(target_id):
    db = get_db()
    db.execute("DELETE FROM sales_targets WHERE id = ?", (target_id,))
    log_audit(db, "delete", "Sales Target", target_id)
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Database Backup & Stats ──

@app.route("/api/admin/db-stats")
def admin_db_stats():
    db = get_db()
    stats = {}
    tables = ["contacts", "companies", "activities", "sm_companies", "sm_contracts",
              "sm_page_history", "sm_directory", "sm_rate_card", "sm_issue_dates",
              "sm_publications", "sm_reps", "sm_product_categories", "admin_users", "audit_log"]
    for t in tables:
        try:
            stats[t] = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            stats[t] = 0
    stats["db_size_mb"] = round(os.path.getsize(DB_PATH) / 1048576, 1)
    db.close()
    return jsonify(stats)


@app.route("/api/admin/backup")
def admin_backup():
    backup_name = f"deviq_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = os.path.join(os.path.dirname(DB_PATH), backup_name)
    shutil.copy2(DB_PATH, backup_path)
    log_audit(get_db(), "backup", "Database", None, {"file": backup_name})
    get_db().commit()
    return send_file(backup_path, as_attachment=True, download_name=backup_name)


# ── Import / Export ──

EXPORT_TABLES = {
    "contacts": {"table": "contacts", "exclude": ["all_properties"]},
    "companies": {"table": "companies", "exclude": ["all_properties"]},
    "sales_accounts": {"table": "sm_companies"},
    "sales_contracts": {"table": "sm_contracts"},
    "sales_insertions": {"table": "sm_page_history"},
    "sales_contacts": {"table": "sm_directory"},
    "rate_cards": {"table": "sm_rate_card"},
    "issue_dates": {"table": "sm_issue_dates"},
    "reps": {"table": "sm_reps"},
    "publications": {"table": "sm_publications"},
    "categories": {"table": "sm_product_categories"},
}


@app.route("/api/admin/export/<table_key>")
def admin_export_table(table_key):
    if table_key not in EXPORT_TABLES:
        return jsonify({"error": "unknown table"}), 404
    cfg = EXPORT_TABLES[table_key]
    db = get_db()
    rows = db.execute(f"SELECT * FROM {cfg['table']}").fetchall()
    if not rows:
        db.close()
        return Response("", mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=deviq_{table_key}.csv"})
    exclude = set(cfg.get("exclude", []))
    keys = [k for k in rows[0].keys() if k not in exclude]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        row_dict = {k: r[k] for k in keys}
        writer.writerow(row_dict)
    db.close()
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=deviq_{table_key}.csv"})


@app.route("/api/admin/import/<table_key>", methods=["POST"])
def admin_import_table(table_key):
    if table_key not in EXPORT_TABLES:
        return jsonify({"error": "unknown table"}), 404
    cfg = EXPORT_TABLES[table_key]
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    f = request.files["file"]
    content = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    db = get_db()
    c = db.cursor()
    c.execute(f"PRAGMA table_info({cfg['table']})")
    valid_cols = {row[1] for row in c.fetchall()}
    imported = 0
    errors = 0
    for row in reader:
        cols = [k for k in row.keys() if k in valid_cols]
        if not cols:
            continue
        vals = [row[k] for k in cols]
        placeholders = ", ".join(["?"] * len(cols))
        try:
            db.execute(f"INSERT INTO {cfg['table']} ({', '.join(cols)}) VALUES ({placeholders})", vals)
            imported += 1
        except Exception:
            errors += 1
    log_audit(db, "import", table_key, None, {"imported": imported, "errors": errors})
    db.commit()
    db.close()
    _lookups_cache_clear()
    return jsonify({"ok": True, "imported": imported, "errors": errors})


# ── Data Hygiene (Duplicate Detection) ──

@app.route("/api/admin/duplicates")
def admin_duplicates():
    entity = request.args.get("entity", "accounts")
    db = get_db()
    results = []
    if entity == "accounts":
        rows = db.execute("""
            SELECT company, COUNT(*) as cnt FROM sm_companies
            GROUP BY LOWER(TRIM(company)) HAVING cnt > 1
            ORDER BY cnt DESC LIMIT 100
        """).fetchall()
        for r in rows:
            matches = db.execute("SELECT id, company, credit_hold FROM sm_companies WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))",
                                 (r["company"],)).fetchall()
            results.append({"name": r["company"], "count": r["cnt"],
                           "records": [dict(m) for m in matches]})
    elif entity == "contacts":
        rows = db.execute("""
            SELECT LOWER(email) as em, COUNT(*) as cnt FROM contacts
            WHERE email IS NOT NULL AND email != ''
            GROUP BY LOWER(email) HAVING cnt > 1
            ORDER BY cnt DESC LIMIT 100
        """).fetchall()
        for r in rows:
            matches = db.execute("SELECT id, firstname, lastname, email, company FROM contacts WHERE LOWER(email) = ?",
                                 (r["em"],)).fetchall()
            results.append({"name": r["em"], "count": r["cnt"],
                           "records": [dict(m) for m in matches]})
    elif entity == "sm_contacts":
        rows = db.execute("""
            SELECT LOWER(email) as em, COUNT(*) as cnt FROM sm_directory
            WHERE email IS NOT NULL AND email != ''
            GROUP BY LOWER(email) HAVING cnt > 1
            ORDER BY cnt DESC LIMIT 100
        """).fetchall()
        for r in rows:
            matches = db.execute("SELECT id, name, email, company FROM sm_directory WHERE LOWER(email) = ?",
                                 (r["em"],)).fetchall()
            results.append({"name": r["em"], "count": r["cnt"],
                           "records": [dict(m) for m in matches]})
    db.close()
    return jsonify({"entity": entity, "duplicates": results})


# ── Global Search ──
@app.route("/api/search")
def global_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"contacts": [], "companies": [], "accounts": []})
    db = get_db()
    c = db.cursor()
    like = f"%{q}%"

    contacts = c.execute("""SELECT id, firstname, lastname, email, company, jobtitle, phone
        FROM contacts
        WHERE firstname LIKE ? OR lastname LIKE ? OR email LIKE ? OR company LIKE ?
        ORDER BY lastname, firstname LIMIT 15""",
        (like, like, like, like)).fetchall()

    companies = c.execute("""SELECT id, name, domain, industry, city, state
        FROM companies
        WHERE name LIKE ? OR domain LIKE ?
        ORDER BY name LIMIT 15""",
        (like, like)).fetchall()

    accounts = c.execute("""SELECT id, company
        FROM sm_companies
        WHERE company LIKE ?
        ORDER BY company LIMIT 15""",
        (like,)).fetchall()

    db.close()
    return jsonify({
        "contacts": [dict(r) for r in contacts],
        "companies": [dict(r) for r in companies],
        "accounts": [dict(r) for r in accounts],
    })

# ── Pipeline / Renewals ──
@app.route("/api/pipeline")
def pipeline():
    db = get_db()
    c = db.cursor()
    now = datetime.now()
    yr2 = f"{now.year % 100:02d}"
    result = {}

    proposals = c.execute("""
        SELECT account_name, publication, issue_date, type_ad, ad_size, ad_cost, rep1,
               sc.id as account_id
        FROM sm_page_history ph
        LEFT JOIN sm_companies sc ON ph.account_name = sc.company
        WHERE ph.canceled = 0 AND ph.likelihood = 10 AND ph.issue_date LIKE ?
        ORDER BY substr(ph.issue_date,7,2)||substr(ph.issue_date,1,2)||substr(ph.issue_date,4,2) ASC
    """, (f"%/{yr2} %",)).fetchall()
    result["proposals"] = []
    for r in proposals:
        row = dict(r)
        row["issue_date_iso"] = _parse_sm_date(row["issue_date"])
        result["proposals"].append(row)

    c.execute("""SELECT COUNT(*) as cnt, SUM(ad_cost) as revenue
        FROM sm_page_history WHERE canceled=0 AND likelihood=10 AND issue_date LIKE ?""",
        (f"%/{yr2} %",))
    ps = c.fetchone()
    result["proposal_stats"] = {"count": ps["cnt"] or 0, "revenue": ps["revenue"] or 0}

    c.execute("""SELECT COUNT(*) as cnt, SUM(ad_cost) as revenue
        FROM sm_page_history WHERE canceled=0 AND likelihood=1 AND issue_date LIKE ?""",
        (f"%/{yr2} %",))
    cs = c.fetchone()
    result["confirmed_stats"] = {"count": cs["cnt"] or 0, "revenue": cs["revenue"] or 0}

    c.execute("""SELECT rep1 as rep, COUNT(*) as proposals, SUM(ad_cost) as value
        FROM sm_page_history WHERE canceled=0 AND likelihood=10 AND issue_date LIKE ? AND rep1 != ''
        GROUP BY rep1 ORDER BY value DESC""",
        (f"%/{yr2} %",))
    result["proposals_by_rep"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT account_name, COUNT(*) as insertions, SUM(ad_cost) as revenue
        FROM sm_page_history
        WHERE canceled=0 AND likelihood=1 AND issue_date LIKE ?
        GROUP BY account_name
        HAVING account_name NOT IN (
            SELECT DISTINCT account_name FROM sm_page_history
            WHERE canceled=0 AND issue_date LIKE ? AND likelihood IN (1, 10)
        )
        ORDER BY revenue DESC LIMIT 25""",
        (f"%/{int(yr2)-1:02d} %", f"%/{yr2} %"))
    result["lapsed_accounts"] = [dict(r) for r in c.fetchall()]

    upcoming_issues = c.execute("""
        SELECT publication, issue_date,
               SUM(CASE WHEN likelihood=1 THEN 1 ELSE 0 END) as confirmed,
               SUM(CASE WHEN likelihood=10 THEN 1 ELSE 0 END) as proposals,
               SUM(CASE WHEN likelihood=1 THEN ad_cost ELSE 0 END) as confirmed_rev,
               SUM(CASE WHEN likelihood=10 THEN ad_cost ELSE 0 END) as proposal_rev
        FROM sm_page_history
        WHERE canceled=0 AND issue_date LIKE ?
        GROUP BY publication, issue_date
        ORDER BY substr(issue_date,7,2)||substr(issue_date,1,2)||substr(issue_date,4,2) ASC
    """, (f"%/{yr2} %",)).fetchall()
    result["issue_pipeline"] = []
    for r in upcoming_issues:
        row = dict(r)
        row["issue_date_iso"] = _parse_sm_date(row["issue_date"])
        result["issue_pipeline"].append(row)

    db.close()
    return jsonify(result)

# ── Email Ingest (BCC-to-CRM) ──
@app.route("/api/email-log", methods=["POST"])
def email_log():
    db = get_db()
    data = request.get_json()
    from_addr = data.get("from", "")
    to_addr = data.get("to", "")
    subject = data.get("subject", "")
    body = data.get("body", "")
    direction = data.get("direction", "outbound")

    contact_id = None
    company_id = None
    lookup_email = to_addr if direction == "outbound" else from_addr
    if lookup_email:
        c = db.cursor()
        row = c.execute("SELECT id FROM contacts WHERE LOWER(email) = ?",
                        (lookup_email.lower(),)).fetchone()
        if row:
            contact_id = row["id"]
            cc = c.execute("SELECT company_id FROM contact_companies WHERE contact_id = ? LIMIT 1",
                           (contact_id,)).fetchone()
            if cc:
                company_id = cc["company_id"]

    db.execute("""INSERT INTO custom_activities (contact_id, company_id, type, subject, body, created_by)
                  VALUES (?, ?, 'emails', ?, ?, ?)""",
               (contact_id, company_id, subject,
                f"{'To' if direction == 'outbound' else 'From'}: {lookup_email}\n\n{body}",
                from_addr if direction == "outbound" else "Inbound"))
    db.commit()
    aid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({"id": aid, "contact_id": contact_id, "company_id": company_id, "ok": True})


# ═══════════════════════════════════════════════════════════
#  PRODUCTION / BILLING / BULK ACTIONS / NOTES / AUDIT API
# ═══════════════════════════════════════════════════════════

def _audit(db, user, action, entity_type, entity_id, details=""):
    db.execute("INSERT INTO audit_log (user, action, entity_type, entity_id, details, timestamp) VALUES (?,?,?,?,?,?)",
               (user, action, entity_type, str(entity_id), details, datetime.now().isoformat()))


def _init_notes_table():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        content TEXT NOT NULL,
        created_by TEXT DEFAULT 'admin',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notes_entity ON notes(entity_type, entity_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS marketing_list_members (
        list_id INTEGER NOT NULL,
        contact_id TEXT NOT NULL,
        added_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (list_id, contact_id)
    )""")
    db.commit()
    db.close()

_init_notes_table()

ISSUE_DATE_SORT_EXPR = "substr(issue_date,7,2)||substr(issue_date,1,2)||substr(issue_date,4,2)"


# ── Production / Material Tracking Dashboard ──
@app.route("/api/production")
def production_dashboard():
    db = get_db()
    c = db.cursor()
    now = datetime.now()
    yr2 = f"{now.year % 100:02d}"
    pub = request.args.get("publication", "")

    where = ["canceled = 0", "likelihood = 1", "issue_date LIKE ?"]
    params = [f"%/{yr2} %"]
    if pub:
        where.append("publication = ?")
        params.append(pub)
    where_sql = " AND ".join(where)

    result = {}

    c.execute(f"""SELECT
                    SUM(CASE WHEN mat_on_hand = 1 THEN 1 ELSE 0 END) as have,
                    SUM(CASE WHEN mat_on_hand = 0 OR mat_on_hand IS NULL THEN 1 ELSE 0 END) as missing,
                    SUM(CASE WHEN mat_expected = 1 THEN 1 ELSE 0 END) as expecting,
                    SUM(CASE WHEN mat_expected = 0 OR mat_expected IS NULL THEN 1 ELSE 0 END) as not_expecting
                  FROM sm_page_history WHERE {where_sql}""", params)
    r = c.fetchone()
    result["summary"] = {
        "materials_received": r["have"] or 0,
        "materials_missing": r["missing"] or 0,
        "expecting_materials": r["expecting"] or 0,
        "not_expecting_materials": r["not_expecting"] or 0,
    }

    missing_where = where + ["(mat_on_hand = 0 OR mat_on_hand IS NULL)", "mat_expected = 1"]
    c.execute(f"""SELECT account_name, publication, issue_date, ad_size, type_ad,
                        mat_due_date, prod_status_id, materials, mat_track_num
                  FROM sm_page_history
                  WHERE {' AND '.join(missing_where)}
                  ORDER BY {ISSUE_DATE_SORT_EXPR} ASC""", params)
    missing_materials = []
    for row in c.fetchall():
        d = dict(row)
        d["issue_date_iso"] = _parse_sm_date(d["issue_date"])
        missing_materials.append(d)
    result["missing_materials"] = missing_materials

    c.execute(f"""SELECT publication, issue_date, COUNT(*) as total,
                        SUM(CASE WHEN mat_on_hand = 1 THEN 1 ELSE 0 END) as received,
                        SUM(CASE WHEN (mat_on_hand = 0 OR mat_on_hand IS NULL) THEN 1 ELSE 0 END) as missing
                  FROM sm_page_history
                  WHERE {where_sql}
                  GROUP BY publication, issue_date
                  ORDER BY {ISSUE_DATE_SORT_EXPR} ASC""", params)
    by_issue = []
    for row in c.fetchall():
        d = dict(row)
        d["issue_date_iso"] = _parse_sm_date(d["issue_date"])
        total = d["total"] or 0
        received = d["received"] or 0
        d["materials_received"] = received
        d["materials_missing"] = d["missing"] or 0
        del d["missing"]
        d["pct_complete"] = round((received / total) * 100, 1) if total else 0
        by_issue.append(d)
    result["by_issue"] = by_issue

    db.close()
    return jsonify(result)


# ── Invoice / Billing Status ──
@app.route("/api/billing")
def billing_dashboard():
    db = get_db()
    c = db.cursor()
    now = datetime.now()
    yr2 = f"{now.year % 100:02d}"
    pub = request.args.get("publication", "")

    where = ["canceled = 0", "likelihood = 1", "issue_date LIKE ?"]
    params = [f"%/{yr2} %"]
    if pub:
        where.append("publication = ?")
        params.append(pub)
    where_sql = " AND ".join(where)

    result = {}

    c.execute(f"""SELECT
                    SUM(CASE WHEN bill_cost > 0 THEN 1 ELSE 0 END) as billed,
                    SUM(CASE WHEN bill_cost IS NULL OR bill_cost <= 0 THEN 1 ELSE 0 END) as unbilled,
                    SUM(CASE WHEN bill_cost > 0 THEN bill_cost ELSE 0 END) as billed_revenue,
                    SUM(CASE WHEN bill_cost IS NULL OR bill_cost <= 0 THEN ad_cost ELSE 0 END) as unbilled_revenue,
                    SUM(CASE WHEN bill_cost > 0 AND (paid_date IS NULL OR paid_date = '') THEN bill_cost ELSE 0 END) as outstanding
                  FROM sm_page_history WHERE {where_sql}""", params)
    r = c.fetchone()
    billed_revenue = r["billed_revenue"] or 0
    unbilled_revenue = r["unbilled_revenue"] or 0
    result["summary"] = {
        "total_billed": r["billed"] or 0,
        "total_unbilled": r["unbilled"] or 0,
        "total_revenue": billed_revenue if billed_revenue else unbilled_revenue,
        "outstanding": r["outstanding"] or 0,
    }

    unbilled_where = where + ["(bill_cost = 0 OR bill_cost IS NULL)", "ad_cost > 0"]
    c.execute(f"""SELECT account_name, publication, issue_date, type_ad, ad_size, ad_cost, rep1
                  FROM sm_page_history
                  WHERE {' AND '.join(unbilled_where)}
                  ORDER BY {ISSUE_DATE_SORT_EXPR} ASC""", params)
    unbilled = []
    for row in c.fetchall():
        d = dict(row)
        d["issue_date_iso"] = _parse_sm_date(d["issue_date"])
        unbilled.append(d)
    result["unbilled"] = unbilled

    unpaid_where = where + ["bill_cost > 0", "(paid_date = '' OR paid_date IS NULL)", "invoice_num != ''"]
    c.execute(f"""SELECT account_name, invoice_num, invoice_date, bill_cost, issue_date, publication
                  FROM sm_page_history
                  WHERE {' AND '.join(unpaid_where)}
                  ORDER BY invoice_date ASC""", params)
    unpaid = []
    for row in c.fetchall():
        d = dict(row)
        d["invoice_date_iso"] = _parse_sm_date(d["invoice_date"])
        d["issue_date_iso"] = _parse_sm_date(d["issue_date"])
        unpaid.append(d)
    result["unpaid"] = unpaid

    db.close()
    return jsonify(result)


# ── Production Calendar ──
@app.route("/api/production/calendar")
def production_calendar():
    db = get_db()
    c = db.cursor()
    now = datetime.now()
    yr2 = f"{now.year % 100:02d}"
    pub = request.args.get("publication", "")

    where = ["canceled = 0", "issue_date LIKE ?"]
    params = [f"%/{yr2} %"]
    if pub:
        where.append("publication = ?")
        params.append(pub)
    where_sql = " AND ".join(where)

    c.execute(f"""SELECT issue_date,
                        GROUP_CONCAT(DISTINCT publication) as pubs,
                        COUNT(*) as total_insertions,
                        SUM(CASE WHEN likelihood = 1 THEN 1 ELSE 0 END) as confirmed,
                        SUM(CASE WHEN likelihood = 10 THEN 1 ELSE 0 END) as proposals,
                        SUM(CASE WHEN mat_on_hand = 1 AND likelihood = 1 THEN 1 ELSE 0 END) as materials_received,
                        SUM(CASE WHEN (mat_on_hand = 0 OR mat_on_hand IS NULL) AND mat_expected = 1 AND likelihood = 1 THEN 1 ELSE 0 END) as materials_missing,
                        SUM(CASE WHEN likelihood = 1 THEN ad_cost ELSE 0 END) as revenue
                  FROM sm_page_history
                  WHERE {where_sql}
                  GROUP BY issue_date
                  ORDER BY {ISSUE_DATE_SORT_EXPR} ASC""", params)

    result = []
    for row in c.fetchall():
        d = dict(row)
        d["issue_date_iso"] = _parse_sm_date(d["issue_date"])
        d["publications"] = sorted(set((d.pop("pubs") or "").split(",")))
        result.append(d)

    db.close()
    return jsonify(result)


# ── Bulk Actions on Contacts ──
@app.route("/api/contacts/bulk", methods=["POST"])
def contacts_bulk_action():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    action = data.get("action", "")
    list_id = data.get("list_id")
    user = data.get("user", "admin")

    if not ids or not isinstance(ids, list):
        return jsonify({"error": "ids (list) required"}), 400
    if action not in ("add_to_list", "export", "delete"):
        return jsonify({"error": "invalid action"}), 400

    db = get_db()
    placeholders = ",".join(["?"] * len(ids))
    affected = 0

    if action == "add_to_list":
        if not list_id:
            db.close()
            return jsonify({"error": "list_id required for add_to_list"}), 400
        row = db.execute("SELECT id FROM marketing_lists WHERE id = ?", (list_id,)).fetchone()
        if not row:
            db.close()
            return jsonify({"error": "list not found"}), 404
        for cid in ids:
            db.execute("INSERT OR IGNORE INTO marketing_list_members (list_id, contact_id) VALUES (?, ?)",
                       (list_id, cid))
            affected += 1
        _audit(db, user, "bulk_add_to_list", "contact", ",".join(str(i) for i in ids),
               json.dumps({"list_id": list_id, "count": affected}))
        db.commit()
        db.close()
        return jsonify({"ok": True, "affected": affected})

    elif action == "export":
        c = db.cursor()
        c.execute(f"SELECT id, all_properties FROM contacts WHERE id IN ({placeholders})", ids)
        rows = []
        all_keys = set()
        for r in c.fetchall():
            props = json.loads(r["all_properties"] or "{}")
            props["hubspot_id"] = r["id"]
            all_keys.update(props.keys())
            rows.append(props)
        priority = ["hubspot_id", "firstname", "lastname", "email", "phone", "company", "jobtitle",
                    "city", "state", "zip", "country", "owner_name", "owner_email"]
        ordered_keys = [k for k in priority if k in all_keys]
        ordered_keys += sorted(all_keys - set(priority))
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=ordered_keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        _audit(db, user, "bulk_export", "contact", ",".join(str(i) for i in ids),
               json.dumps({"count": len(rows)}))
        db.commit()
        db.close()
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=devnex_contacts_bulk_export.csv"}
        )

    elif action == "delete":
        c = db.cursor()
        c.execute(f"SELECT COUNT(*) FROM contacts WHERE id IN ({placeholders})", ids)
        affected = c.fetchone()[0]
        db.execute(f"DELETE FROM contacts WHERE id IN ({placeholders})", ids)
        _audit(db, user, "bulk_delete", "contact", ",".join(str(i) for i in ids),
               json.dumps({"count": affected}))
        db.commit()
        db.close()
        return jsonify({"ok": True, "affected": affected})


# ── Custom Notes ──
@app.route("/api/notes", methods=["GET"])
def list_notes():
    entity_type = request.args.get("entity_type", "")
    entity_id = request.args.get("entity_id", "")
    if not entity_type or not entity_id:
        return jsonify({"error": "entity_type and entity_id required"}), 400
    db = get_db()
    rows = db.execute("""SELECT * FROM notes WHERE entity_type = ? AND entity_id = ?
                         ORDER BY created_at DESC""", (entity_type, entity_id)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/notes", methods=["POST"])
def create_note():
    data = request.get_json() or {}
    entity_type = data.get("entity_type", "")
    entity_id = data.get("entity_id", "")
    content = data.get("content", "")
    created_by = data.get("created_by", "admin")
    if not entity_type or not entity_id or not content:
        return jsonify({"error": "entity_type, entity_id and content required"}), 400
    db = get_db()
    db.execute("""INSERT INTO notes (entity_type, entity_id, content, created_by)
                  VALUES (?, ?, ?, ?)""",
               (entity_type, str(entity_id), content, created_by))
    nid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    _audit(db, created_by, "create", "note", nid, json.dumps({"entity_type": entity_type, "entity_id": entity_id}))
    db.commit()
    db.close()
    return jsonify({"id": nid, "ok": True})


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    user = request.args.get("user", "admin")
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    _audit(db, user, "delete", "note", note_id)
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Audit Trail ──
@app.route("/api/audit")
def audit_trail():
    db = get_db()
    entity_type = request.args.get("entity_type", "")
    entity_id = request.args.get("entity_id", "")
    limit = request.args.get("limit", "")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    where, params = [], []
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        where.append("entity_id = ?")
        params.append(str(entity_id))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = db.execute(f"SELECT COUNT(*) FROM audit_log {where_sql}", params).fetchone()[0]

    if limit:
        rows = db.execute(f"SELECT * FROM audit_log {where_sql} ORDER BY timestamp DESC LIMIT ?",
                          params + [int(limit)]).fetchall()
        db.close()
        return jsonify({"entries": [dict(r) for r in rows], "total": total})

    rows = db.execute(f"SELECT * FROM audit_log {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                      params + [per_page, (page - 1) * per_page]).fetchall()
    db.close()
    return jsonify({
        "entries": [dict(r) for r in rows],
        "total": total, "page": page, "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


if __name__ == "__main__":
    print("Starting DevIQ CRM at http://localhost:5151")
    app.run(host="0.0.0.0", port=5151, debug=False)
