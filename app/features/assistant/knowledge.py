"""Static RAG corpus: intent exemplars (EN / Bangla / Banglish) and domain knowledge.

Knowledge text mirrors the backend-canonical rules; it explains, it never recalculates."""

INTENT_EXAMPLES: dict[str, list[str]] = {
    "overview": [
        "show overall summary", "how is quality today", "dashboard summary", "kpi overview",
        "pass rate koto", "aj koto roll inspect hoise", "ajker obostha ki", "quality summary dao",
        "এই মাসের অবস্থা কেমন", "পাস রেট কত", "overall status", "how many rolls inspected this month",
        "fail koto gula", "total inspected rolls", "summary of this month",
    ],
    "list_buyers": [
        "show buyers", "list of buyers", "buyer list dao", "amake buyer er list dao", "buyer gula dekhao",
        "how many buyers", "koyta buyer ache", "বায়ারের তালিকা দাও", "all buyers", "buyer koto",
    ],
    "list_suppliers": [
        "show suppliers", "supplier list", "supplier gula dekhao", "mill list", "vendor list",
        "how many suppliers", "koyta supplier ache", "সাপ্লায়ারের তালিকা", "all suppliers with pass rate",
    ],
    "list_plms": [
        "show plms", "plm list", "plm gula dao", "supplier onujayi plm", "ei maser plm list dao",
        "plms of square this month", "orders this month", "plm koto gula", "এই মাসের পিএলএম দাও",
        "plm list by buyer", "production orders", "last month plm", "plm of supplier",
    ],
    "plm_detail": [
        "plm details", "show plm MNS-489", "plm er details dao", "plm detail with rolls",
        "what happened in plm", "plm status", "পিএলএম এর বিস্তারিত", "ei plm er obostha",
    ],
    "report": [
        "4 point report print", "open gsm report", "print moisture report for plm", "pdf dao",
        "4 point er print dao", "report download", "shade check report", "রিপোর্ট প্রিন্ট",
        "factory report pdf", "correlation report", "skew report print",
    ],
    "list_rolls": [
        "show failed rolls", "fail roll gula dao", "rolls on hold", "rejected rolls", "roll list",
        "which rolls failed this month", "ফেল রোল দেখাও", "inspected rolls of square", "claim rolls",
        "pending decision rolls", "roll gula dekhao", "roll inspection list",
    ],
    "defect_pareto": [
        "top defects", "most common defect", "defect pareto", "kon defect beshi", "defect gula dekhao",
        "সবচেয়ে বেশি ত্রুটি", "which defect causes most points", "defect summary",
    ],
    "supplier_scorecard": [
        "supplier scorecard", "best supplier", "worst supplier", "supplier grade", "supplier ranking",
        "kon supplier valo", "সাপ্লায়ার স্কোর", "supplier performance", "supplier rating",
    ],
    "trend": [
        "pass rate trend", "quality trend", "trend dekhao", "monthly trend", "daily pass rate",
        "কোয়ালিটি ট্রেন্ড", "how is pass rate changing",
    ],
    "list_schedules": [
        "show schedules", "today's schedule", "ajker schedule", "pending schedules", "schedule list",
        "সিডিউল দেখাও", "upcoming inspections", "tomorrow schedule", "in progress schedules",
    ],
    "list_consignments": [
        "show consignments", "fabric receive list", "received fabric", "consignment gula dao",
        "draft consignments", "finalized qc setup", "কনসাইনমেন্ট তালিকা", "fabric receives this month",
    ],
    "list_materials": [
        "show materials", "material list", "fabric materials", "material gula dao", "gsm of materials",
        "ম্যাটেরিয়াল তালিকা",
    ],
    "list_defect_types": [
        "defect types", "defect type list", "defect er type gula", "4 point defect types", "ত্রুটির ধরন",
    ],
    "knowledge": [
        "what is 4 point", "how is gsm calculated", "4 point ki", "how does moisture pass",
        "skew formula", "explain relaxation", "kivabe calculate hoy", "ফোর পয়েন্ট কী", "what is plm",
        "how does correlation work", "what is shade check", "explain shrinkage", "how to create plm",
    ],
    "help": [
        "help", "what can you do", "tumi ki korte paro", "ki ki jante pari", "সাহায্য", "commands",
    ],
    "greeting": [
        "hi", "hello", "salam", "assalamualaikum", "hey", "good morning", "kemon acho", "হ্যালো",
    ],
    "thanks": ["thanks", "thank you", "dhonnobad", "ধন্যবাদ", "thx", "great"],
}

