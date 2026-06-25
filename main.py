import os
import json
import pyodbc
import pandas as pd
import numpy as np

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import google.generativeai as genai

# =====================================================
# GEMINI CONFIG
# =====================================================

GEMINI_API_KEY = "YOUR_ACTUAL_API_KEY"
genai.configure(api_key=GEMINI_API_KEY)
 

model = genai.GenerativeModel("gemini-2.5-flash")

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

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=TaxDB;"
    "Trusted_Connection=yes;"
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
You are an expert SQL Server query generator.

DATABASE : TaxDB

TABLE 1 : Property_Management_Report

Columns:

Sr_No
Prop_No
Name_Of_Owner
Property_Address
Mobile_No
Usage
NRV
Outstanding_Arrears
Current_Outstanding
Penalty_as_on_Month
Total

--------------------------------------------------


TABLE 2 : Property_Payments

Columns:

Sr_No
Bill_No
Payment_Date
Property_No
Zone
Address
Payment_Mode
Paid_Amount
Total_Outstanding
Total_Paid
Total_Balance

--------------------------------------------------

Bill_Collector_Report

Columns:

Sr_No
Collector_Name
Mobile_No
Email
Zone
Block
SubBlock
Total_Property
Tax_Recovered
Today_Tax_Recovered
Total_Tax_Collection
Total_Tax_Pending
Today_Reporting
Total_Reporting
Today_Complaint
Total_Complaint
Today_Notice
Total_Notice
Login_Time
Logout_Time

--------------------------------------------------

RELATIONSHIP

Property_Management_Report.Prop_No
=
Property_Payments.Property_No

-----------------------------------------------------

COLUMN MEANINGS

Outstanding_Arrears =
Pending
Pending Tax
Arrears
Unpaid
Due Amount

Current_Outstanding =
Current Pending
Current Due

Penalty_as_on_Month =
Penalty
Fine
Late Fee

Total =
Total Tax
Total Demand

-----------------------------------------------------

USAGE VALUES

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

=================================================
USAGE MAPPINGS (ENGLISH)
=================================================

commercial
shop
business property
commercial property

=> Usage LIKE 'Commercial'

residential
house
home

=> Usage LIKE 'Residential'

industrial
factory
industry

=> Usage LIKE 'industrial'

open plot
plot

=> Usage LIKE 'Open Plot'

mobile tower

=> Usage LIKE 'Mobile Tower'

educational

=> Usage LIKE 'Educational'

government

=> Usage LIKE 'Government'

unauthorized

=> Usage LIKE 'Unauthorized'

=================================================
USAGE MAPPINGS (MARATHI)
=================================================

कमर्शियल
व्यावसायिक

=> Usage LIKE 'Commercial'

रेसिडेन्शियल
निवासी
घर

=> Usage LIKE 'Residential'

इंडस्ट्रियल
कारखाना

=> Usage LIKE 'industrial'

ओपन प्लॉट
मोकळा प्लॉट
प्लॉट

=> Usage LIKE 'Open Plot'

मोबाईल टॉवर

=> Usage LIKE 'Mobile Tower'

शैक्षणिक

=> Usage LIKE 'Educational'

सरकारी

=> Usage LIKE 'Government'
OR Usage LIKE 'Govt'

अनधिकृत

=> Usage LIKE 'Unauthorized'
-----------------------------------------------------

Total_Balance means:

Balance
Remaining Amount
Pending Balance

Total_Outstanding means:

Outstanding
Due
Demand
Pending Tax

-----------------------------------------------------

Payment_Mode means:

Cash
Cheq
DD
Online


=================================================
PAYMENT MODE MAPPINGS
=================================================

cash
cash payment
cash paid

=> Payment_Mode='Cash'

cheque
check
cheq

=> Payment_Mode='Cheq'

dd
demand draft

=> Payment_Mode='DD'

online
upi
gpay
phonepe
paytm
internet banking

=> Payment_Mode='Online'

=================================================
PAYMENT MODE MAPPINGS (MARATHI)
=================================================

रोख
कॅश

=> Payment_Mode='Cash'

चेक

=> Payment_Mode='Cheq'

डीडी
डिमांड ड्राफ्ट

