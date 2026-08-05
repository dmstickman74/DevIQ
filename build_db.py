import json
import sqlite3
import os
import sys

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "hubspot_crm_data.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "crm.db")

def build():
    print("Loading JSON...")
    with open(JSON_PATH) as f:
        data = json.load(f)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")

    # --- Contacts ---
    c.execute("""CREATE TABLE contacts (
        id TEXT PRIMARY KEY,
        firstname TEXT,
        lastname TEXT,
        email TEXT,
        phone TEXT,
        company TEXT,
        jobtitle TEXT,
        city TEXT,
        state TEXT,
        country TEXT,
        owner_name TEXT,
        lifecyclestage TEXT,
        hs_lead_status TEXT,
        createdate TEXT,
        lastmodifieddate TEXT,
        all_properties TEXT
    )""")

    # --- Companies ---
    c.execute("""CREATE TABLE companies (
        id TEXT PRIMARY KEY,
        name TEXT,
        domain TEXT,
        industry TEXT,
        city TEXT,
        state TEXT,
        country TEXT,
        phone TEXT,
        owner_name TEXT,
        all_properties TEXT
    )""")

    # --- Contact-Company link ---
    c.execute("""CREATE TABLE contact_companies (
        contact_id TEXT,
        company_id TEXT,
        PRIMARY KEY (contact_id, company_id)
    )""")

    # --- Activities ---
    c.execute("""CREATE TABLE activities (
        id TEXT PRIMARY KEY,
        contact_id TEXT,
        type TEXT,
        timestamp TEXT,
        subject TEXT,
        body TEXT,
        direction TEXT,
        status TEXT,
        owner_name TEXT,
        from_email TEXT,
        to_email TEXT,
        all_properties TEXT
    )""")

    # --- Owners ---
    c.execute("""CREATE TABLE owners (
        id TEXT PRIMARY KEY,
        name TEXT,
        email TEXT
    )""")

    for oid, o in data.get("owners", {}).items():
        c.execute("INSERT OR IGNORE INTO owners VALUES (?,?,?)",
                  (oid, o.get("name",""), o.get("email","")))

    print(f"Processing {len(data['contacts'])} contacts...")
    activity_count = 0

    for contact in data["contacts"]:
        cid = contact.get("hubspot_id", "")
        props = {k: v for k, v in contact.items() if k not in ("companies", "activities", "hubspot_id")}

        c.execute("INSERT OR IGNORE INTO contacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            cid,
            contact.get("firstname", ""),
            contact.get("lastname", ""),
            contact.get("email", ""),
            contact.get("phone", "") or contact.get("mobilephone", ""),
            contact.get("company", ""),
            contact.get("jobtitle", ""),
            contact.get("city", ""),
            contact.get("state", ""),
            contact.get("country", ""),
            contact.get("owner_name", ""),
            contact.get("lifecyclestage", ""),
            contact.get("hs_lead_status", ""),
            contact.get("createdate", ""),
            contact.get("lastmodifieddate", ""),
            json.dumps(props, default=str),
        ))

        for comp in contact.get("companies", []):
            comp_id = comp.get("hubspot_id", "")
            if not comp_id:
                continue
            c.execute("INSERT OR IGNORE INTO contact_companies VALUES (?,?)", (cid, comp_id))
            comp_props = {k: v for k, v in comp.items() if k != "hubspot_id"}
            c.execute("INSERT OR IGNORE INTO companies VALUES (?,?,?,?,?,?,?,?,?,?)", (
                comp_id,
                comp.get("name", ""),
                comp.get("domain", ""),
                comp.get("industry", ""),
                comp.get("city", ""),
                comp.get("state", ""),
                comp.get("country", ""),
                comp.get("phone", ""),
                comp.get("owner_name", ""),
                json.dumps(comp_props, default=str),
            ))

        for act in contact.get("activities", []):
            aid = act.get("activity_id", "")
            atype = act.get("type", "")

            subject = ""
            body = ""
            direction = ""
            status = ""
            from_email = ""
            to_email = ""

            if atype == "emails":
                subject = act.get("hs_email_subject", "")
                body = act.get("hs_email_text", "") or act.get("hs_email_html", "")
                direction = act.get("hs_email_direction", "")
                status = act.get("hs_email_status", "")
                from_email = act.get("hs_email_from_email", "")
                to_email = act.get("hs_email_to_email", "")
            elif atype == "calls":
                subject = act.get("hs_call_title", "")
                body = act.get("hs_call_body", "")
                direction = act.get("hs_call_direction", "")
                status = act.get("hs_call_status", "")
            elif atype == "meetings":
                subject = act.get("hs_meeting_title", "")
                body = act.get("hs_meeting_body", "") or act.get("hs_internal_meeting_notes", "")
            elif atype == "notes":
                body = act.get("hs_note_body", "")
            elif atype == "tasks":
                subject = act.get("hs_task_subject", "")
                body = act.get("hs_task_body", "")
                status = act.get("hs_task_status", "")

            act_props = {k: v for k, v in act.items() if k != "activity_id"}

            c.execute("INSERT OR IGNORE INTO activities VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                f"{aid}_{cid}",
                cid,
                atype,
                act.get("hs_timestamp", ""),
                subject,
                body,
                direction,
                status,
                act.get("owner_name", ""),
                from_email,
                to_email,
                json.dumps(act_props, default=str),
            ))
            activity_count += 1

    # Indexes
    c.execute("CREATE INDEX idx_contacts_email ON contacts(email)")
    c.execute("CREATE INDEX idx_contacts_company ON contacts(company)")
    c.execute("CREATE INDEX idx_contacts_owner ON contacts(owner_name)")
    c.execute("CREATE INDEX idx_contacts_name ON contacts(firstname, lastname)")
    c.execute("CREATE INDEX idx_activities_contact ON activities(contact_id)")
    c.execute("CREATE INDEX idx_activities_type ON activities(type)")
    c.execute("CREATE INDEX idx_activities_ts ON activities(timestamp DESC)")
    c.execute("CREATE INDEX idx_cc_contact ON contact_companies(contact_id)")
    c.execute("CREATE INDEX idx_cc_company ON contact_companies(company_id)")

    # FTS for full-text search
    c.execute("""CREATE VIRTUAL TABLE contacts_fts USING fts5(
        id, firstname, lastname, email, company, jobtitle, city, state, owner_name
    )""")
    c.execute("""INSERT INTO contacts_fts
        SELECT id, firstname, lastname, email, company, jobtitle, city, state, owner_name
        FROM contacts""")

    conn.commit()

    # Stats
    c.execute("SELECT COUNT(*) FROM contacts")
    nc = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM companies")
    nco = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM activities")
    na = c.fetchone()[0]

    conn.close()
    size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"\nDatabase built: {DB_PATH}")
    print(f"  {nc} contacts, {nco} companies, {na} activities")
    print(f"  Size: {size_mb:.1f} MB")


if __name__ == "__main__":
    build()
