"""Accidental bursts use the same ownership, keeper, approval and reversible trash gates."""
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from ..db import db_dependency, serialized
from ..models import AccidentGroup, Media, GpItem, ReviewAction
from ..jobs import manager
from ..pipeline import accidents
from .review import reviewed_targets

router = APIRouter(prefix='/api/accidents', tags=['accidents'])

@router.post('/analyze')
@serialized
def analyze():
    if manager.is_running('accident-analysis'):
        raise HTTPException(409, 'Accident analysis is already running')
    return manager.submit('accident-analysis', accidents.analyze).to_dict()

@router.get('')
def groups(after: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=25), db: Session = Depends(db_dependency)):
    rows = db.query(AccidentGroup).filter(AccidentGroup.status == 'pending', AccidentGroup.id > after).order_by(AccidentGroup.id).limit(limit + 1).all()
    out = []
    for g in rows[:limit]:
        p = json.loads(g.payload)
        photos = []
        reasons = {x['media_id']: x['reasons'] for x in p['members']}
        for mid in list(reasons) + p['context_ids']:
            m = db.get(Media, mid)
            live = db.query(GpItem).filter(GpItem.media_id == mid, GpItem.trashed.is_(False), GpItem.is_owned.is_(True)).first()
            if m and live:
                photos.append({'id': mid, 'name': m.rel_name, 'taken_at': m.taken_at.isoformat() if m.taken_at else None,
                               'thumb': f'/media/thumbs/{m.thumb_path}' if m.thumb_path else None,
                               'reasons': reasons.get(mid, []), 'context': mid not in reasons,
                               'product_url': f'https://photos.google.com/photo/{live.media_key}'})
        out.append({'id': g.id, 'taken_at': p['taken_at'], 'photos': photos})
    return {'groups': out, 'more': len(rows) > limit}

def get_group(db, gid):
    g = db.get(AccidentGroup, gid)
    if not g or g.status != 'pending':
        raise HTTPException(409, 'This group is no longer pending; refresh the page')
    return g

@router.post('/{gid}/ignore')
@serialized
def ignore(gid: int, db: Session = Depends(db_dependency)):
    g = get_group(db, gid)
    g.status = 'ignored'
    db.commit()
    return {'status': 'ignored'}

class Selection(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=25)

@router.post('/{gid}/review')
@serialized
def review(gid: int, body: Selection, db: Session = Depends(db_dependency)):
    g = get_group(db, gid)
    p = json.loads(g.payload)
    members = {x['media_id'] for x in p['members']}
    selected = set(body.media_ids)
    if not selected <= members:
        raise HTTPException(400, 'Only photos in this burst may be selected')
    kept = (members | set(p['context_ids'])) - selected
    live = {x.media_id for x in db.query(GpItem).filter(GpItem.media_id.in_(members | kept), GpItem.trashed.is_(False))}
    if not selected <= live:
        raise HTTPException(409, 'A selected photo is no longer present; run analysis again')
    kept &= live
    if not kept:
        raise HTTPException(409, 'Keep at least one photo from this burst for the protected review')
    a = ReviewAction(kind='delete', payload=json.dumps({'reason': 'Possible accidental burst — selected by you',
        'accident_group_id': gid, 'kept_media_ids': sorted(kept),
        'items': [{'media_id': mid} for mid in sorted(selected)]}))
    reviewed_targets(db, a)  # Validate now; approval and execution validate again.
    db.add(a)
    g.status = 'reviewed'
    db.commit()
    return {'action_id': a.id, 'status': 'pending'}