=> Payment_Mode='DD'

ऑनलाइन
युपीआय
फोनपे
गुगल पे
पेटीएम

=> Payment_Mode='Online'
=================================================

=================================================
YEAR LOGIC (FIXED YEARS)
=================================================

Current Year = 2026
Previous Year = 2025

If user asks:

"ज्यांनी मागच्या वर्षी पेमेंट केली पण या वर्षी बाकी आहे"

"Paid in 2025 but pending in 2026"

"Last year paid but this year not paid"

"2025 payment and 2026 pending"

"Previous year paid current year pending"

Generate:

SELECT DISTINCT TOP 500
    PMR.Prop_No,
    PMR.Name_Of_Owner,
    PMR.Property_Address,
    PMR.Mobile_No,
    PMR.Usage,
    PMR.Outstanding_Arrears,
    PMR.Current_Outstanding,
    PMR.Penalty_as_on_Month,
    PMR.Total,
    PP.Payment_Date,
    PP.Payment_Mode,
    PP.Paid_Amount
FROM Property_Management_Report PMR WITH (NOLOCK)
INNER JOIN Property_Payments PP WITH (NOLOCK)
    ON PMR.Prop_No = PP.Property_No
WHERE
    YEAR(PP.Payment_Date) = 2025
    AND PMR.Prop_No NOT IN
    (
        SELECT DISTINCT Property_No
        FROM Property_Payments WITH (NOLOCK)
        WHERE YEAR(Payment_Date) = 2026
    )
    AND
    (
        PMR.Outstanding_Arrears > 0
        OR PMR.Current_Outstanding > 0
    )
ORDER BY PMR.Name_Of_Owner

IMPORTANT:

DO NOT USE:

YEAR(GETDATE())
GETDATE()
DATEADD()
CURRENT_TIMESTAMP

Use fixed years:

2025 = Previous Year
2026 = Current Year

=================================================

If user asks:

paid between 2 march 2025 and 12 april 2026

Generate:

WHERE Payment_Date >= '2025-03-02'
AND Payment_Date <= '2026-04-12'

Use YYYY-MM-DD format.

Never generate:

BETWEEN '2 march 2025' AND '12 april 2026'

=================================================
BILL COLLECTOR BUSINESS MEANINGS
=================================================

Collector_Name

collector
employee
staff
tax collector
bill collector
recovery officer

-----------------------------------------

Tax_Recovered

recovered tax
collection amount
recovered amount

-----------------------------------------

Today_Tax_Recovered

today collection
today recovery
today tax collection

-----------------------------------------

Total_Tax_Collection

total collection
lifetime collection
overall collection

-----------------------------------------

Total_Tax_Pending

pending tax
remaining tax
uncollected tax
tax balance

-----------------------------------------

Total_Property

properties assigned
assigned properties

-----------------------------------------

Today_Reporting

today reports

-----------------------------------------

Total_Reporting

total reports

-----------------------------------------

Today_Complaint

today complaints

-----------------------------------------

Total_Complaint

total complaints

-----------------------------------------

Today_Notice

today notices

-----------------------------------------

Total_Notice

total notices

-----------------------------------------

Login_Time

login time
first login

-----------------------------------------

Logout_Time

logout time
last logout
-----------------------------------------

If question contains columns from both tables,
use INNER JOIN.

Property_Management_Report PMR
INNER JOIN Property_Payments PP
ON PMR.Prop_No = PP.Property_No

RULES

1. Return SQL only.
2. SQL Server syntax.
3. Use TOP.
4. Use WITH (NOLOCK).
5. Default .
6. Only SELECT query.
7. Never generate DELETE.
8. Never generate UPDATE.
9. Never generate INSERT.
10. Never generate DROP.
11. Never generate ALTER.

-----------------------------------------------------

Examples

Question:
top 10 pending properties

SQL:

SELECT TOP 10 *
FROM Property_Management_Report WITH (NOLOCK)
ORDER BY Outstanding_Arrears DESC

-----------------------------------------------------

=================================================
TABLE SELECTION RULES
=================================================

Property owner information
=> Property_Management_Report

Payment information
=> Property_Payments

Collector information
=> Bill_Collector_Report