KNOWLEDGE: list[dict] = [
    {"title": "4-Point Fabric Inspection", "module": "fabric-inspection", "content":
        "4-point points per 100 square yards = total points x 3600 / (width in inches x length in yards). "
        "Each roll passes when its points are within the individual standard. The overall shipment passes "
        "against the average standard using the sum of every roll's area. Defect types and their point ranges "
        "are dashboard master data; reports snapshot the names and points at inspection time. Workers enter "
        "defects in the app; the backend calculates and prints the same PDF everywhere."},
    {"title": "GSM Check", "module": "gsm", "content":
        "GSM over/less % = (actual - required) / required x 100. A roll passes when the absolute average "
        "deviation is within the tolerance. Before-wash and after-wash are checked; an empty after-wash is not "
        "a PASS. Required GSM and tolerance come from the Material."},
    {"title": "Moisture Check", "module": "moisture", "content":
        "Actual moisture is compared with the maximum allowed for the fabric composition from the material / "
        "consignment snapshot. Above the maximum fails."},
    {"title": "Skew & Bowing", "module": "skew-bowing", "content":
        "Skew amount = |AC - BD| compared with the width and fabric type (solid / stripe) acceptance. "
        "Skew % = |AC - BD| / (AC + BD) x 100."},
    {"title": "Fabric Relaxation", "module": "relaxation", "content":
        "Relaxation records the length and width % change after the relaxation hours set on the dashboard "
        "(backend defaults to 12 hours if missing). It has no pass/fail."},
    {"title": "Shrinkage / Pattern", "module": "shrinkage", "content":
        "Shrinkage records length and width change after wash. There is no supplier pass/fail for shrinkage."},
    {"title": "Shade Check", "module": "shade-check", "content":
        "Shade check is entered in the app against the shade points snapshotted from the supplier QC profile "
        "(top-middle-bottom and width checks)."},
    {"title": "Color Continuity", "module": "color-continuity", "content":
        "Color continuity is entered in the app per roll to confirm shade consistency across a lot."},
    {"title": "Fabric Correlation", "module": "correlation", "content":
        "Correlation is derived: the supplier/mill total points planned per roll on the dashboard are compared "
        "with the factory 4-point result. It is view/print only in the app."},
    {"title": "Factory Report", "module": "factory-report", "content":
        "The factory report is derived from the 4-point inspection and is view/print only."},
    {"title": "Production flow", "module": None, "content":
        "Supplier QC Profile -> Fabric Receive consignment QC setup (draft, then finalize; edits create a new "
        "version) -> PLM snapshots the finalized QC config -> colors and rolls are planned (marked width/length, "
        "quantity per roll, supplier total point) -> schedule -> factory app inspects rolls -> backend calculates "
        "-> the same PDF via the report endpoint."},
    {"title": "What is a PLM", "module": None, "content":
        "A PLM is a production order under one received consignment. It snapshots the QC setup so later "
        "consignment edits never rewrite history. PLMs are created on the dashboard, never in the app."},
    {"title": "Roles and access codes", "module": None, "content":
        "Admins log in to the dashboard with email and password. App users log in with access codes: Worker "
        "(4-point defect entry) and Inspector (all inspection modules and schedules). Codes can be active, "
        "blocked (access paused) or deleted (a new code is needed)."},
    {"title": "Roll disposition", "module": None, "content":
        "After inspection each roll gets a decision: pending, accepted, hold, rejected or claim, with an "
        "optional note and shade band. Failed rolls waiting for a decision show as pending."},
    {"title": "Supplier scorecard", "module": None, "content":
        "Supplier score = 70% roll pass rate + 30% communication rating scaled to 100. "
        "Grade A >= 90, B >= 75, C >= 60, otherwise D."},
    {"title": "Units", "module": None, "content":
        "The measurement unit (inch or cm) belongs to the supplier. Quantity unit is yards or meters."},
]

MODULE_LABELS = {
    "fabric-inspection": "4-Point Fabric Inspection",
    "gsm": "GSM Check",
    "moisture": "Moisture Check",
    "skew-bowing": "Skew & Bowing",
    "relaxation": "Fabric Relaxation",
    "shrinkage": "Shrinkage / Pattern",
    "shade-check": "Shade Check",
    "color-continuity": "Color Continuity",
    "correlation": "Fabric Correlation",
    "factory-report": "Factory Report",
}
