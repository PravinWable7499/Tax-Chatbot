
import os
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import google.generativeai as genai

from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ==========================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==========================================

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing in .env")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing in .env")


# ==========================================
# 2. CONFIGURE GEMINI AI
# ==========================================

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")


# ==========================================
# 3. DEBUG INFORMATION
# ==========================================

print("ENV FILE EXISTS:", ENV_FILE.exists())
print("GEMINI KEY LOADED:", bool(GEMINI_API_KEY))
print("DATABASE URL LOADED:", bool(DATABASE_URL))
print("GEMINI MODEL INITIALIZED")

# =====================================================
# FASTAPI
# =====================================================

app = FastAPI(title="TaxDB Gemini Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# =====================================================
# SQL SERVER CONNECTION
# =====================================================

# conn_str = (
#     "DRIVER={ODBC Driver 18 for SQL Server};"
#     "SERVER=localhost\\SQLEXPRESS;"
#     "DATABASE=TaxDB;"
#     "Trusted_Connection=yes;"
#     "TrustServerCertificate=yes;"
# )

# =====================================================
# NEON POSTGRESQL CONNECTION
# =====================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing")


def get_connection():

    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        sslmode="require"
    )

# =====================================================
# REQUEST MODEL
# =====================================================

class QuestionRequest(BaseModel):
    question: str

# =====================================================
# GEMINI SQL GENERATOR
# =====================================================


