from pydantic import BaseModel


class LockRequest(BaseModel):
    plm_id: str
    roll_no: str
    module: str   # 'gsm' | 'moisture' | 'skew_bowing' | 'fabric_inspection' | ...


class LockStatus(BaseModel):
    locked: bool
    locked_by: str | None = None
