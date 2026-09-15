"""Conservative preview-only accident suggestions. Never creates a Google operation."""
import hashlib
import json

import numpy as np
from PIL import Image

from ..config import settings
from ..db import session_scope, mutation_lock
from ..models import Media, GpItem, AccidentGroup, ReviewAction


PROFILES = {
    'conservative': dict(dark=24, contrast=7, edge=1.8, gap=12, duration=90, minimum=3, fraction=.6),
    'broad': dict(dark=65, contrast=18, edge=5, gap=30, duration=180, minimum=2, fraction=.25),
    'very-broad': dict(dark=90, contrast=26, edge=8, gap=45, duration=240, minimum=2, fraction=.15),
}


def preview_signals(path, sensitivity='broad'):
    profile = PROFILES[sensitivity]
    with Image.open(path) as im:
        im = im.convert('L')
        im.thumbnail((256, 256))
        a = np.asarray(im, dtype=np.float32)
    contrast = float(a.std())
    edge = float((np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean()) / 2)
    reasons = []
    if float(np.quantile(a, .95)) < profile['dark']:
        reasons.append('Dark preview')
    if contrast < profile['contrast']:
        reasons.append('Very little visible detail')
    if edge < profile['edge'] and contrast >= profile['contrast']:
        reasons.append('Possibly blurry')
    return reasons


def find_bursts(rows, sensitivity='broad'):
    """Rows sorted by account/time; cap duration and size so chains cannot swallow an event."""
    profile = PROFILES[sensitivity]
    runs, run = [], []
    for row in rows:
        if run and (row['account'] != run[-1]['account'] or
                    (row['time'] - run[-1]['time']).total_seconds() > profile['gap'] or
                    (row['time'] - run[0]['time']).total_seconds() > profile['duration'] or len(run) >= 25):
            runs.append(run)
            run = []
        run.append(row)
    if run:
        runs.append(run)
    return [r for r in runs if sum(bool(x['reasons']) for x in r) >= profile['minimum']
            and sum(bool(x['reasons']) for x in r) / len(r) >= profile['fraction']]


def analyze(handle, sensitivity='broad'):
    if sensitivity not in PROFILES:
        raise ValueError('Unknown sensitivity')
    with session_scope() as s:
        rows = s.query(Media, GpItem).join(GpItem, GpItem.media_id == Media.id).filter(
            Media.source == 'google-photos-thumbnail', Media.media_type == 'photo',
            GpItem.is_owned.is_(True), GpItem.trashed.is_(False)).order_by(GpItem.account, Media.taken_at, Media.id).all()
        excluded = set()
        for action in s.query(ReviewAction).filter(ReviewAction.kind == 'delete', ReviewAction.status != 'dismissed'):
            p = json.loads(action.payload)
            excluded.update(i['media_id'] for i in p.get('items', []))
            excluded.update(p.get('kept_media_ids', []))
            excluded.update(i.get('keeper_media_id') for i in p.get('items', []))
            excluded.add(p.get('keeper_media_id'))
        timeline, missing, no_date = [], 0, 0
        seen = set()
        for n, (m, g) in enumerate(rows):
            if handle.cancelled:
                return {'cancelled': True}
            if m.id in seen:
                continue
            seen.add(m.id)
            if m.taken_at is None:
                no_date += 1
                continue
            reasons = []
            path = settings.thumbs_dir / m.thumb_path if m.thumb_path else None
            if path and path.is_file() and m.id not in excluded:
                try:
                    reasons = preview_signals(path, sensitivity)
                except (OSError, ValueError):
                    missing += 1
            elif m.id not in excluded:
                missing += 1
            timeline.append(dict(id=m.id, account=g.account, time=m.taken_at, reasons=reasons))
            if n % 100 == 0:
                handle.update(n / max(len(rows), 1), f'Checking cached previews: {n:,} / {len(rows):,}')
        bursts = find_bursts(timeline, sensitivity)
        positions = {x['id']: i for i, x in enumerate(timeline)}
        drafts = []
        for burst in bursts:
            if any(x['id'] in excluded for x in burst):
                continue
            # Nearby context is never selected automatically, and remains protected in reviews.
            lo, hi = positions[burst[0]['id']], positions[burst[-1]['id']]
            context = [x for x in timeline[max(0, lo-2):hi+3] if x not in burst
                       and x['account'] == burst[0]['account']
                       and min(abs((x['time'] - burst[0]['time']).total_seconds()),
                               abs((x['time'] - burst[-1]['time']).total_seconds())) <= 300]
            payload = {'members': [{'media_id': x['id'], 'reasons': x['reasons']} for x in burst],
                       'context_ids': [x['id'] for x in context], 'taken_at': burst[0]['time'].isoformat()}
            fingerprint = hashlib.sha256(json.dumps([burst[0]['account'], sorted(x['id'] for x in burst)]).encode()).hexdigest()
            drafts.append((fingerprint, payload))
    if handle.cancelled:
        return {'cancelled': True}
    with mutation_lock, session_scope() as s:
        # Preserve reviewed/ignored results and suppress their photos even if burst boundaries change.
        handled = set()
        for g in s.query(AccidentGroup).filter(AccidentGroup.status != 'pending'):
            handled.update(x['media_id'] for x in json.loads(g.payload)['members'])
        s.query(AccidentGroup).filter(AccidentGroup.status == 'pending').delete()
        count = 0
        for fingerprint, payload in drafts:
            if any(x['media_id'] in handled for x in payload['members']):
                continue
            s.add(AccidentGroup(fingerprint=fingerprint, payload=json.dumps(payload)))
            count += 1
    return {'sensitivity': sensitivity, 'groups': count, 'checked': len(seen), 'unavailable': missing, 'without_date': no_date}