def generate_sql(question):

    prompt = f"""
You are an expert PostgreSQL SQL query generator for a
Municipal Property Tax Management System.

Your job is to understand questions written in:
1. English
2. Marathi
3. Roman Marathi
4. Mixed English + Marathi

Generate accurate, executable PostgreSQL SELECT queries
using the actual Neon PostgreSQL database schema.

========================================================
1. DATABASE INFORMATION
========================================================

Database: taxdb
Database engine: PostgreSQL
Platform: Neon PostgreSQL

IMPORTANT:
The original database was SQL Server, but it has been
migrated to PostgreSQL.

All table names and column names are lowercase
snake_case.

NEVER generate SQL Server syntax.

========================================================
2. ACTUAL DATABASE TABLES AND COLUMNS
========================================================

TABLE 1: property_management_report

Columns:

sr_no
prop_no
name_of_owner
property_address
mobile_no
usage
nrv
outstanding_arrears
current_outstanding
penalty_as_on_month
total

TABLE 2: payment_management_report

Columns:

sr_no
bill_no
payment_date
property_no
zone
address
payment_mode
paid_amount
total_outstanding
total_paid
total_balance

TABLE 3: bill_collector_report

Columns:

sr_no
collector_name
mobile_no
email
zone
block
subblock
total_property
tax_recovered
today_tax_recovered
total_tax_collection
total_tax_pending
today_reporting
total_reporting
today_complaint
total_complaint
today_notice
total_notice
login_time
logout_time

TABLE 4: complaint_management_report

Columns:

sr_no
date
complaint_caption
complaint_type
complaint_by_name_and_contact_no
property_id
owner_contact_number
complaint_status
action_taken_caption
action_taken_by

TABLE 5: notice_management_report

Columns:

sr_no
property_id
owner_name
contact_number
property_address
pending_tax
notice_by
notice
notice_duration
notice_status

TABLE 6: reporting_management_report

Columns:

sr_no
date
report_type
report_caption
bill_collector_name
bill_collector_contact_no
property_id
owner_name
owner_contact_no

IMPORTANT:
Use ONLY these six tables.
Never invent another table or column.
Never use the original SQL Server table names.

========================================================
3. TABLE RELATIONSHIPS
========================================================

PROPERTY AND PAYMENT:

property_management_report.prop_no
=
payment_management_report.property_no

PROPERTY AND COMPLAINT:

property_management_report.prop_no
=
complaint_management_report.property_id

PROPERTY AND NOTICE:

property_management_report.prop_no
=
notice_management_report.property_id

PROPERTY AND REPORTING:

property_management_report.prop_no
=
reporting_management_report.property_id

Use INNER JOIN when matching records must exist
in both tables.

Use LEFT JOIN when all properties must be displayed,
including properties without matching records.

Use DISTINCT when a join may create duplicate properties
and the question asks for unique properties or owners.

========================================================
4. PROPERTY BUSINESS MEANINGS
========================================================

outstanding_arrears means:

pending
pending tax
arrears
unpaid
due amount
remaining old tax
थकबाकी
बाकी
बाकी कर
थकीत कर
पेंडिंग

current_outstanding means:

current pending
current due
current outstanding
current tax pending
चालू थकबाकी
सद्य थकबाकी
चालू बाकी

penalty_as_on_month means:

penalty
fine
late fee
दंड
दंड रक्कम
विलंब शुल्क
पेनल्टी

total means:

total tax
total demand
total amount
एकूण
एकूण कर
एकूण रक्कम
एकूण मागणी

nrv means:

rateable value
net rateable value
NRV

name_of_owner means:

owner
property owner
owner name
मालक
मालकाचे नाव
धारक

prop_no means:

property number
property id
मालमत्ता क्रमांक
प्रॉपर्टी नंबर

========================================================
5. PROPERTY USAGE VALUES
========================================================

Actual usage values may include:

Commercial
Commercial Unauthorized
Open Plot
Residential
Residential Unauthorized
Non Residential
Non Residential Unauthorized
Resi 1.5 Shasti
Resi 1.0 Shasti
Resi 2.0 Shasti
Industrial
Industrial Unauthorized
Mixed
Mobile Tower
Mobile Tower Unauthorized
Educational Granted
Educational Non Granted
Educational Unauthorized
Central Govt.50%
Non Residential Central Government
Open Plot Commercial
ussage not found

========================================================
6. USAGE MAPPINGS - ENGLISH
========================================================

commercial
shop
business property
commercial property
व्यावसायिक
कमर्शियल

=> usage ILIKE '%commercial%'

residential
house
home
residential property
निवासी
रेसिडेन्शियल
घर
घरगुती

=> usage ILIKE '%residential%'

industrial
factory
industry
औद्योगिक
इंडस्ट्रियल

=> usage ILIKE '%industrial%'

open plot
plot
empty plot
मोकळा प्लॉट
रिकामा प्लॉट
ओपन प्लॉट

=> usage ILIKE '%open plot%'

mobile tower
मोबाईल टॉवर

=> usage ILIKE '%mobile tower%'

educational
education property
school
college
शैक्षणिक

=> usage ILIKE '%educational%'

government
govt
सरकारी

=> usage ILIKE '%government%'
   OR usage ILIKE '%govt%'

unauthorized
अनधिकृत

=> usage ILIKE '%unauthorized%'

mixed
mixed usage
मिश्र
मिश्र वापर

=> usage ILIKE '%mixed%'

IMPORTANT:
Use ILIKE for case-insensitive matching.
Do not assume every usage value is an exact match.
Do not use only usage = 'Commercial' when the user
asks for all commercial categories.

========================================================
7. PAYMENT BUSINESS MEANINGS
========================================================

payment_management_report contains payment information.

payment
paid
payment amount
payment collection
कर भरला
भरणा
पेमेंट
कर जमा

payment_mode values:

Cash
Cheq
DD
Online

PAYMENT MODE MAPPINGS:

cash
cash payment
cash paid
रोख
कॅश
रोख पेमेंट
रोख भरणा

=> payment_mode = 'Cash'

cheque
check
cheq
चेक
चेक पेमेंट
धनादेश

=> payment_mode IN ('Cheq', 'Cheque')

dd
demand draft
डीडी
डिमांड ड्राफ्ट

=> payment_mode = 'DD'

online
upi
gpay
phonepe
paytm
internet banking
ऑनलाइन
युपीआय
फोनपे
गुगल पे
पेटीएम
ऑनलाईन भरणा

=> payment_mode = 'Online'

PAYMENT COLUMN MEANINGS:

paid_amount:
Actual amount paid.

total_paid:
Total paid amount.

total_balance:
Remaining balance.
Pending balance.
Balance amount.

total_outstanding:
Outstanding amount.
Due amount.
Demand amount.
Pending tax.

IMPORTANT:
Do not confuse paid_amount, total_paid,
total_balance and total_outstanding.

========================================================
8. FIXED YEAR LOGIC
========================================================

Previous Year = 2025
Current Year = 2026

These years are fixed business rules.

If user asks:

Paid last year
Previous year payment
मागच्या वर्षी पेमेंट
मागच्या वर्षी भरले

Use year 2025.

If user asks:

Paid this year
Current year payment
या वर्षी पेमेंट
या वर्षी भरले

Use year 2026.

If user asks:

"ज्यांनी मागच्या वर्षी पेमेंट केली पण या वर्षी बाकी आहे"

"Paid in 2025 but pending in 2026"

"Last year paid but this year not paid"

"2025 payment and 2026 pending"

"Previous year paid current year pending"

Generate:

SELECT DISTINCT
    pmr.prop_no,
    pmr.name_of_owner,
    pmr.property_address,
    pmr.mobile_no,
    pmr.usage,
    pmr.outstanding_arrears,
    pmr.current_outstanding,
    pmr.penalty_as_on_month,
    pmr.total
FROM property_management_report pmr
INNER JOIN payment_management_report pp
    ON pmr.prop_no = pp.property_no
WHERE
    EXTRACT(YEAR FROM pp.payment_date) = 2025
    AND NOT EXISTS (
        SELECT 1
        FROM payment_management_report pp2
        WHERE pp2.property_no = pmr.prop_no
          AND EXTRACT(YEAR FROM pp2.payment_date) = 2026
    )
    AND (
        COALESCE(pmr.outstanding_arrears, 0) > 0
        OR COALESCE(pmr.current_outstanding, 0) > 0
    )
ORDER BY pmr.name_of_owner
LIMIT 500;

IMPORTANT:

Do not use:

YEAR()
YEAR(GETDATE())
GETDATE()
DATEADD()
CURRENT_TIMESTAMP

Use:

EXTRACT(YEAR FROM payment_date)

Do not use dynamic year calculations.
Use the fixed years 2025 and 2026.

========================================================
9. DATE RANGE RULES
========================================================

If the user asks:

Paid between 2 March 2025 and 12 April 2026

Generate:

SELECT *
FROM payment_management_report
WHERE payment_date >= '2025-03-02'
  AND payment_date < '2026-04-13'
ORDER BY payment_date DESC
LIMIT 500;

Use ISO date format:

YYYY-MM-DD

For timestamp columns, use an exclusive next-day
boundary when the user specifies an inclusive end date.

Never generate:

BETWEEN '2 march 2025' AND '12 april 2026'

========================================================
10. BILL COLLECTOR BUSINESS MEANINGS
========================================================

collector_name:

collector
employee
staff
tax collector
bill collector
recovery officer
कलेक्टर
कर्मचारी
बिल कलेक्टर
कर वसुली कर्मचारी
वसुली कर्मचारी

tax_recovered:

recovered tax
recovery amount
collection amount
recovered amount

today_tax_recovered:

today collection
today recovery
today tax collection
आजची वसुली
आजची कर वसुली

total_tax_collection:

total collection
lifetime collection
overall collection
एकूण वसुली

total_tax_pending:

pending tax
remaining tax
uncollected tax
tax balance
एकूण बाकी कर

total_property:

properties assigned
assigned properties
एकूण मालमत्ता

today_reporting:

today reports
आजचे रिपोर्ट

total_reporting:

total reports
एकूण रिपोर्ट

today_complaint:

today complaints
आजच्या तक्रारी

total_complaint:

total complaints
एकूण तक्रारी

today_notice:

today notices
आजच्या नोटिसा

total_notice:

total notices
एकूण नोटिसा

login_time:

login time
first login
logged in time
लॉगिन वेळ

logout_time:

logout time
last logout
logged out time
लॉगआउट वेळ

========================================================
11. BILL COLLECTOR QUERY EXAMPLES
========================================================

Question:
top collectors

SQL:

SELECT
    collector_name,
    mobile_no,
    zone,
    total_tax_collection
FROM bill_collector_report
ORDER BY total_tax_collection DESC
LIMIT 500;

--------------------------------------------------------

Question:
today collection report

SQL:

SELECT
    collector_name,
    today_tax_recovered,
    today_reporting
FROM bill_collector_report
ORDER BY today_tax_recovered DESC
LIMIT 500;

--------------------------------------------------------

Question:
collectors with complaints

SQL:

SELECT
    collector_name,
    total_complaint
FROM bill_collector_report
WHERE total_complaint > 0
ORDER BY total_complaint DESC
LIMIT 500;

--------------------------------------------------------

Question:
collectors with highest pending tax

SQL:

SELECT
    collector_name,
    mobile_no,
    zone,
    total_tax_pending
FROM bill_collector_report
ORDER BY total_tax_pending DESC
LIMIT 500;

========================================================
12. LOGIN AND LOGOUT RULES
========================================================

login_time and logout_time may contain values such as:

10:10:20 AM
11:45:10 AM
12:20:00 PM
01:15:00 PM
02:30:00 PM
03:45:00 PM

They may also contain:

NULL
blank
-
--

The values NULL, blank, '-' and '--' mean
that the collector has not logged in or logged out.

IMPORTANT:
These columns may be stored as text because the original
data contains '-' and '--'.

Treat them as text when checking missing login/logout
values.

For comparing AM/PM time values, first verify that the
value matches a valid AM/PM time pattern.

Use PostgreSQL-compatible conversion:

CASE
    WHEN TRIM(login_time) ~
         '^[0-9]{{1,2}}:[0-9]{{2}}:[0-9]{{2}} [AP]M$'
    THEN TO_TIMESTAMP(
        TRIM(login_time),
        'HH12:MI:SS AM'
    )::time
END

Use the equivalent expression for logout_time.

Do not use TRY_CONVERT.
Do not use CONVERT(TIME,...).

--------------------------------------------------------
NOT LOGGED IN COLLECTORS
--------------------------------------------------------

Question:
not logged in collectors
collectors not logged in
who did not login
login pending collectors
collectors without login
लॉगिन नाही
हजर नाही

SQL:

SELECT *
FROM bill_collector_report
WHERE
    login_time IS NULL
    OR TRIM(login_time) = ''
    OR TRIM(login_time) IN ('-', '--')
ORDER BY collector_name
LIMIT 500;

--------------------------------------------------------
NOT LOGGED OUT COLLECTORS
--------------------------------------------------------

Question:
not logged out collectors
collectors without logout
who did not logout
लॉगआउट नाही

SQL:

SELECT *
FROM bill_collector_report
WHERE
    logout_time IS NULL
    OR TRIM(logout_time) = ''
    OR TRIM(logout_time) IN ('-', '--')
ORDER BY collector_name
LIMIT 500;

--------------------------------------------------------
LOGGED IN COLLECTORS
--------------------------------------------------------

Question:
logged in collectors
collectors who logged in
show active collectors

SQL:

SELECT *
FROM bill_collector_report
WHERE
    login_time IS NOT NULL
    AND TRIM(login_time) <> ''
    AND TRIM(login_time) NOT IN ('-', '--')
ORDER BY collector_name
LIMIT 500;

--------------------------------------------------------
LOGGED OUT COLLECTORS
--------------------------------------------------------

Question:
logged out collectors
collectors completed work

SQL:

SELECT *
FROM bill_collector_report
WHERE
    logout_time IS NOT NULL
    AND TRIM(logout_time) <> ''
    AND TRIM(logout_time) NOT IN ('-', '--')
ORDER BY collector_name
LIMIT 500;

--------------------------------------------------------
COUNT COLLECTORS NOT LOGGED IN
--------------------------------------------------------

Question:
count collectors not logged in

SQL:

SELECT COUNT(*) AS total_not_logged_in
FROM bill_collector_report
WHERE
    login_time IS NULL
    OR TRIM(login_time) = ''
    OR TRIM(login_time) IN ('-', '--');

--------------------------------------------------------
LATE LOGIN
--------------------------------------------------------

Question:
collectors logged in after 10 AM

SQL:

SELECT
    collector_name,
    mobile_no,
    zone,
    login_time
FROM bill_collector_report
WHERE
    CASE
        WHEN TRIM(login_time) ~
             '^[0-9]{{1,2}}:[0-9]{{2}}:[0-9]{{2}} [AP]M$'
        THEN TO_TIMESTAMP(
            TRIM(login_time),
            'HH12:MI:SS AM'
        )::time
    END >= TIME '10:00:00'
ORDER BY collector_name
LIMIT 500;

--------------------------------------------------------
LOGOUT AFTER 5 PM
--------------------------------------------------------

Question:
collectors logged out after 5 PM

SQL:

SELECT
    collector_name,
    mobile_no,
    zone,
    logout_time
FROM bill_collector_report
WHERE
    CASE
        WHEN TRIM(logout_time) ~
             '^[0-9]{{1,2}}:[0-9]{{2}}:[0-9]{{2}} [AP]M$'
        THEN TO_TIMESTAMP(
            TRIM(logout_time),
            'HH12:MI:SS AM'
        )::time
    END >= TIME '17:00:00'
ORDER BY collector_name
LIMIT 500;

========================================================
13. MOBILE NUMBER VALIDATION
========================================================

mobile available
mobile exists
valid mobile
owners with mobile
मोबाईल उपलब्ध
मोबाईल नंबर उपलब्ध

A valid mobile should:

- Not be NULL.
- Not be blank.
- Contain at least 10 characters after trimming.
- Not consist entirely of zeros.

Use:

mobile_no IS NOT NULL
AND TRIM(mobile_no) <> ''
AND LENGTH(TRIM(mobile_no)) >= 10
AND REPLACE(TRIM(mobile_no), '0', '') <> ''

--------------------------------------------------------

mobile not available
without mobile
invalid mobile
no mobile
mobile missing
मोबाईल नाही
मोबाईल नंबर नाही
फोन नंबर नाही
संपर्क क्रमांक नाही

Use:

mobile_no IS NULL
OR TRIM(mobile_no) = ''
OR REPLACE(TRIM(mobile_no), '0', '') = ''
OR LENGTH(TRIM(mobile_no)) < 10

--------------------------------------------------------

Question:
owners without mobile

SQL:

SELECT *
FROM property_management_report
WHERE
    mobile_no IS NULL
    OR TRIM(mobile_no) = ''
    OR REPLACE(TRIM(mobile_no), '0', '') = ''
    OR LENGTH(TRIM(mobile_no)) < 10
ORDER BY total DESC
LIMIT 500;

--------------------------------------------------------

Question:
count of owners without mobile

SQL:

SELECT COUNT(*) AS total_owners_without_mobile
FROM property_management_report
WHERE
    mobile_no IS NULL
    OR TRIM(mobile_no) = ''
    OR REPLACE(TRIM(mobile_no), '0', '') = ''
    OR LENGTH(TRIM(mobile_no)) < 10;

========================================================
14. PROPERTY QUERY EXAMPLES
========================================================

Question:
top 10 pending properties

SQL:

SELECT *
FROM property_management_report
ORDER BY outstanding_arrears DESC
LIMIT 10;

--------------------------------------------------------

Question:
commercial properties

SQL:

SELECT *
FROM property_management_report
WHERE usage ILIKE '%commercial%'
ORDER BY total DESC
LIMIT 500;

--------------------------------------------------------

Question:
pending above 50000

SQL:

SELECT *
FROM property_management_report
WHERE outstanding_arrears > 50000
ORDER BY outstanding_arrears DESC
LIMIT 500;

--------------------------------------------------------

Question:
highest penalty properties

SQL:

SELECT *
FROM property_management_report
ORDER BY penalty_as_on_month DESC
LIMIT 500;

--------------------------------------------------------

Question:
properties with current outstanding

SQL:

SELECT *
FROM property_management_report
WHERE current_outstanding > 0
ORDER BY current_outstanding DESC
LIMIT 500;

--------------------------------------------------------

Question:
residential properties

SQL:

SELECT *
FROM property_management_report
WHERE usage ILIKE '%residential%'
ORDER BY total DESC
LIMIT 500;

--------------------------------------------------------

Question:
open plots

SQL:

SELECT *
FROM property_management_report
WHERE usage ILIKE '%open plot%'
ORDER BY total DESC
LIMIT 500;

========================================================
15. PAYMENT QUERY EXAMPLES
========================================================

Question:
cash owners

SQL:

SELECT
    pmr.prop_no,
    pmr.name_of_owner,
    pmr.property_address,
    pmr.mobile_no,
    pmr.usage,
    pmr.outstanding_arrears,
    pmr.current_outstanding,
    pmr.penalty_as_on_month,
    pmr.total,
    pp.bill_no,
    pp.payment_date,
    pp.payment_mode,
    pp.paid_amount
FROM property_management_report pmr
INNER JOIN payment_management_report pp
    ON pmr.prop_no = pp.property_no
WHERE pp.payment_mode = 'Cash'
ORDER BY pp.payment_date DESC
LIMIT 500;

--------------------------------------------------------

Question:
online payments

SQL:

SELECT *
FROM payment_management_report
WHERE payment_mode = 'Online'
ORDER BY payment_date DESC
LIMIT 500;

--------------------------------------------------------

Question:
highest payments

SQL:

SELECT *
FROM payment_management_report
ORDER BY paid_amount DESC
LIMIT 500;

--------------------------------------------------------

Question:
total cash collection

SQL:

SELECT
    COALESCE(SUM(paid_amount), 0) AS total_cash_collection
FROM payment_management_report
WHERE payment_mode = 'Cash';

--------------------------------------------------------

Question:
payment mode wise collection

SQL:

SELECT
    payment_mode,
    COUNT(*) AS total_transactions,
    COALESCE(SUM(paid_amount), 0) AS total_collection
FROM payment_management_report
GROUP BY payment_mode
ORDER BY total_collection DESC;

========================================================
16. COMPLAINT MANAGEMENT
========================================================

TABLE:

complaint_management_report

Columns:

sr_no
date
complaint_caption
complaint_type
complaint_by_name_and_contact_no
property_id
owner_contact_number
complaint_status
action_taken_caption
action_taken_by

Business meanings:

complaint
complaints
grievance
issue
problem
तक्रार
तक्रारी

=> complaint_management_report

Complaint status values may include:

Pending
Approved
Rejected
Closed
Resolved
In Progress

Mappings:

pending complaints
=> complaint_status = 'Pending'

resolved complaints
=> complaint_status = 'Resolved'

approved complaints
=> complaint_status = 'Approved'

rejected complaints
=> complaint_status = 'Rejected'

closed complaints
=> complaint_status = 'Closed'

in progress complaints
=> complaint_status = 'In Progress'

--------------------------------------------------------

Question:
pending complaints

SQL:

SELECT *
FROM complaint_management_report
WHERE complaint_status = 'Pending'
ORDER BY date DESC
LIMIT 500;

--------------------------------------------------------

Question:
owners having complaints

SQL:

SELECT
    pmr.prop_no,
    pmr.name_of_owner,
    pmr.property_address,
    pmr.mobile_no,
    pmr.usage,
    pmr.outstanding_arrears,
    pmr.total,
    cmr.complaint_type,
    cmr.complaint_status,
    cmr.complaint_caption
FROM property_management_report pmr
INNER JOIN complaint_management_report cmr
    ON pmr.prop_no = cmr.property_id
ORDER BY cmr.date DESC
LIMIT 500;

--------------------------------------------------------

Question:
count pending complaints

SQL:

SELECT COUNT(*) AS total_pending_complaints
FROM complaint_management_report
WHERE complaint_status = 'Pending';

========================================================
17. NOTICE MANAGEMENT
========================================================

TABLE:

notice_management_report

Columns:

sr_no
property_id
owner_name
contact_number
property_address
pending_tax
notice_by
notice
notice_duration
notice_status

Business meanings:

notice
notices
tax notice
warning notice
demand notice
नोटीस
सूचना

Notice status mappings:

pending notice
=> notice_status = 'Pending'

approved notice
=> notice_status = 'Approved'

rejected notice
=> notice_status = 'Rejected'

closed notice
=> notice_status = 'Closed'

--------------------------------------------------------

Question:
pending notices

SQL:

SELECT *
FROM notice_management_report
WHERE notice_status = 'Pending'
ORDER BY pending_tax DESC
LIMIT 500;

--------------------------------------------------------

Question:
highest pending tax notices

SQL:

SELECT *
FROM notice_management_report
ORDER BY pending_tax DESC
LIMIT 500;

--------------------------------------------------------

Question:
owners having notices

SQL:

SELECT
    pmr.prop_no,
    pmr.name_of_owner,
    pmr.property_address,
    pmr.mobile_no,
    pmr.usage,
    pmr.outstanding_arrears,
    pmr.total,
    nmr.notice_status,
    nmr.pending_tax,
    nmr.notice_by
FROM property_management_report pmr
INNER JOIN notice_management_report nmr
    ON pmr.prop_no = nmr.property_id
ORDER BY nmr.pending_tax DESC
LIMIT 500;

========================================================
18. REPORTING MANAGEMENT
========================================================

TABLE:

reporting_management_report

Columns:

sr_no
date
report_type
report_caption
bill_collector_name
bill_collector_contact_no
property_id
owner_name
owner_contact_no

Business meanings:

report
field report
tax report
verification report
रिपोर्ट
अहवाल

Mappings:

tax recovered report
=> report_type = 'Tax Recovered'

address verification report
=> report_type = 'Address Verification'

--------------------------------------------------------

Question:
tax recovered reports

SQL:

SELECT *
FROM reporting_management_report
WHERE report_type = 'Tax Recovered'
ORDER BY date DESC
LIMIT 500;

--------------------------------------------------------

Question:
address verification reports

SQL:

SELECT *
FROM reporting_management_report
WHERE report_type = 'Address Verification'
ORDER BY date DESC
LIMIT 500;

--------------------------------------------------------

Question:
owners having reports

SQL:

SELECT
    pmr.prop_no,
    pmr.name_of_owner,
    pmr.property_address,
    pmr.mobile_no,
    pmr.usage,
    pmr.outstanding_arrears,
    pmr.total,
    rmr.report_type,
    rmr.report_caption,
    rmr.bill_collector_name
FROM property_management_report pmr
INNER JOIN reporting_management_report rmr
    ON pmr.prop_no = rmr.property_id
ORDER BY rmr.date DESC
LIMIT 500;

========================================================
19. MULTIPLE TABLE JOIN RULES
========================================================

If the question asks for properties with payments
and complaints, join all three tables.

If the question asks for properties with notices,
join property_management_report and notice_management_report.

If the question asks for properties with complaints,
join property_management_report and complaint_management_report.

If the question asks for properties with reports,
join property_management_report and reporting_management_report.

If the question asks for cash payments with complaints,
join:

property_management_report
payment_management_report
complaint_management_report

Example:

SELECT DISTINCT
    pmr.prop_no,
    pmr.name_of_owner,
    pmr.property_address,
    pmr.mobile_no,
    pmr.usage,
    pmr.outstanding_arrears,
    pmr.current_outstanding,
    pmr.total,
    pp.payment_mode,
    pp.paid_amount,
    cmr.complaint_type,
    cmr.complaint_status,
    cmr.complaint_caption
FROM property_management_report pmr
INNER JOIN payment_management_report pp
    ON pmr.prop_no = pp.property_no
INNER JOIN complaint_management_report cmr
    ON pmr.prop_no = cmr.property_id
WHERE pp.payment_mode = 'Cash'
LIMIT 500;

========================================================
20. IMPORTANT OUTPUT RULES
========================================================

Never return only one column for a normal listing query.

For property questions, prefer:

pmr.prop_no
pmr.name_of_owner
pmr.property_address
pmr.mobile_no
pmr.usage
pmr.outstanding_arrears
pmr.current_outstanding
pmr.penalty_as_on_month
pmr.total

For payment questions, additionally return:

pp.bill_no
pp.payment_date
pp.payment_mode
pp.paid_amount

For complaint questions, additionally return:

cmr.complaint_type
cmr.complaint_status
cmr.complaint_caption

For notice questions, additionally return:

nmr.notice_status
nmr.pending_tax
nmr.notice_by

For reporting questions, additionally return:

rmr.report_type
rmr.report_caption
rmr.bill_collector_name

For collector questions, return relevant collector details
such as:

collector_name
mobile_no
zone
total_tax_collection
total_tax_pending
today_tax_recovered
today_reporting
total_complaint
total_notice
login_time
logout_time

For count questions, use COUNT(*).

For total collection questions, use SUM(paid_amount)
or the appropriate collection column.

For average questions, use AVG().

For highest questions, use ORDER BY column DESC.

For lowest questions, use ORDER BY column ASC.

Use COALESCE() for NULL values in numeric calculations.

========================================================
21. POSTGRESQL SQL GENERATION RULES
========================================================

These rules are mandatory.

1. Generate PostgreSQL-compatible SQL only.

2. Use lowercase table names.

3. Use lowercase snake_case column names.

4. Never use TOP.
   Use LIMIT instead.

5. Never use WITH (NOLOCK).

6. Never use SQL Server-specific functions.

7. Never use:
   GETDATE()
   YEAR()
   DATEADD()
   TRY_CONVERT()
   LEN()
   LTRIM()
   RTRIM()
   ISNULL()

8. Use:
   EXTRACT(YEAR FROM date_column)
   LENGTH()
   TRIM()
   COALESCE()

9. Use ILIKE for case-insensitive text matching.

10. Use LIMIT 500 as the default result limit.

11. If the user specifies a number, respect that number.

12. Do not add LIMIT to count-only queries unless needed.

13. Use correct PostgreSQL JOIN syntax.

14. Use the actual six migrated PostgreSQL tables.

15. Do not invent table names or column names.

16. Do not generate INSERT.

17. Do not generate UPDATE.

18. Do not generate DELETE.

19. Do not generate DROP.

20. Do not generate ALTER.

21. Do not generate TRUNCATE.

22. Do not generate CREATE.

23. Do not generate EXEC.

24. Do not generate MERGE.

25. Generate SELECT queries only.

26. Do not generate multiple SQL statements.

27. Do not include explanations or Markdown.

28. Return only executable SQL.

29. Do not wrap the SQL in Markdown code fences.

30. Never use SQL Server table aliases or column names
    with the original capitalization.

========================================================
22. ROMAN MARATHI MAPPINGS
========================================================

jyni = who
kele = paid / done
bharle = paid amount
madhe = in
pexya jast = greater than
magchya varshi = previous year
ya varshi = current year
baki = pending
kar bharla = tax paid
dakhva = show
sankhya = count
kadhich nahi = never
saglyat jast = highest
saglyat kami = lowest

Examples:

"commercial property dakhva"
=> Show commercial properties.

"jyni magchya varshi kar bharla"
=> Owners who paid tax in 2025.

"50000 pexya jast baki"
=> Properties with outstanding arrears greater than 50000.

"cash madhe payment kele"
=> Cash payments.

"login nahi kele"
=> Collectors who have not logged in.

========================================================
23. FINAL SQL VALIDATION BEFORE RETURNING
========================================================

Before returning the SQL, verify every item:

- Is it valid PostgreSQL syntax?
- Are all table names correct?
- Are all column names correct?
- Are all table names lowercase?
- Are all column names lowercase snake_case?
- Is TOP absent?
- Is WITH (NOLOCK) absent?
- Are all SQL Server functions removed?
- Are joins using the correct relationships?
- Are the correct payment modes used?
- Are fixed years 2025 and 2026 respected?
- Is the requested limit respected?
- Is it a SELECT-only query?
- Is there only one SQL statement?

If any rule is violated, correct the SQL before returning it.

========================================================
USER QUESTION
========================================================

{question}

Generate the PostgreSQL SQL query now.

Return SQL only.
"""

    response = model.generate_content(prompt)

    sql = response.text.strip()

    # Remove Markdown code fences if Gemini returns them
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)

    return sql.strip()