If question contains:

collector
employee
staff
recovery officer
collection report
today collection
today recovery
complaints
notices
login
logout

Then use Bill_Collector_Report.

If question contains:

payment
paid amount
cash payment
online payment
bill

Then use Property_Payments.

If question contains:

owner
property
pending tax
arrears
penalty
mobile number
commercial property
residential property

Then use Property_Management_Report.

-----------------------------------------------------

Question:
commercial properties


SQL:

SELECT  TOP 500 *
FROM Property_Management_Report WITH (NOLOCK)
WHERE Usage='Commercial'
ORDER BY Total DESC

-----------------------------------------------------

Question:
pending above 50000

SQL:

SELECT TOP 500 *
FROM Property_Management_Report WITH (NOLOCK)
WHERE Outstanding_Arrears > 50000
ORDER BY Outstanding_Arrears DESC

--------------------------------------------------

mobile available
mobile exists
valid mobile
owners with mobile

=>

Mobile_No IS NOT NULL
AND LTRIM(RTRIM(Mobile_No)) <> ''
AND LEN(LTRIM(RTRIM(Mobile_No))) >= 10
AND REPLACE(LTRIM(RTRIM(Mobile_No)), '0', '') <> ''

------------------------------------------------

mobile not available
without mobile
invalid mobile
no mobile
mobile missing

=>

Mobile_No IS NULL
OR LTRIM(RTRIM(Mobile_No)) = ''
OR REPLACE(LTRIM(RTRIM(Mobile_No)), '0', '') = ''
OR LEN(LTRIM(RTRIM(Mobile_No))) < 10

-----------------------------------------------------

Question:
owners without mobile

SQL:

SELECT TOP 500 *
FROM Property_Management_Report WITH (NOLOCK)
WHERE
    Mobile_No IS NULL
    OR LTRIM(RTRIM(Mobile_No)) = ''
    OR REPLACE(LTRIM(RTRIM(Mobile_No)), '0', '') = ''
    OR LEN(LTRIM(RTRIM(Mobile_No))) < 10
ORDER BY Total DESC
-----------------------------------------------------


Question:
count of owners without mobile

SQL:

SELECT COUNT(*) AS Total_Owners_Without_Mobile
FROM Property_Management_Report WITH (NOLOCK)
WHERE
    Mobile_No IS NULL
    OR LTRIM(RTRIM(Mobile_No)) = ''
    OR REPLACE(LTRIM(RTRIM(Mobile_No)), '0', '') = ''
    OR LEN(LTRIM(RTRIM(Mobile_No))) < 10
-----------------------------------------------------

Question:
cash owners

SQL:

SELECT TOP 500
PMR.Prop_No,
PMR.Name_Of_Owner,
PMR.Property_Address,
PMR.Mobile_No,
PMR.Usage,
PMR.Outstanding_Arrears,
PMR.Current_Outstanding,
PMR.Penalty_as_on_Month,
PMR.Total,
PP.Payment_Mode,
PP.Payment_Date
FROM Property_Management_Report PMR WITH (NOLOCK)
INNER JOIN Property_Payments PP WITH (NOLOCK)
ON PMR.Prop_No = PP.Property_No
WHERE PP.Payment_Mode='Cash'
ORDER BY PMR.Name_Of_Owner

-----------------------------------------------------

Question:
commercial properties


SQL:

SELECT TOP 500 *
FROM Property_Management_Report WITH (NOLOCK)
WHERE Usage='Commercial'
ORDER BY Total DESC

-----------------------------------------------------

Question:
cash owners

SQL:

SELECT TOP 500
PMR.Prop_No,
PMR.Name_Of_Owner,
PMR.Property_Address,
PMR.Mobile_No,
PMR.Usage,
PMR.Outstanding_Arrears,
PMR.Current_Outstanding,
PMR.Penalty_as_on_Month,
PMR.Total,
PP.Bill_No,
PP.Payment_Date,
PP.Payment_Mode,
PP.Paid_Amount
FROM Property_Management_Report PMR WITH (NOLOCK)
INNER JOIN Property_Payments PP WITH (NOLOCK)
ON PMR.Prop_No = PP.Property_No
WHERE PP.Payment_Mode='Cash'
ORDER BY PP.Payment_Date DESC


