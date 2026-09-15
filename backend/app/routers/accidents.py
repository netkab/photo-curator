"""Accidental bursts use the same ownership, keeper, approval and reversible trash gates."""
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from ..db import db_dependency, serialized
from ..models import AccidentGroup, Media, GpItem, ReviewAction
from ..jobs import manager
from ..pipeline import accidents, collections
from .review import reviewed_targets

router = APIRouter(prefix='/api/accidents', tags=['accidents'])

class AnalyzeBody(BaseModel):
    category: Literal['accidents', 'temporary', 'attempts'] = 'accidents'
    sensitivity: Literal['conservative', 'broad', 'very-broad'] = 'broad'

@router.post('/analyze')
@serialized
def analyze(body: AnalyzeBody | None = None):
    body = body or AnalyzeBody()
    job_name = 'accident-analysis' if body.category == 'accidents' else f'{body.category}-analysis'
    if manager.is_running(job_name):
        raise HTTPException(409, 'This analysis is already running')
    target = (lambda h: accidents.analyze(h, body.sensitivity)) if body.category == 'accidents' else (lambda h: collections.analyze(h, body.category, body.sensitivity))
    return manager.submit(job_name, target).to_dict()

@router.get('')
def groups(category: Literal['accidents', 'temporary', 'attempts'] = 'accidents', label: str | None = None, after: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=25), db: Session = Depends(db_dependency)):
    query = db.query(AccidentGroup).filter(AccidentGroup.category == category, AccidentGroup.status == 'pending', AccidentGroup.id > after)
    if label:
        from sqlalchemy import func
        query = query.filter(func.json_extract(AccidentGroup.payload, '$.label') == label)
    rows = query.order_by(AccidentGroup.id).limit(limit + 1).all()
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
        out.append({'id': g.id, 'category': g.category, 'title': p.get('title', 'Burst'), 'keeper_id': p.get('keeper_id'), 'taken_at': p['taken_at'], 'photos': photos})
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
    allow_all: bool = False
    preview: bool = False
    media_ids: list[int] = Field(min_length=1, max_length=25)

def build_selection(gid: int, body: Selection, db: Session):
    g = get_group(db, gid)
    p = json.loads(g.payload)
    members = {x['media_id'] for x in p['members']}
    selected = set(body.media_ids)
    if not selected <= members:
        raise HTTPException(400, 'Only photos in this burst may be selected')
    for previous in db.query(ReviewAction).filter(ReviewAction.kind == 'delete', ReviewAction.status != 'dismissed'):
        prior = json.loads(previous.payload)
        reserved = {i['media_id'] for i in prior.get('items', [])}
        reserved.update(prior.get('kept_media_ids', []))
        reserved.update(i.get('keeper_media_id') for i in prior.get('items', []))
        reserved.add(prior.get('keeper_media_id'))
        if selected & reserved:
            raise HTTPException(409, 'A selected photo is already part of another review; keep it or run analysis again')
    kept = (members | set(p['context_ids'])) - selected
    live = {x.media_id for x in db.query(GpItem).filter(GpItem.media_id.in_(members | kept), GpItem.trashed.is_(False))}
    if not selected <= live:
        raise HTTPException(409, 'A selected photo is no longer present; run analysis again')
    kept &= live
    if not kept and g.category != 'temporary' and not (body.allow_all and selected == (members & live)):
        raise HTTPException(409, 'Keep at least one photo from this burst for the protected review')
    a = ReviewAction(kind='delete', payload=json.dumps({'reason': {'accidents': 'Possible accidental burst', 'temporary': 'Temporary photo candidate', 'attempts': 'Repeated attempts'}[g.category] + ' — selected by you', 'cleanup_category': g.category,
        'accident_group_id': gid, 'kept_media_ids': sorted(kept),
        'items': [{'media_id': mid} for mid in sorted(selected)]}))
    return g, a

@router.post('/{gid}/review')
@serialized
def review(gid: int, body: Selection, db: Session = Depends(db_dependency)):
    g, a = build_selection(gid, body, db)
    account, keys, protected = reviewed_targets(db, a)
    if body.preview:
        return {'account': account, 'keys': keys, 'protected_keys': protected}
    # Approval and execution validate again.
    db.add(a)
    g.status = 'reviewed'
    db.commit()
    return {'action_id': a.id, 'status': 'pending'}


class GroupSelection(Selection):
    group_id: int

class BulkSelection(BaseModel):
    category: Literal['accidents', 'temporary', 'attempts']
    preview: bool = True
    groups: list[GroupSelection] = Field(min_length=1, max_length=100)

@router.post('/review-bulk')
@serialized
def review_bulk(body: BulkSelection, db: Session = Depends(db_dependency)):
    if len({x.group_id for x in body.groups}) != len(body.groups):
        raise HTTPException(400, 'A group may only appear once')
    groups, selected, kept, snapshots = [], set(), set(), []
    unselected_members = set()
    for selection in body.groups:
        group, action = build_selection(selection.group_id, selection, db)
        if group.category != body.category:
            raise HTTPException(400, 'Selection must belong to this tab')
        payload = json.loads(action.payload)
        ids = {x['media_id'] for x in payload['items']}
        selected.update(ids)
        unselected_members.update({x['media_id'] for x in json.loads(group.payload)['members']} - ids)
        kept.update(payload['kept_media_ids'])
        snapshots.append({'group_id': group.id, 'media_ids': sorted(ids), 'allow_all': selection.allow_all})
        groups.append(group)
    if selected & unselected_members:
        raise HTTPException(409, 'A selected photo is unselected in another group. Adjust the selection.')
    # Nearby context may itself be explicitly selected as a member of another included group.
    kept.difference_update(selected)
    payload = {'reason': 'Selected cleanup photos — page selection', 'cleanup_category': body.category,
               'cleanup_selections': snapshots, 'kept_media_ids': sorted(kept),
               'items': [{'media_id': mid} for mid in sorted(selected)]}
    action = ReviewAction(kind='delete', payload=json.dumps(payload))
    account, keys, protected = reviewed_targets(db, action)
    if body.preview:
        return {'account': account, 'keys': keys, 'protected_keys': protected}
    db.add(action)
    for group in groups:
        group.status = 'reviewed'
    db.commit()
    return {'action_id': action.id, 'status': 'pending', 'selected': len(keys)}
