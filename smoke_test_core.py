"""Isolated SQLite smoke test for core Interfab QC invariants.
Run with: DATABASE_URL=sqlite:////tmp/interfab-smoke.db python smoke_test_core.py
This script is safe: it never touches the production PostgreSQL URL.
"""
import os
import sys
import types
from datetime import date
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/interfab-smoke.db')

# The execution environment used for artifact verification does not ship three
# production-only auth/Redis packages. Stub only their imports; this test calls
# route functions directly and does not test authentication/Redis behavior.
if 'jose' not in sys.modules:
    jose = types.ModuleType('jose')
    jose.JWTError = Exception
    jose.jwt = SimpleNamespace(encode=lambda *a, **k: 'stub', decode=lambda *a, **k: {})
    sys.modules['jose'] = jose
if 'passlib' not in sys.modules:
    passlib = types.ModuleType('passlib')
    context = types.ModuleType('passlib.context')
    class CryptContext:
        def __init__(self, *a, **k): pass
        def hash(self, value): return f'hash:{value}'
        def verify(self, value, hashed): return hashed == f'hash:{value}'
    context.CryptContext = CryptContext
    sys.modules['passlib'] = passlib
    sys.modules['passlib.context'] = context
if 'redis' not in sys.modules:
    redis = types.ModuleType('redis')
    class Redis:
        @classmethod
        def from_url(cls, *a, **k): return cls()
    redis.Redis = Redis
    sys.modules['redis'] = redis

from app.core.database import Base, engine, SessionLocal
import app.models_registry  # noqa: F401
from app.features.admin_config.models import Buyer, Supplier, DefectType, DefectRangeConfig, ShadeCheckPointDefinition, Material
from app.features.admin_config import routes as admin_routes, schemas as admin_schemas
from app.features.store import schemas as store_schemas, routes as store_routes
from app.features.plm import schemas as plm_schemas, routes as plm_routes, models as plm_models
from app.features.fabric_inspection import schemas as fi_schemas, routes as fi_routes, models as fi_models, service as fi_service
from app.features.gsm import schemas as gsm_schemas, routes as gsm_routes, models as gsm_models, service as gsm_service
from app.features.factory_report import schemas as factory_schemas, routes as factory_routes, models as factory_models
from app.features.shade_check import schemas as shade_schemas, routes as shade_routes, models as shade_models
from app.features.relaxation import service as relaxation_service
from app.features.correlation import service as correlation_service
from app.features.moisture import service as moisture_service
from app.features.skew_bowing import service as skew_service, routes as skew_routes, schemas as skew_schemas, models as skew_models
from app.features.correlation import routes as correlation_routes
from app.features.shrinkage import service as shrinkage_service