-----------------------------------------------------
Question:
top collectors

SQL:

SELECT 
Collector_Name,
Mobile_No,
Zone,
Total_Tax_Collection
FROM Bill_Collector_Report WITH (NOLOCK)
ORDER BY Total_Tax_Collection DESC

-------------------------------------------------

Question:
today collection report

SQL:

SELECT
Collector_Name,
Today_Tax_Recovered,
Today_Reporting
FROM Bill_Collector_Report WITH (NOLOCK)
ORDER BY Today_Tax_Recovered DESC
-----------------------------------------------------

Question:
collectors with complaints

SQL:

SELECT
Collector_Name,
Total_Complaint
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE Total_Complaint > 0
ORDER BY Total_Complaint DESC


=================================================
LOGIN / LOGOUT RULES
=================================================

Login_Time and Logout_Time are SQL TIME columns.

Do NOT use TRY_CONVERT.

Do NOT use CONVERT(TIME,...).

Use direct comparison.

Examples:

logged in after 10 AM

SELECT
Collector_Name,
Mobile_No,
Zone,
Login_Time
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE Login_Time >= '10:00:00'
ORDER BY Login_Time

-------------------------------------------------

logged in before 9 AM

SELECT
Collector_Name,
Mobile_No,
Zone,
Login_Time
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE Login_Time < '09:00:00'
ORDER BY Login_Time

-------------------------------------------------

logout after 5 PM

SELECT
Collector_Name,
Mobile_No,
Zone,
Logout_Time
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE Logout_Time >= '17:00:00'
ORDER BY Logout_Time

-------------------------------------------------

late login

SELECT
Collector_Name,
Mobile_No,
Zone,
Login_Time
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE Login_Time > '10:00:00'
ORDER BY Login_Time


=================================================
BILL COLLECTOR LOGIN / LOGOUT LOGIC
=================================================

Table:
Bill_Collector_Report

Columns:

Collector_Name
Mobile_No
Email
Zone
Block_SubBlock
Total_Property
Tax_Recovered
Today_Tax_Recovered
Total_Tax_Collection
Total_Tax_Pending
Today_Reporting
Total_Reporting
Today_Complaint
Total_Complaint
Today_Notice
Total_Notice
Login_Time
Logout_Time

=================================================
LOGIN STATUS RULES
=================================================

Login_Time values may contain:

--
-
blank
NULL

These mean:

NOT LOGGED IN

If user asks:

not logged in collectors
collectors not logged in
who did not login
login pending collectors
collectors without login

Generate:

SELECT *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Login_Time IS NULL
OR LTRIM(RTRIM(Login_Time)) = ''
OR Login_Time IN ('-','--')

=================================================
LOGOUT STATUS RULES
=================================================

Logout_Time values may contain:

--
-
blank
NULL

These mean:

NOT LOGGED OUT

If user asks:

not logged out collectors
collectors without logout
who did not logout

Generate:

SELECT *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Logout_Time IS NULL
OR LTRIM(RTRIM(Logout_Time)) = ''
OR Logout_Time IN ('-','--')

=================================================
LOGGED IN COLLECTORS
=================================================



If user asks:

logged in collectors
collectors who logged in today
show active collectors

Generate:

SELECT *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Login_Time IS NOT NULL
AND LTRIM(RTRIM(Login_Time)) <> ''
AND Login_Time NOT IN ('-','--')

=================================================
LOGGED OUT COLLECTORS
=================================================
=================================================
LOGIN / LOGOUT TIME RULES
=================================================

Login_Time and Logout_Time are stored in AM/PM format.

Examples:

10:10:20 AM
11:45:10 AM
12:20:00 PM
01:15:00 PM
02:30:00 PM
03:45:00 PM

Always use:

TRY_CONVERT(TIME, Login_Time)

TRY_CONVERT(TIME, Logout_Time)

-------------------------------------------------

after 10 am

WHERE TRY_CONVERT(TIME, Login_Time) >= '10:00:00 AM'

-------------------------------------------------

after 11 am

WHERE TRY_CONVERT(TIME, Login_Time) >= '11:00:00 AM'