# =====================================================
# SQL VALIDATION
# =====================================================

def validate_sql(sql):

    sql = sql.strip()

    sql_upper = sql.upper()

    # Allow SELECT queries and SELECT-based CTEs
    if not (
        sql_upper.startswith("SELECT")
        or sql_upper.startswith("WITH")
    ):
        return False

    blocked = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "EXEC",
        "MERGE",
        "CREATE",
        "GRANT",
        "REVOKE"
    ]

    for keyword in blocked:

        if re.search(
            rf"\b{keyword}\b",
            sql_upper
        ):
            return False

    # Reject multiple SQL statements
    cleaned_sql = sql.rstrip(";").strip()

    if ";" in cleaned_sql:
        return False

    return True


# =====================================================
# EXECUTE SQL
# =====================================================


# =====================================================
# EXECUTE SQL - NEON POSTGRESQL
# =====================================================

def execute_sql(sql):

    conn = None

    try:

        if not validate_sql(sql):
            return {
                "success": False,
                "error": "Unsafe SQL detected"
            }

        # Connect to Neon PostgreSQL
        conn = get_connection()

        with conn.cursor(cursor_factory=RealDictCursor) as cursor:

            cursor.execute(sql)

            rows = cursor.fetchall()

            # Convert PostgreSQL rows into normal dictionaries
            data = [dict(row) for row in rows]

        df = pd.DataFrame(data)

        # Format date columns
        for col in df.columns:

            col_lower = str(col).lower()

            if "date" in col_lower:

                try:
                    df[col] = pd.to_datetime(
                        df[col],
                        errors="coerce"
                    ).dt.strftime("%d-%m-%Y")

                except Exception:
                    pass

        # Replace missing values
        df = df.astype(object).where(pd.notna(df), "")

        # Convert to JSON-compatible Python records
        rows = json.loads(
            df.to_json(
                orient="records",
                force_ascii=False
            )
        )

        return {
            "success": True,
            "count": len(rows),
            "rows": rows
        }

    except Exception as e:

        if conn:
            conn.rollback()

        return {
            "success": False,
            "error": str(e),
            "sql": sql
        }

    finally:

        if conn:
            conn.close()


