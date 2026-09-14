"""Direct catalog analysis controls; scanning lives in the extension."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..jobs import manager
from ..db import serialized
from ..pipeline import direct
router = APIRouter(prefix="/api/direct", tags=["direct cleanup"])
class AnalyzeBody(BaseModel):
    use_clip: bool = False
@router.post("/analyze")
@serialized
def analyze(body: AnalyzeBody | None = None):
    if manager.is_running("direct-analysis"):
        raise HTTPException(409, "Thumbnail analysis is already running")
    body = body or AnalyzeBody()
    return manager.submit("direct-analysis", lambda h: direct.analyze(h, body.use_clip)).to_dict()