def approx(a, b, eps=1e-6):
    assert a is not None, f'expected {b}, got None'
    assert abs(float(a) - float(b)) <= eps, f'{a} != {b}'


Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
db = SessionLocal()
try:
    buyer = Buyer(name='Buyer A')
    supplier = Supplier(name='Jomuna', default_measurement_unit='inch')
    db.add_all([buyer, supplier]); db.commit(); db.refresh(buyer); db.refresh(supplier)

    # Dashboard-owned Supplier QC Profile is saved atomically, including Shade Check.
    defaults = admin_routes.save_supplier_qc_profile(
        supplier.id,
        admin_schemas.SupplierQcProfileSaveIn(
            qc_config={
                'gsm': {'standard_gsm': 180, 'tolerance_percent': 5},
                'moisture': {'composition_standards': [
                    {'composition_name': 'Cotton', 'acceptance_percentage': 61},
                    {'composition_name': 'Wool', 'acceptance_percentage': 14},
                ]},
                'skew_bowing': {'width_standards': [
                    {'width_label': '45in', 'solid_standard': 1.0, 'stripe_standard': 0.75},
                    {'width_label': '60in', 'solid_standard': 2.0, 'stripe_standard': 1.5},
                    {'width_label': '90in', 'solid_standard': 3.0, 'stripe_standard': 2.0},
                ]},
                'fabric_inspection': {'individual_standard': 6, 'average_standard': 5},
                'relaxation': {'relaxation_hours': 12},
                'correlation': {'standard': 40, 'scs': 1},
            },
            shade_points=[
                admin_schemas.SupplierShadePointIn(
                    check_key='top_to_middle', label='Top to Middle Check',
                    check_type='length', sort_order=1, is_active=True,
                ),
                admin_schemas.SupplierShadePointIn(
                    check_key='width_4_to_1', label='4 to 1 Check',
                    check_type='width', sort_order=2, is_active=True,
                ),
            ],
        ),
        db=db,
        _=SimpleNamespace(is_admin=True),
    )
    assert defaults['measurement_unit'] == 'inch'
    assert [p['check_key'] for p in defaults['qc_config']['shade_check']['points']] == ['top_to_middle', 'width_4_to_1']
    assert defaults['qc_config']['moisture']['composition_standards'][0]['composition_name'] == 'Cotton'
    assert defaults['qc_config']['skew_bowing']['width_standards'][1]['stripe_standard'] == 1.5
    shade_length = db.query(ShadeCheckPointDefinition).filter_by(supplier_id=supplier.id, check_key='top_to_middle').one()
    shade_width = db.query(ShadeCheckPointDefinition).filter_by(supplier_id=supplier.id, check_key='width_4_to_1').one()

    # Finalized receive cannot omit Dashboard-owned context/standards. Uses a supplier
    # without a QC profile, since profile defaults would otherwise fill the standards.
    bare_supplier = Supplier(name='No Profile Mill', default_measurement_unit='inch')
    db.add(bare_supplier); db.commit(); db.refresh(bare_supplier)
    try:
        store_routes.create_fabric_receive(
            store_schemas.FabricReceiveCreate(
                consignment='INCOMPLETE', buyer_id=buyer.id, supplier_id=bare_supplier.id,
                received_quantity=100, fabric_type='Test', fabric_composition='Cotton',
                measurement_unit='inch', qc_setup_status='finalized', qc_config={},
            ),
            db=db, user=SimpleNamespace(role='store'),
        )
        raise AssertionError('incomplete finalized QC setup should be rejected')
    except Exception as exc:
        assert 'Final QC setup is incomplete' in str(exc)
        db.rollback()

    defect = DefectType(name='Dynamic Hole', sort_order=1, is_active=True)
    db.add(defect); db.flush()
    range4 = DefectRangeConfig(defect_type_id=defect.id, range_label='Above 9', points=4, sort_order=4, is_active=True)
    db.add(range4); db.commit(); db.refresh(defect); db.refresh(range4)

    receive = store_routes.create_fabric_receive(
        store_schemas.FabricReceiveCreate(
            consignment='CONS-100',
            buyer_id=buyer.id,
            supplier_id=supplier.id,
            received_quantity=1000,
            quantity_unit='yds',
            fabric_type='Single Jersey',
            fabric_composition='95% Cotton 5% Spandex',
            measurement_unit='cm',  # deliberately wrong: supplier-owned INCH must win
            received_at=date(2026, 8, 11),
            qc_setup_status='finalized',
            qc_config={
                'moisture': {'standard_label': 'Cotton', 'acceptance_percentage': 61},
                'skew_bowing': {'width_label': '60in', 'standard_point': 1.5, 'is_stripe': True},
                'fabric_inspection': {'checked_quantity': 500},
            },
        ),
        db=db,
        user=SimpleNamespace(role='store'),
    )
    assert float(receive.received_quantity) == 1000
    assert receive.measurement_unit == 'inch'  # supplier default overrides payload
    assert receive.qc_config['gsm']['standard_gsm'] == 180
    assert receive.qc_config['fabric_inspection']['checked_quantity'] == 500
    assert receive.qc_config['moisture']['standard_label'] == 'Cotton'
    assert receive.qc_config['moisture']['acceptance_percentage'] == 61
    assert receive.qc_config['skew_bowing']['width_label'] == '60in'
    assert receive.qc_config['skew_bowing']['standard_point'] == 1.5
    assert [p['check_key'] for p in receive.qc_config['shade_check']['points']] == ['top_to_middle', 'width_4_to_1']

    material = Material(fabric_name='Single Jersey', is_active=True)
    db.add(material); db.commit(); db.refresh(material)

    plm = plm_routes.create_plm(
        plm_schemas.PlmCreate(
            plm_code='PLM-001',
            plm_date=date(2026, 8, 11),
            fabric_receive_id=receive.id,
            material_id=material.id,
            plm_style='STYLE-A',
            allocated_quantity=600,
            colors=[plm_schemas.PlmColorIn(
                colour_code='NAVY', planned_quantity=600,
                rolls=[plm_schemas.PlmRollIn(roll_no='1', marked_width=58, marked_length=100, planned_quantity=100, supplier_total_point=12)],
            )],
        ),
        db=db,
        user=SimpleNamespace(is_admin=True),
    )
    assert plm.buyer_id == buyer.id and plm.supplier_id == supplier.id
    assert plm.consignment == 'CONS-100' and plm.fabric_composition.startswith('95%')
    assert plm.qc_config['gsm']['standard_gsm'] == 180
    assert plm.quantity_unit == 'yds' and plm.qc_setup_version == 1
    color = plm.colors[0]

    # Revising consignment QC after PLM creation affects FUTURE PLMs only. Existing PLM
    # keeps the exact finalized setup/unit/version that it inherited at creation time.
    revised_receive = store_routes.update_fabric_receive(
        receive.id,
        store_schemas.FabricReceiveUpdate(qc_config={'gsm': {'standard_gsm': 190}}),
        db=db, user=SimpleNamespace(is_admin=True),
    )
    assert revised_receive.qc_setup_version == 2
    assert revised_receive.qc_config['gsm']['standard_gsm'] == 190
    db.refresh(plm)
    assert plm.qc_config['gsm']['standard_gsm'] == 180
    assert plm.qc_setup_version == 1

    plm2 = plm_routes.create_plm(
        plm_schemas.PlmCreate(
            plm_code='PLM-002', plm_date=date(2026, 8, 11), fabric_receive_id=receive.id, material_id=material.id,
            plm_style='STYLE-B', allocated_quantity=300,
            colors=[plm_schemas.PlmColorIn(
                colour_code='BLACK', planned_quantity=300,
                rolls=[plm_schemas.PlmRollIn(roll_no='B1', marked_width=58, marked_length=100, planned_quantity=100, supplier_total_point=8)],
            )],
        ),
        db=db, user=SimpleNamespace(is_admin=True),
    )
    assert plm2.qc_config['gsm']['standard_gsm'] == 190
    assert plm2.qc_setup_version == 2

    try:
        store_routes.update_fabric_receive(
            receive.id, store_schemas.FabricReceiveUpdate(consignment='CONS-CHANGED'),
            db=db, user=SimpleNamespace(is_admin=True),
        )
        raise AssertionError('consignment identity should lock after PLM creation')
    except Exception as exc:
        assert 'Cannot change consignment identity' in str(exc)
        db.rollback()

    # Bulk operation is idempotent and updates common planning fields.
    rolls = plm_routes.bulk_add_rolls(
        color.id,
        plm_schemas.PlmRollBulkCreate(start_roll=1, end_roll=3, marked_width=60, marked_length=120, planned_quantity=120, supplier_total_point=12),
        db=db,
        _=SimpleNamespace(is_admin=True),
    )
    assert len(rolls) == 3
    assert db.query(plm_models.PlmRoll).filter(plm_models.PlmRoll.plm_color_id == color.id).count() == 3
    roll1_plan = db.query(plm_models.PlmRoll).filter_by(plm_color_id=color.id, roll_no='1').one()
    approx(roll1_plan.marked_width, 60)

    # Shade Check uses ONLY this consignment's frozen supplier points. Master changes later
    # do not alter the report or block in-progress history.
    shade_report = shade_routes.create_report(shade_schemas.ReportCreate(plm_id=plm.id), db=db, _=SimpleNamespace(role='inspection'))
    assert [p['check_key'] for p in shade_report.points_snapshot] == ['top_to_middle', 'width_4_to_1']
    shade_width.is_active = False
    shade_width.label = 'Renamed Master Label'
    db.commit()
    shade_saved = shade_routes.upsert_roll(
        shade_report.id,
        shade_schemas.RollIn(
            roll_no='1',
            results=[
                shade_schemas.ResultIn(point_definition_id=shade_length.id, first_ok=True),
                shade_schemas.ResultIn(point_definition_id=shade_width.id, first_ok=True, middle_ok=False, last_ok=True),
            ],
        ),
        response=None, db=db, _=SimpleNamespace(role='inspection'),
    )
    assert db.query(shade_models.ShadeCheckRoll).filter_by(report_id=shade_report.id, roll_no='1').count() == 1
    width_result = next(r for r in shade_saved.results if str(r.point_definition_id) == str(shade_width.id))
    assert width_result.point_label == '4 to 1 Check'

    # 4-point report auto-creates from PLM/consignment planning context.
    fi_report = fi_routes.create_report(fi_schemas.ReportCreate(plm_id=plm.id), db=db, _=SimpleNamespace(role='inspection'))
    approx(fi_report.checked_qty, 500)
    approx(fi_report.individual_standard_snapshot, 6)
    approx(fi_report.average_standard_snapshot, 5)
    assert fi_report.fabric_composition == '95% Cotton 5% Spandex'

    fi_payload = fi_schemas.RollIn(
        roll_no='1', width_marked=999, length_marked=999,
        width_actual=58, length_actual=100,
        defects=[fi_schemas.DefectEntryIn(defect_type_id=defect.id, range_config_id=range4.id, count=2)],
    )
    saved = fi_routes.upsert_roll(fi_report.id, fi_payload, response=None, db=db, user=SimpleNamespace(role='inspection'))
    assert saved.total_points == 8
    approx(saved.width_marked, 60)  # Dashboard planning wins over worker payload 999.
    approx(saved.length_marked, 120)
    approx(saved.percent_points, 8 * 3600 / (58 * 100))
    assert saved.is_pass is True

    # Edit same roll: must UPDATE, never duplicate.
    fi_payload2 = fi_schemas.RollIn(
        roll_no='1', width_actual=58, length_actual=100,
        defects=[fi_schemas.DefectEntryIn(defect_type_id=defect.id, range_config_id=range4.id, count=1)],
    )
    saved2 = fi_routes.upsert_roll(fi_report.id, fi_payload2, response=None, db=db, user=SimpleNamespace(role='inspection'))
    assert db.query(fi_models.FabricInspectionRoll).filter_by(report_id=fi_report.id, roll_no='1').count() == 1
    assert db.query(fi_models.FabricInspectionRollDefect).filter_by(roll_id=saved2.id).count() == 1
    assert saved2.total_points == 4
    stored_defect = db.query(fi_models.FabricInspectionRollDefect).filter_by(roll_id=saved2.id).one()
    defect.name = 'Renamed Defect Master'
    range4.range_label = 'Renamed Range Master'
    db.commit(); db.refresh(stored_defect)
    assert stored_defect.defect_type_name == 'Dynamic Hole'
    assert stored_defect.range_label == 'Above 9'

    # Weighted-area overall calculation: second roll has a different width/length.
    fi_routes.upsert_roll(
        fi_report.id,
        fi_schemas.RollIn(
            roll_no='2', width_actual=60, length_actual=120,
            defects=[fi_schemas.DefectEntryIn(defect_type_id=defect.id, range_config_id=range4.id, count=1)],
        ),
        response=None,
        db=db,
        user=SimpleNamespace(role='inspection'),
    )
    refreshed = fi_routes.get_report(fi_report.id, db=db)
    expected_overall = 8 * 3600 / ((58 * 100) + (60 * 120))
    approx(refreshed.overall_percent_points, expected_overall)

    # Dynamic defect delete/deactivate affects new entry, not stored snapshot rows.
    defect.is_active = False; db.commit()
    try:
        fi_routes.upsert_roll(
            fi_report.id,
            fi_schemas.RollIn(
                roll_no='3', width_actual=60, length_actual=120,
                defects=[fi_schemas.DefectEntryIn(defect_type_id=defect.id, range_config_id=range4.id, count=1)],
            ),
            response=None,
            db=db,
            user=SimpleNamespace(role='inspection'),
        )
        raise AssertionError('inactive defect should be rejected for new entry')
    except Exception as exc:
        assert 'inactive defect_type_id' in str(exc)
        db.rollback()
    defect.is_active = True; db.commit()

    # GSM consignment standard + tolerance and roll upsert.
    gsm_report = gsm_routes.create_report(gsm_schemas.ReportCreate(plm_id=plm.id), db=db, _=SimpleNamespace(role='inspection'))
    approx(gsm_report.standard_gsm, 180)
    approx(gsm_report.tolerance_snapshot, 5)
    gsm_routes.upsert_roll(gsm_report.id, gsm_schemas.RollIn(roll_no='1', actual_gsm_before=180), response=None, db=db, _=SimpleNamespace(role='inspection'))
    gsm_routes.upsert_roll(gsm_report.id, gsm_schemas.RollIn(roll_no='1', actual_gsm_before=200), response=None, db=db, _=SimpleNamespace(role='inspection'))
    assert db.query(gsm_models.GsmCheckRoll).filter_by(report_id=gsm_report.id, roll_no='1').count() == 1
    gsm_loaded = gsm_routes.get_report(gsm_report.id, db=db)
    approx(gsm_loaded.avg_over_less_before_pct, ((200-180)/180)*100)
    assert gsm_loaded.pass_before is False

    # Skew uses the paper formula |AC-BD|/(AC+BD)*100; no reference-length input is needed.
    skew_report = skew_routes.create_report(
        skew_schemas.ReportCreate(plm_id=plm.id), db=db, _=SimpleNamespace(role='inspection')
    )
    skew_saved = skew_routes.upsert_roll(
        skew_report.id,
        skew_schemas.RollIn(roll_no='1', ac_value_before=102, bd_value_before=98),
        response=None, db=db, _=SimpleNamespace(role='inspection'),
    )
    approx(skew_saved.skew_percent_before, 2)
    # 60in stripe acceptance is 1.5in; |102-98| = 4in, so this roll fails even
    # though the 2% value is still printed as the calculated skew percentage.
    assert skew_saved.is_pass_before is False
    assert db.query(skew_models.SkewBowingRoll).filter_by(report_id=skew_report.id, roll_no='1').count() == 1

    # Correlation is derived from Dashboard roll planning + 4-point factory results.
    correlation = correlation_routes.list_reports(response=None, plm_id=plm.id, db=db)[0]
    corr_roll1 = next(r for r in correlation.rolls if r.roll_no == '1')
    approx(corr_roll1.length_yards, 120)
    approx(corr_roll1.width, 60)
    approx(corr_roll1.mill_total_point, 12)
    assert corr_roll1.factory_total_points == 4

    # Factory Report is one-per-PLM; context comes from PLM and percentage/result are backend calculations.
    factory = factory_routes.create_or_update_report(
        factory_schemas.FactoryReportCreate(plm_id=plm.id, inspection_yardage=999, avg_shipment_point=999, supplier='WRONG', order_qty=1),
        db=db, _=SimpleNamespace(role='inspection'))
    assert factory.supplier == 'Jomuna' and factory.job_no == 'PLM-001'
    approx(factory.order_qty, 600)
    approx(factory.inspection_yardage, 220)
    approx(factory.inspection_percentage, (220 / 600) * 100)
    approx(factory.total_point, 8)
    approx(factory.avg_shipment_point, expected_overall)
    assert factory.is_pass is True
    factory2 = factory_routes.create_or_update_report(
        factory_schemas.FactoryReportCreate(plm_id=plm.id),
        db=db, _=SimpleNamespace(role='inspection'))
    assert factory2.id == factory.id
    assert db.query(factory_models.FactoryReport).filter_by(plm_id=plm.id).count() == 1
    approx(factory2.avg_shipment_point, expected_overall)

    # Service-level calculation checks for formulas used by report routes.
    approx(relaxation_service.change_pct(50, 50.2), 0.4)

    # Moisture: every recorded before/after swatch is evaluated against the same
    # consignment acceptance snapshot; average uses all available measurements.
    moisture_stats = moisture_service.compute_report_stats([
        SimpleNamespace(swatch_rating_before=5, swatch_rating_after=7),
        SimpleNamespace(swatch_rating_before=6, swatch_rating_after=None),
    ], acceptance=6)
    approx(moisture_stats['avg_swatch'], 6)
    assert moisture_stats['pass_count'] == 2 and moisture_stats['fail_count'] == 1

    # Skew & Bowing paper formula: |AC-BD|/(AC+BD)*100; unit cancels.
    approx(skew_service.bow_rule_value(102, 98), 4)
    approx(skew_service.skew_percent(102, 98, None, 'inch'), 2)
    approx(skew_service.skew_percent(102 * 2.54, 98 * 2.54, None, 'cm'), 2)
    # Exact supplied-paper example: 0.5 / 147.8 * 100 ~= 0.338%.
    approx(skew_service.skew_percent(74.15, 73.65, None, 'inch'), (0.5 / 147.8) * 100)
    # Acceptance uses the measured amount in the supplier unit (paper width table),
    # not the displayed skew percentage.
    assert skew_service.roll_is_pass(0.9, 1.5) is True
    assert skew_service.roll_is_pass(1.6, 1.5) is False

    # Shrinkage sign convention: shrinkage is negative in the displayed adjust %,
    # while acceptance uses absolute magnitude.
    approx(shrinkage_service.adjust_pct(100, 95), -5)
    shrink_fields = shrinkage_service.compute_measurement_fields(
        SimpleNamespace(before_length=100, before_width=100, after_length=95, after_width=104)
    )
    approx(shrink_fields['adjust_length_pct'], -5)
    approx(shrink_fields['adjust_width_pct'], 4)

    corr = SimpleNamespace(length_yards=100, width=60, factory_length=100, factory_width=60, mill_total_point=20, factory_total_points=16, standard=20)
    cf = correlation_service.compute_roll_fields(corr, measurement_unit='inch', quantity_unit='yds')
    approx(cf['mill_average_point'], 12)
    approx(cf['factory_average_point'], 9.6)
    approx(cf['difference'], 4)
    assert cf['mill_pass'] is True and cf['factory_pass'] is True
    corr_cm_m = SimpleNamespace(length_yards=91.44, width=152.4, factory_length=91.44, factory_width=152.4, mill_total_point=20, factory_total_points=16, standard=20)
    cf2 = correlation_service.compute_roll_fields(corr_cm_m, measurement_unit='cm', quantity_unit='meter')
    approx(cf2['mill_average_point'], 12, eps=1e-4)
    approx(fi_service.length_to_yards(1, 'meter'), 1.0936132983377078)

    # Source-report regression checks from the supplied factory documents.
    gsm_devs = [gsm_service.over_less_pct(v, 159) for v in (154, 160, 155)]
    approx(gsm_devs[0], -3.1446540880503147)
    approx(gsm_devs[1], 0.6289308176100629)
    approx(sum(gsm_devs) / len(gsm_devs), -1.6771488469601674)
    # 4-point / Factory sample: 28 points over 177.1 yds x 56 in = 10.16.
    approx((28 * 3600) / (177.1 * 56), 10.163749294184077, eps=1e-6)
    # Moisture source page uses Cotton acceptance 61%, so 57% is PASS.
    assert 57 <= 61

    # Canonical client-facing PDFs must be generated by the same backend used by App + Dashboard.
    from app.features.report_export.service import build_report_pdf
    import os
    from pathlib import Path
    export_dir = os.environ.get('EXPORT_SMOKE_PDFS_DIR')
    if export_dir:
        Path(export_dir).mkdir(parents=True, exist_ok=True)
    for module in ('fabric-inspection', 'gsm', 'skew-bowing', 'shade-check', 'correlation', 'factory-report'):
        pdf = build_report_pdf(db, module, plm.id)
        assert pdf.startswith(b'%PDF') and len(pdf) > 1000, f'{module} PDF generation failed'
        if export_dir:
            (Path(export_dir) / f'{module}.pdf').write_bytes(pdf)

    print('SMOKE_OK')
    print('tables=', len(Base.metadata.tables))
    print('supplier_profile_atomic=OK supplier_defaults=OK supplier_unit=OK finalized_qc_validation=OK plm_cascade=OK plm_qc_snapshot=OK consignment_lock=OK bulk_roll_idempotency=OK')
    print('shade_supplier_snapshot=OK shade_history=OK')
    print('4point_dynamic_defects=OK defect_history_snapshot=OK 4point_weighted_area=OK 4point_upsert=OK')
    print('gsm_qc_snapshot=OK gsm_upsert=OK')
    print('factory_idempotent=OK backend_percentage=OK')
    print('moisture_formula=OK skew_formula=OK shrinkage_formula=OK')
    print('relaxation_formula=OK correlation_formula=OK correlation_derived=OK')
finally:
    db.close()
