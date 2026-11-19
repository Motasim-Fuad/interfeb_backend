"""Seed the standard 4-point defect types with their ranges.

Only adds what is missing: an existing type (matched by name, any case, active or deleted) is
never renamed or re-activated, and its ranges are left alone unless it has none at all. The first
eight follow the column order of the factory's paper Fabrics Inspection Report.

Revision ID: b7d2e4f1a903
Revises: a1c4e7b9d250
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "b7d2e4f1a903"
down_revision = "a1c4e7b9d250"
branch_labels = None
depends_on = None

# Length of defect -> points (paper form: 0"-3"=1, 3"-6"=2, 6"-9"=3, above 9"=4).
LENGTH = [("0-3", 1), ("3-6", 2), ("6-9", 3), ("9+", 4)]
# In case of hole / opening (paper form: 0"-1"=2, above 1"=4).
HOLE = [("0-1", 2), ("Above 1", 4)]

DEFECTS = [
    ("Shade", LENGTH), ("Hole", HOLE), ("Naps", LENGTH), ("Colour Yarn", LENGTH),
    ("Miss Yarn", LENGTH), ("Thread Knot", LENGTH), ("Slab", LENGTH), ("Spot", LENGTH),
    ("Yarn", LENGTH), ("Thick Yarn", LENGTH), ("Thin Yarn", LENGTH), ("Broken End", LENGTH),
    ("Broken Pick", LENGTH), ("Double End", LENGTH), ("Double Pick", LENGTH), ("Float", LENGTH),
    ("Stain", LENGTH), ("Oil Stain", LENGTH), ("Dirt Mark", LENGTH), ("Crease Mark", LENGTH),
    ("Barre / Weft Bar", LENGTH), ("Reed Mark", LENGTH), ("Starting Mark", LENGTH),
    ("Foreign Fibre / Fly", LENGTH), ("Contamination", LENGTH), ("Selvedge Defect", LENGTH),
    ("Uneven Dyeing", LENGTH), ("Print Defect", LENGTH), ("Snag / Pull", LENGTH),
    ("Needle Line", LENGTH), ("Drop Stitch", LENGTH), ("Lycra Out", LENGTH), ("Cut / Tear", HOLE),
]


def upgrade():
    conn = op.get_bind()
    now = datetime.utcnow()
    types = {r.name.strip().lower(): r for r in conn.execute(sa.text("SELECT id, name, sort_order FROM defect_types"))}
    with_ranges = {r.defect_type_id for r in conn.execute(
        sa.text("SELECT DISTINCT defect_type_id FROM defect_range_configs WHERE defect_type_id IS NOT NULL"))}
    for order, (name, ranges) in enumerate(DEFECTS, 1):
        row = types.get(name.lower())
        if row is None:
            type_id = uuid.uuid4()
            conn.execute(sa.text(
                "INSERT INTO defect_types (id, name, sort_order, is_active, created_at, updated_at) "
                "VALUES (:id, :name, :order, true, :now, :now)"), {"id": type_id, "name": name, "order": order, "now": now})
        else:
            type_id = row.id
            if not row.sort_order:  # give old unsorted types the paper-form order
                conn.execute(sa.text("UPDATE defect_types SET sort_order = :o WHERE id = :id"), {"o": order, "id": type_id})
        if type_id in with_ranges:
            # Existing ranges keep their points; only put them in reading order.
            for pos, (label, _) in enumerate(ranges, 1):
                conn.execute(sa.text(
                    "UPDATE defect_range_configs SET sort_order = :p WHERE defect_type_id = :t "
                    "AND lower(range_label) = lower(:l) AND sort_order = 0"), {"p": pos, "t": type_id, "l": label})
            continue
        for pos, (label, points) in enumerate(ranges, 1):
            conn.execute(sa.text(
                "INSERT INTO defect_range_configs (id, defect_type_id, range_label, points, sort_order, is_active, created_at, updated_at) "
                "VALUES (:id, :t, :l, :p, :o, true, :now, :now)"),
                {"id": uuid.uuid4(), "t": type_id, "l": label, "p": points, "o": pos, "now": now})


def downgrade():
    # Seeded rows may already be used by inspections, so they are kept.
    pass