-------------------------------------------------

after 12 pm

WHERE TRY_CONVERT(TIME, Logout_Time) >= '12:00:00 PM'

-------------------------------------------------

after 1 pm

WHERE TRY_CONVERT(TIME, Logout_Time) >= '01:00:00 PM'

-------------------------------------------------

after 2 pm

WHERE TRY_CONVERT(TIME, Logout_Time) >= '02:00:00 PM'

-------------------------------------------------

after 3 pm

WHERE TRY_CONVERT(TIME, Logout_Time) >= '03:00:00 PM'

-------------------------------------------------

after 4 pm

WHERE TRY_CONVERT(TIME, Logout_Time) >= '04:00:00 PM'

-------------------------------------------------

after 5 pm

WHERE TRY_CONVERT(TIME, Logout_Time) >= '05:00:00 PM'

-------------------------------------------------

before 10 am

WHERE TRY_CONVERT(TIME, Login_Time) < '10:00:00 AM'

-------------------------------------------------

before 12 pm

WHERE TRY_CONVERT(TIME, Logout_Time) < '12:00:00 PM'

If user asks:

logged out collectors
collectors completed work

Generate:

SELECT TOP 500 *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Logout_Time IS NOT NULL
AND LTRIM(RTRIM(Logout_Time)) <> ''
AND Logout_Time NOT IN ('-','--')

=================================================
EXAMPLES
=================================================

Question:
show collectors not logged in

SQL:

SELECT TOP 500 *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Login_Time IS NULL
OR LTRIM(RTRIM(Login_Time))=''
OR Login_Time IN ('-','--')
ORDER BY Collector_Name

-------------------------------------------------

Question:
show collectors not logged out

SQL:

SELECT TOP 500 *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Logout_Time IS NULL
OR LTRIM(RTRIM(Logout_Time))=''
OR Logout_Time IN ('-','--')
ORDER BY Collector_Name

-------------------------------------------------

Question:
show active collectors

SQL:

SELECT *
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Login_Time IS NOT NULL
AND LTRIM(RTRIM(Login_Time)) <> ''
AND Login_Time NOT IN ('-','--')
ORDER BY Collector_Name

-------------------------------------------------

Question:
count collectors not logged in

SQL:

SELECT COUNT(*) AS Total_Not_Logged_In
FROM Bill_Collector_Report WITH (NOLOCK)
WHERE
Login_Time IS NULL
OR LTRIM(RTRIM(Login_Time))=''
OR Login_Time IN ('-','--')

-------------------------------------------------


=================================================
TABLE : Complaint_Management_Report
=================================================

Columns:

Sr_No
Date
Complaint_Caption
Complaint_Type
Complaint_by_name_and_contact_no
Property_ID
Owner_contact_number
Complaint_status
Action_Taken_Caption
Action_taken_by

BUSINESS MEANING

Complaint_status values:

Pending
Approved
Rejected
Closed
Resolved
In Progress

SYNONYMS

complaint
grievance
issue
problem

=> Complaint_Management_Report

pending complaints

=> Complaint_status='Pending'

resolved complaints

=> Complaint_status='Resolved'

approved complaints

=> Complaint_status='Approved'

=================================================
TABLE : Notice_Management_Report
=================================================  

Columns:

Sr_No
Property_ID
Owner_Name
Contact_Number
Property_Address
Pending_Tax
Notice_By
Notice
Notice_Duration
Notice_Status

BUSINESS MEANING

notice
tax notice
warning notice
demand notice

=> Notice_Management_Report

pending notice

=> Notice_Status='Pending'

approved notice

=> Notice_Status='Approved'

highest pending tax notice

=> ORDER BY Pending_Tax DESC

=================================================
TABLE : Reporting_Management_Report
=================================================

Columns:

Sr_No
Date
Report_Type
Report_Caption
Bill_Collector_Name
Bill_Collector_Contact_No
Property_ID
Owner_Name
Owner_Contact_No

BUSINESS MEANING

report
field report
tax report
verification report

=> Reporting_Management_Report

tax recovered report

=> Report_Type='Tax Recovered'

address verification report

=> Report_Type='Address Verification'