# =====================================================
# HOME PAGE
# =====================================================


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )
# =====================================================
# HEALTH
# =====================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "message": "Gemini Tax Dashboard is running"
    }

# =====================================================
# SCHEMA
# =====================================================

@app.get("/schema")
def schema():

    return {

        "database": "TaxDB",

        "table": "Property_Management_Report",

        "columns": [

            "Sr_No",
            "Prop_No",
            "Name_Of_Owner",
            "Property_Address",
            "Mobile_No",
            "Usage",
            "NRV",
            "Outstanding_Arrears",
            "Current_Outstanding",
            "Penalty_as_on_Month",
            "Total"

        ]
    }

# # =====================================================
# # ASK AI
# # =====================================================

@app.post("/generate-sql")
def generate_sql_api(request: QuestionRequest):

    try:

        sql = generate_sql(request.question)

        return {
            "success": True,
            "question": request.question,
            "generated_sql": sql
        }

    except Exception as e:

        print("GENERATE SQL ERROR:", str(e))

        return {
            "success": False,
            "error": str(e)
        }

# =====================================================
# EXECUTE QUERY
# =====================================================

@app.post("/execute")
def execute_api(request: QuestionRequest):

    try:

        sql = generate_sql(request.question)

        result = execute_sql(sql)

        return {
            "success": result.get("success", False),
            "question": request.question,
            "generated_sql": sql,
            "result": result
        }

    except Exception as e:

        print("EXECUTE ERROR:", str(e))

        return {
            "success": False,
            "error": str(e)
        }


# =====================================================
# ASK AI
# =====================================================

@app.post("/ask")
def ask_ai(request: QuestionRequest):

    try:

        print("\n" + "=" * 80)
        print("QUESTION:")
        print(request.question)

        sql = generate_sql(request.question)

        print("\nGENERATED SQL:")
        print(sql)

        result = execute_sql(sql)

        print("\nQUERY STATUS:")
        print(result.get("success"))

        if not result.get("success"):

            return {
                "success": False,
                "question": request.question,
                "generated_sql": sql,
                "error": result.get("error")
            }

        return {
            "success": True,
            "question": request.question,
            "generated_sql": sql,
            "count": result.get("count", 0),
            "rows": result.get("rows", [])
        }

    except Exception as e:

        print("\nASK AI ERROR:")
        print(str(e))

        return {
            "success": False,
            "error": str(e)
        }


# =====================================================
# TEST API
# =====================================================

@app.get("/test")
def test():

    return {
        "success": True,
        "message": "API Running Successfully"
    }