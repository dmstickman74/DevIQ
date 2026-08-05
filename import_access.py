import csv
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "crm.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "access_data")

def read_csv(filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))

def import_all():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")

    # --- SpaceMaster Companies (ad accounts) ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_companies (
        id INTEGER PRIMARY KEY,
        company TEXT,
        credit_hold INTEGER DEFAULT 0,
        credit_hold_reason TEXT
    )""")
    c.execute("DELETE FROM sm_companies")
    rows = read_csv("COMPANIES.csv")
    for r in rows:
        cid = r.get("ID", "").strip()
        if not cid:
            continue
        c.execute("INSERT OR IGNORE INTO sm_companies VALUES (?,?,?,?)", (
            int(cid), r.get("Company", ""), int(r.get("credithold", 0) or 0),
            r.get("CreditHoldReason", "")
        ))
    print(f"  sm_companies: {c.execute('SELECT COUNT(*) FROM sm_companies').fetchone()[0]}")

    # --- Directory (contacts for ad accounts) ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_directory (
        id INTEGER PRIMARY KEY,
        imis_id TEXT,
        company TEXT,
        name TEXT,
        phone TEXT,
        ext TEXT,
        cell TEXT,
        email TEXT,
        url TEXT,
        title TEXT,
        fax TEXT,
        associated_company TEXT,
        contact_type TEXT,
        street TEXT,
        street2 TEXT,
        city TEXT,
        state TEXT,
        zip TEXT,
        country TEXT,
        prefix TEXT,
        notes TEXT,
        owned_by TEXT,
        mailing_list INTEGER DEFAULT 0,
        call_list INTEGER DEFAULT 0,
        email_list INTEGER DEFAULT 0,
        default_contact INTEGER DEFAULT 0,
        updated_date TEXT
    )""")
    c.execute("DELETE FROM sm_directory")
    rows = read_csv("DIRECTORY.csv")
    for r in rows:
        did = r.get("Directory ID", "").strip()
        if not did:
            continue
        c.execute("INSERT OR IGNORE INTO sm_directory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            int(did), r.get("IMIS_ID", ""), r.get("Company", ""), r.get("Name", ""),
            r.get("Business Phone", ""), r.get("Ext", ""), r.get("Cellular Phone", ""),
            r.get("E-Mail", ""), r.get("URL Address", ""), r.get("Title", ""),
            r.get("Fax", ""), r.get("AssociatedCompany", ""), r.get("Type", ""),
            r.get("Street", ""), r.get("Street2", ""), r.get("City", ""),
            r.get("State", ""), r.get("Zip", ""), r.get("Country", ""),
            r.get("Prefix", ""), r.get("Notes", ""), r.get("OwnedBy", ""),
            int(r.get("MailingList", 0) or 0), int(r.get("CallList", 0) or 0),
            int(r.get("EmailList", 0) or 0), int(r.get("DefaultContact", 0) or 0),
            r.get("_Date", "")
        ))
    print(f"  sm_directory: {c.execute('SELECT COUNT(*) FROM sm_directory').fetchone()[0]}")

    # --- Publications ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_publications (
        publication TEXT PRIMARY KEY,
        pub_num TEXT,
        gl_code TEXT
    )""")
    c.execute("DELETE FROM sm_publications")
    rows = read_csv("PUBLICATIONS.csv")
    for r in rows:
        pub = r.get("Publication", "").strip()
        if not pub:
            continue
        c.execute("INSERT OR IGNORE INTO sm_publications VALUES (?,?,?)", (
            pub, r.get("PubNum", ""), r.get("GLCode", "")
        ))
    print(f"  sm_publications: {c.execute('SELECT COUNT(*) FROM sm_publications').fetchone()[0]}")

    # --- Rep List ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_reps (
        rep_id TEXT PRIMARY KEY,
        rep TEXT,
        territory_id TEXT,
        territory TEXT,
        commission REAL DEFAULT 0
    )""")
    c.execute("DELETE FROM sm_reps")
    rows = read_csv("REP_LIST.csv")
    for r in rows:
        rid = r.get("Rep ID", "").strip()
        if not rid:
            continue
        comm = r.get("Comm", "0") or "0"
        c.execute("INSERT OR IGNORE INTO sm_reps VALUES (?,?,?,?,?)", (
            rid, r.get("Rep", ""), r.get("Ter ID", ""), r.get("Territory", ""),
            float(comm)
        ))
    print(f"  sm_reps: {c.execute('SELECT COUNT(*) FROM sm_reps').fetchone()[0]}")

    # --- Issue Dates ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_issue_dates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cover_date TEXT,
        publication TEXT,
        closing_date TEXT
    )""")
    c.execute("DELETE FROM sm_issue_dates")
    rows = read_csv("ISSUEDATES.csv")
    for r in rows:
        c.execute("INSERT INTO sm_issue_dates (cover_date, publication, closing_date) VALUES (?,?,?)", (
            r.get("Cover Date", ""), r.get("Publication", ""), r.get("Closing Date", "")
        ))
    print(f"  sm_issue_dates: {c.execute('SELECT COUNT(*) FROM sm_issue_dates').fetchone()[0]}")

    # --- Rate Card ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_rate_card (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        publication TEXT,
        rate_card_num TEXT,
        ad_size TEXT,
        rate INTEGER,
        type_ad TEXT,
        color TEXT,
        ad_cost REAL
    )""")
    c.execute("DELETE FROM sm_rate_card")
    rows = read_csv("RATE_CARD.csv")
    for r in rows:
        cost = r.get("Ad Cost", "0") or "0"
        rate = r.get("Rate", "0") or "0"
        c.execute("INSERT INTO sm_rate_card (publication, rate_card_num, ad_size, rate, type_ad, color, ad_cost) VALUES (?,?,?,?,?,?,?)", (
            r.get("Publication", ""), r.get("Rate Card Num", ""), r.get("Ad Size", ""),
            int(rate), r.get("TypeAd", ""), r.get("Color", ""), float(cost)
        ))
    print(f"  sm_rate_card: {c.execute('SELECT COUNT(*) FROM sm_rate_card').fetchone()[0]}")

    # --- Ad Sizes, Types, Colors (lookup tables) ---
    c.execute("CREATE TABLE IF NOT EXISTS sm_ad_sizes (ad_size TEXT PRIMARY KEY)")
    c.execute("DELETE FROM sm_ad_sizes")
    for r in read_csv("AD_SIZES.csv"):
        val = r.get("Ad Size", "").strip()
        if val:
            c.execute("INSERT OR IGNORE INTO sm_ad_sizes VALUES (?)", (val,))

    c.execute("CREATE TABLE IF NOT EXISTS sm_ad_types (ad_type TEXT PRIMARY KEY)")
    c.execute("DELETE FROM sm_ad_types")
    for r in read_csv("AD_TYPES.csv"):
        val = r.get("Ad Type", "").strip()
        if val:
            c.execute("INSERT OR IGNORE INTO sm_ad_types VALUES (?)", (val,))

    c.execute("CREATE TABLE IF NOT EXISTS sm_ad_colors (color TEXT PRIMARY KEY)")
    c.execute("DELETE FROM sm_ad_colors")
    for r in read_csv("AD_COLORS.csv"):
        val = r.get("Color", "").strip()
        if val:
            c.execute("INSERT OR IGNORE INTO sm_ad_colors VALUES (?)", (val,))
    print(f"  sm_ad_sizes: {c.execute('SELECT COUNT(*) FROM sm_ad_sizes').fetchone()[0]}")
    print(f"  sm_ad_types: {c.execute('SELECT COUNT(*) FROM sm_ad_types').fetchone()[0]}")
    print(f"  sm_ad_colors: {c.execute('SELECT COUNT(*) FROM sm_ad_colors').fetchone()[0]}")

    # --- Product Categories ---
    c.execute("CREATE TABLE IF NOT EXISTS sm_product_categories (category TEXT PRIMARY KEY)")
    c.execute("DELETE FROM sm_product_categories")
    for r in read_csv("PRODUCT_CATEGORIES.csv"):
        val = r.get("Product Category", "").strip()
        if val:
            c.execute("INSERT OR IGNORE INTO sm_product_categories VALUES (?)", (val,))
    print(f"  sm_product_categories: {c.execute('SELECT COUNT(*) FROM sm_product_categories').fetchone()[0]}")

    # --- Production Statuses ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_prod_statuses (
        id INTEGER PRIMARY KEY,
        status TEXT
    )""")
    c.execute("DELETE FROM sm_prod_statuses")
    for r in read_csv("tblProdStatus.csv"):
        sid = r.get("ProdStatusId", "").strip()
        if sid:
            c.execute("INSERT OR IGNORE INTO sm_prod_statuses VALUES (?,?)", (
                int(sid), r.get("ProdStatus", "")
            ))
    print(f"  sm_prod_statuses: {c.execute('SELECT COUNT(*) FROM sm_prod_statuses').fetchone()[0]}")

    # --- Account/Category (links accounts to product categories) ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_account_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        account_name TEXT,
        publication TEXT,
        product_category TEXT
    )""")
    c.execute("DELETE FROM sm_account_categories")
    rows = read_csv("ACCOUNT_CATEGORY.csv")
    for r in rows:
        c.execute("INSERT INTO sm_account_categories (contract_id, account_name, publication, product_category) VALUES (?,?,?,?)", (
            r.get("Contract ID", ""), r.get("Account Name", ""),
            r.get("Publication", ""), r.get("Product Category", "")
        ))
    print(f"  sm_account_categories: {c.execute('SELECT COUNT(*) FROM sm_account_categories').fetchone()[0]}")

    # --- Contracts ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        account_name TEXT,
        agency_name TEXT,
        bill_name TEXT,
        publication TEXT,
        rate_card_num TEXT,
        rate TEXT,
        contract_start TEXT,
        contract_end TEXT,
        bill INTEGER,
        credit_hold INTEGER DEFAULT 0,
        agency_discount REAL,
        status TEXT,
        terms TEXT,
        notes TEXT,
        type_ad TEXT,
        rep1 TEXT,
        territory1 TEXT,
        rep2 TEXT,
        territory2 TEXT,
        rep3 TEXT,
        territory3 TEXT,
        rep4 TEXT,
        territory4 TEXT,
        split1 REAL,
        split2 REAL,
        split3 REAL,
        split4 REAL,
        created_date TEXT
    )""")
    c.execute("DELETE FROM sm_contracts")
    rows = read_csv("CONTRACTS.csv")
    for r in rows:
        def flt(v): return float(v) if v and v.strip() else 0.0
        c.execute("""INSERT INTO sm_contracts (contract_id, account_name, agency_name, bill_name,
            publication, rate_card_num, rate, contract_start, contract_end, bill, credit_hold,
            agency_discount, status, terms, notes, type_ad, rep1, territory1, rep2, territory2,
            rep3, territory3, rep4, territory4, split1, split2, split3, split4, created_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            r.get("Contract ID", ""), r.get("Account Name", ""), r.get("Agency Name", ""),
            r.get("BillName", ""), r.get("Publication", ""), r.get("Rate Card Num", ""),
            r.get("Rate", ""), r.get("Contract Start Date", ""), r.get("Contract End Date", ""),
            int(r.get("Bill", 0) or 0), int(r.get("Credit Hold", 0) or 0),
            flt(r.get("Agency Discount", "")), r.get("Status", ""), r.get("Terms", ""),
            r.get("Notes", ""), r.get("TypeAd", ""),
            r.get("Rep1", ""), r.get("Territory1", ""), r.get("Rep2", ""), r.get("Territory2", ""),
            r.get("Rep3", ""), r.get("Territory3", ""), r.get("Rep4", ""), r.get("Territory4", ""),
            flt(r.get("Split1", "")), flt(r.get("Split2", "")),
            flt(r.get("Split3", "")), flt(r.get("Split4", "")),
            r.get("_Date", "")
        ))
    print(f"  sm_contracts: {c.execute('SELECT COUNT(*) FROM sm_contracts').fetchone()[0]}")

    # --- Page History (the big one: every ad insertion) ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_page_history (
        page_history_id INTEGER PRIMARY KEY,
        contract_num TEXT,
        order_num TEXT,
        invoice_num TEXT,
        account_name TEXT,
        agency_name TEXT,
        bill_name TEXT,
        type_ad TEXT,
        canceled INTEGER DEFAULT 0,
        is_frozen INTEGER DEFAULT 0,
        is_open INTEGER DEFAULT 0,
        invoice_date TEXT,
        paid_date TEXT,
        category TEXT,
        issue_date TEXT,
        ad_size TEXT,
        color TEXT,
        pms_color TEXT,
        rc_ad_cost REAL,
        ad_cost REAL,
        net_cost REAL,
        gross_cost REAL,
        bill_cost REAL,
        bill INTEGER,
        prepayment REAL,
        premium REAL,
        start_date TEXT,
        end_date TEXT,
        headline TEXT,
        commitment INTEGER DEFAULT 0,
        mat_on_hand INTEGER DEFAULT 0,
        materials TEXT,
        mat_expected INTEGER DEFAULT 0,
        mat_track_num TEXT,
        mat_pub TEXT,
        mat_from_page TEXT,
        mat_changes TEXT,
        last_ran_date TEXT,
        new_materials_date TEXT,
        position_request TEXT,
        position TEXT,
        separation TEXT,
        placement TEXT,
        comments TEXT,
        rate_card_num TEXT,
        rate TEXT,
        publication TEXT,
        rep1 TEXT,
        territory1 TEXT,
        rep2 TEXT,
        territory2 TEXT,
        rep3 TEXT,
        territory3 TEXT,
        rep4 TEXT,
        territory4 TEXT,
        split1 REAL,
        split2 REAL,
        split3 REAL,
        split4 REAL,
        discount REAL,
        ag_disc REAL,
        materials_contact_id INTEGER,
        likelihood INTEGER,
        prod_status_id INTEGER,
        url_address TEXT,
        booth_num TEXT,
        mat_due_date TEXT,
        surcharge REAL,
        columns INTEGER,
        inches INTEGER,
        page_num TEXT,
        created_date TEXT
    )""")
    c.execute("DELETE FROM sm_page_history")
    rows = read_csv("PAGE_HISTORY.csv")
    count = 0
    for r in rows:
        def flt(v): return float(v) if v and v.strip() else 0.0
        def intv(v): return int(v) if v and v.strip() else 0
        phid = r.get("Page History ID", "").strip()
        if not phid:
            continue
        c.execute("""INSERT OR IGNORE INTO sm_page_history VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            int(phid), r.get("ContractNum", ""), r.get("OrderNum", ""),
            r.get("InvoiceNum", ""), r.get("Account Name", ""), r.get("Agency Name", ""),
            r.get("BillName", ""), r.get("TypeAd", ""),
            intv(r.get("Canceled", "")), intv(r.get("IsFrozen", "")), intv(r.get("isOpen", "")),
            r.get("InvoiceDate", ""), r.get("PaidDate", ""),
            r.get("Category", ""), r.get("Issue Date", ""),
            r.get("Ad Size", ""), r.get("Color", ""), r.get("PMS_Color", ""),
            flt(r.get("RC Ad Cost", "")), flt(r.get("Ad Cost", "")),
            flt(r.get("Net Cost", "")), flt(r.get("Gross Cost", "")),
            flt(r.get("Bill Cost", "")), intv(r.get("Bill", "")),
            flt(r.get("Prepayment", "")), flt(r.get("Premium", "")),
            r.get("Start Date", ""), r.get("End Date", ""),
            r.get("Headline", ""), intv(r.get("Commitment", "")),
            intv(r.get("MatOnHand", "")), r.get("Materials", ""),
            intv(r.get("MatExpected", "")), r.get("MatTrackNum", ""),
            r.get("MatPub", ""), r.get("matFromPage", ""),
            r.get("MatChanges", ""), r.get("LastRanDate", ""),
            r.get("NewMaterialsDate", ""), r.get("Position Request", ""),
            r.get("Position", ""), r.get("Separation", ""),
            r.get("Placement", ""), r.get("Comments", ""),
            r.get("Rate Card Num", ""), r.get("Rate", ""),
            r.get("Publication", ""),
            r.get("Rep1", ""), r.get("Territory1", ""),
            r.get("Rep2", ""), r.get("Territory2", ""),
            r.get("Rep3", ""), r.get("Territory3", ""),
            r.get("Rep4", ""), r.get("Territory4", ""),
            flt(r.get("Split1", "")), flt(r.get("Split2", "")),
            flt(r.get("Split3", "")), flt(r.get("Split4", "")),
            flt(r.get("discount", "")), flt(r.get("agDisc", "")),
            intv(r.get("MaterialsContactID", "")),
            intv(r.get("Likelihood", "")), intv(r.get("ProdStatusId", "")),
            r.get("URLAddress", ""), r.get("BoothNum", ""),
            r.get("MatDueDate", ""), flt(r.get("surcharge", "")),
            intv(r.get("Columns", "")), intv(r.get("Inches", "")),
            r.get("PageNum", ""), r.get("_Date", "")
        ))
        count += 1
    print(f"  sm_page_history: {count}")

    # --- Transactions ---
    c.execute("""CREATE TABLE IF NOT EXISTS sm_transactions (
        trans_id INTEGER PRIMARY KEY,
        invoice_num TEXT,
        payment_type_id INTEGER,
        check_no TEXT,
        description TEXT,
        debit_amount REAL,
        credit_amount REAL,
        transaction_date TEXT,
        user_id TEXT,
        multi_payment_id INTEGER
    )""")
    c.execute("DELETE FROM sm_transactions")
    rows = read_csv("Transactions.csv")
    for r in rows:
        tid = r.get("TransID", "").strip()
        if not tid:
            continue
        def flt(v): return float(v) if v and v.strip() else 0.0
        c.execute("INSERT OR IGNORE INTO sm_transactions VALUES (?,?,?,?,?,?,?,?,?,?)", (
            int(tid), r.get("InvoiceNum", ""),
            int(r.get("PaymentTypeID", 0) or 0), r.get("CheckNo", ""),
            r.get("Description", ""), flt(r.get("DebitAmount", "")),
            flt(r.get("CreditAmount", "")), r.get("TransactionDateTime", ""),
            r.get("User", ""), int(r.get("MultiPaymentID", 0) or 0)
        ))
    print(f"  sm_transactions: {c.execute('SELECT COUNT(*) FROM sm_transactions').fetchone()[0]}")

    # --- Indexes ---
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_ph_account ON sm_page_history(account_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_ph_pub ON sm_page_history(publication)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_ph_issue ON sm_page_history(issue_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_ph_rep1 ON sm_page_history(rep1)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_ph_type ON sm_page_history(type_ad)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_ph_status ON sm_page_history(prod_status_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_contracts_account ON sm_contracts(account_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_contracts_pub ON sm_contracts(publication)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_dir_company ON sm_directory(company)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_dir_email ON sm_directory(email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sm_trans_invoice ON sm_transactions(invoice_num)")

    conn.commit()
    conn.close()
    print("\nImport complete!")


if __name__ == "__main__":
    import_all()