=================================================
RELATIONSHIPS
=================================================

Property_Management_Report.Prop_No
=
Property_Payments.Property_No

Property_Management_Report.Prop_No
=
Complaint_Management_Report.Property_ID

Property_Management_Report.Prop_No
=
Notice_Management_Report.Property_ID

Property_Management_Report.Prop_No
=
Reporting_Management_Report.Property_ID

=================================================
JOIN RULES
=================================================

If user asks:

owners having complaints

JOIN Property_Management_Report
and Complaint_Management_Report

If user asks:

owners having notices

JOIN Property_Management_Report
and Notice_Management_Report

If user asks:

owners having reports

JOIN Property_Management_Report
and Reporting_Management_Report

If user asks:

paid by cash and having complaint

JOIN

Property_Management_Report
Property_Payments
Complaint_Management_Report

=================================================
IMPORTANT OUTPUT RULE
=================================================

Never return only one column.

Always return meaningful columns.

For Property questions return:

PMR.Prop_No,
PMR.Name_Of_Owner,
PMR.Property_Address,
PMR.Mobile_No,
PMR.Usage,
PMR.Outstanding_Arrears,
PMR.Current_Outstanding,
PMR.Penalty_as_on_Month,
PMR.Total

For Payment questions additionally return:


PP.Payment_Date,
PP.Payment_Mode,
PP.Paid_Amount

For Complaint questions additionally return:

CMR.Complaint_Type,
CMR.Complaint_status,
CMR.Complaint_Caption

For Notice questions additionally return:

NMR.Notice_Status,
NMR.Pending_Tax,
NMR.Notice_By

For Reporting questions additionally return:

RMR.Report_Type,
RMR.Report_Caption,
RMR.Bill_Collector_Name
User Question:

{question}

Generate SQL:


=================================================
MARATHI + ENGLISH BUSINESS DICTIONARY
=================================================

PROPERTY
-------------------------------------------------

property
properties
मालमत्ता
मालमत्ता क्रमांक
प्रॉपर्टी

=> Property

-------------------------------------------------

OWNER
-------------------------------------------------

owner
owners
मालक
मालकाचे नाव
धारक

=> Name_Of_Owner

-------------------------------------------------

MOBILE
-------------------------------------------------

mobile
mobile number
मोबाईल
मोबाईल नंबर
फोन नंबर
संपर्क क्रमांक

=> Mobile_No

-------------------------------------------------

PENDING
-------------------------------------------------

pending
tax pending
arrears
due amount
unpaid
बाकी
थकबाकी
पेंडिंग
पेंडिंग कर
बाकी कर
थकीत कर

=> Outstanding_Arrears

-------------------------------------------------

CURRENT PENDING
-------------------------------------------------

current pending
current due
current outstanding
चालू थकबाकी
सद्य थकबाकी
चालू बाकी

=> Current_Outstanding

-------------------------------------------------

PENALTY
-------------------------------------------------

penalty
fine
late fee
दंड
दंड रक्कम
विलंब शुल्क
पेनल्टी

=> Penalty_as_on_Month

-------------------------------------------------

TOTAL
-------------------------------------------------

total
total tax
total demand
total amount
एकूण
एकूण कर
एकूण रक्कम
एकूण मागणी

=> Total

-------------------------------------------------

COMMERCIAL
-------------------------------------------------

commercial
commercial property
व्यावसायिक
कमर्शियल
व्यापारी

=> Usage='Commercial'

-------------------------------------------------

RESIDENTIAL
-------------------------------------------------

residential
निवासी
रेसिडेन्शियल
घरगुती

=> Usage='Residential'

-------------------------------------------------

INDUSTRIAL
-------------------------------------------------

industrial
इंडस्ट्रियल
औद्योगिक

=> Usage='Industrial'

-------------------------------------------------

OPEN PLOT
-------------------------------------------------

open plot
मोकळा प्लॉट
रिकामा प्लॉट

=> Usage='Open Plot'

-------------------------------------------------

MIXED
-------------------------------------------------

mixed
mixed usage
मिश्र
मिश्र वापर

=> Usage='Mixed'

-------------------------------------------------

MOBILE AVAILABLE
-------------------------------------------------

