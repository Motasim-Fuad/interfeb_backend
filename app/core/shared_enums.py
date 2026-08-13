"""
Shared Postgres ENUM types used across multiple modules. Defined once here and imported
everywhere, so SQLAlchemy doesn't try to CREATE TYPE the same name twice with different
Python objects (which causes migration conflicts).
"""
from sqlalchemy import Enum

# Used by GSM, Moisture, Skew & Bowing, Shrinkage, Color Continuity (before/after-wash modules)
RollStatusEnum = Enum("before_wash_done", "after_wash_done", name="roll_status_enum")

# cm vs inch, decided once per PLM, drives display/calculation everywhere except the 4-point system
MeasurementUnitEnum = Enum("cm", "inch", name="measurement_unit_enum")

# Locked to exactly 3 app roles - typos in role names used to slip through as free text before this.
AccessCodeRoleEnum = Enum("worker", "inspection", "store", name="access_code_role_enum")
