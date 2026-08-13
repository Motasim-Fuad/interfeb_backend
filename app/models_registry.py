"""
Central import of every SQLAlchemy model in the project. Alembic's env.py imports THIS
file (not each feature individually) so autogenerate always sees the full schema.
Adding a new module later = add one import line here, nothing else.
"""
from app.features.auth import models as auth_models                          # noqa
from app.features.admin_config import models as admin_config_models          # noqa
from app.features.plm import models as plm_models                            # noqa
from app.features.store import models as store_models                        # noqa
from app.features.attachments import models as attachments_models            # noqa
from app.features.fabric_inspection import models as fabric_inspection_models  # noqa
from app.features.gsm import models as gsm_models                            # noqa
from app.features.moisture import models as moisture_models                  # noqa
from app.features.skew_bowing import models as skew_bowing_models            # noqa
from app.features.relaxation import models as relaxation_models              # noqa
from app.features.shade_check import models as shade_check_models            # noqa
from app.features.shrinkage import models as shrinkage_models                # noqa
from app.features.correlation import models as correlation_models           # noqa
from app.features.color_continuity import models as color_continuity_models  # noqa
from app.features.factory_report import models as factory_report_models     # noqa
from app.features.inspection_report import models as inspection_report_models  # noqa