mobile available
मोबाईल उपलब्ध
मोबाईल नंबर उपलब्ध

=> Valid Mobile_No

-------------------------------------------------

MOBILE NOT AVAILABLE
-------------------------------------------------

mobile not available
without mobile
no mobile
मोबाईल नाही
मोबाईल नंबर नाही
फोन नंबर नाही
संपर्क क्रमांक नाही

=> Mobile_No IS NULL
OR Mobile_No=''
OR Mobile_No='0'

=================================================
PAYMENT TABLE
=================================================

payment
paid
payment amount
कर भरला
भरणा
पेमेंट
कर जमा

=> Property_Payments

cash payment
रोख पेमेंट
रोख भरणा

=> Payment_Mode='Cash'

online payment
ऑनलाईन पेमेंट
ऑनलाईन भरणा

=> Payment_Mode='Online'

cheque payment
चेक पेमेंट
धनादेश

=> Payment_Mode='Cheque'

=================================================
BILL COLLECTOR REPORT
=================================================

bill collector
bill collectors
बिल कलेक्टर
कर वसुली कर्मचारी
वसुली कर्मचारी

=> Bill_Collector_Report

collector name
कलेक्टर नाव
कर्मचारी नाव

=> Collector_Name

zone
झोन
विभाग

=> Zone

login
logged in
लॉगिन
हजर
उपस्थित

=> Login_Time

logout
logged out
लॉगआउट

=> Logout_Time

not login
not logged in
लॉगिन नाही
हजर नाही

=> Login_Time IN ('','-','--')

not logout
logout नाही
लॉगआउट नाही

=> Logout_Time IN ('','-','--')

=================================================
COMPLAINTS
=================================================

complaint
complaints
तक्रार
तक्रारी

=> Complaint

notice
notices
नोटीस
सूचना

=> Notice

reporting
report
रिपोर्ट
अहवाल

=> Reporting

=================================================

ROMAN MARATHI MAPPINGS

jyni = who
kele = paid/done
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
"""

    response = model.generate_content(prompt)

    sql = response.text.strip()

    sql = sql.replace("```sql", "")
    sql = sql.replace("```", "")
    sql = sql.strip()

    return sql

# =====================================================
# SQL VALIDATION
# =====================================================

def validate_sql(sql):

    blocked = [
        "DELETE",
        "UPDATE",
        "INSERT",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "EXEC",
        "MERGE",
        "CREATE"
    ]

    sql_upper = sql.upper()

    for keyword in blocked:

        if keyword in sql_upper:
            return False

    return True

# =====================================================
# EXECUTE SQL
# =====================================================

def execute_sql(sql):

    if not validate_sql(sql):

        return {
            "success": False,
            "error": "Unsafe SQL detected"
        }

    conn = pyodbc.connect(conn_str)

    try:

        df = pd.read_sql(sql, conn)

        # Convert date columns
        for col in df.columns:

            col_lower = str(col).lower()

            if (
                "date" in col_lower
                or "duration" in col_lower
            ):

                try:

                    df[col] = pd.to_datetime(
                        df[col],
                        errors="coerce"
                    ).dt.strftime("%d-%m-%Y")

                except:
                    pass

        # Replace NaN / NaT with empty string
        df = df.fillna("")

        # Convert numpy values to normal Python values
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

        return {
            "success": False,
            "error": str(e),
            "sql": sql
        }

    finally:

        conn.close()

# =====================================================
# HOME PAGE
# =====================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request
        }
    )

# =====================================================
# HEALTH
# =====================================================

@app.get("/health")
def health():

    return {
        "status": "running"
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
# # GENERATE SQL
# # =====================================================

# @app.post("/generate-sql")
# def generate_sql_api(request: QuestionRequest):

#     sql = generate_sql(request.question)

#     return {

#         "question": request.question,
#         "generated_sql": sql

#     }

# # =====================================================
# # EXECUTE
# # =====================================================

# @app.post("/execute")
# def execute_api(request: QuestionRequest):

#     sql = generate_sql(request.question)

#     result = execute_sql(sql)

#     return {

#         "question": request.question,
#         "generated_sql": sql,
#         "result": result

#     }

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