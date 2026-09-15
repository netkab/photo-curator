"""Local temporary-photo labels and repeated-attempt groups; no mutation execution."""
import hashlib
import json
import platform
import re
import select
from itertools import islice
import shutil
import subprocess
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from ..config import settings
from ..db import session_scope, mutation_lock
from ..models import Media, GpItem, AccidentGroup, PreviewClassification, ReviewAction

LABELS = {
    'documents': 'Documents or receipts — inspect carefully',
    'delivery': 'Delivery or order updates',
    'parking': 'Parking or travel details',
    'shopping': 'Shopping or menus',
    'screenshots': 'Possible screenshots',
    'text': 'Other text-heavy photos — inspect carefully',
}


def classify_text(text, filename=''):
    """Keyword cues suggest a review category, never whether information has expired."""
    text = text.lower()
    def has(pattern):
        return bool(re.search(pattern, text))
    if has(r'\b(receipt|invoice|passport|insurance|prescription|contract|bank statement|medical)\b|קבלה|חשבונית|דרכון|ביטוח|מרשם|חוזה'):
        return 'documents'
    if has(r'\b(delivery|delivered|tracking|shipment|order status|order confirmation|pickup|pick up)\b|משלוח|חבילה|איסוף|הזמנתך'):
        return 'delivery'
    if has(r'\b(parking|boarding|reservation|gate|check.in|flight)\b|חניה|חנייה|טיסה|שער עלייה'):
        return 'parking'
    if has(r'\b(menu|add to cart|shopping list|checkout|discount|sale|shipping)\b|תפריט|הוסף לסל|רשימת קניות|מבצע'):
        return 'shopping'
    if re.search(r'screen.?shot|screen capture|צילום.?מסך', filename.lower()):
        return 'screenshots'
    if len(text.split()) >= 18:
        return 'text'
    return None


class LocalTextReader:
    def __enter__(self):
        if platform.system() != 'Darwin' or not shutil.which('swiftc'):
            raise ValueError('Temporary-photo text recognition currently requires macOS and Apple command-line tools (swiftc).')
        source = Path(__file__).parent.parent / 'native/text_scan.swift'
        signature = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        directory = settings.data_dir / 'helpers'
        directory.mkdir(exist_ok=True, mode=0o700)
        binary = directory / f'text-scan-{signature}'
        if not binary.is_file():
            result = subprocess.run(['swiftc', '-module-cache-path', str(directory / 'module-cache'), '-O', str(source), '-o', str(binary)], capture_output=True, timeout=120)
            if result.returncode:
                raise ValueError('Could not build local text recognition. Check that Apple command-line tools are installed.')
            binary.chmod(0o700)
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        return self

    def read(self, path):
        self.process.stdin.write(json.dumps({'path': str(path)}) + '\n')
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 30)[0]:
            raise ValueError('Local text recognition timed out; cached classifications remain. Retry analysis.')
        line = self.process.stdout.readline()
        if not line:
            raise ValueError('Local text recognition stopped unexpectedly; retry analysis.')
        result = json.loads(line)
        if result.get('error'):
            raise ValueError(result['error'])
        return result['text']

    def __exit__(self, *_):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def reserved_ids(s):
    ids = set()
    for a in s.query(ReviewAction).filter(ReviewAction.kind == 'delete', ReviewAction.status != 'dismissed'):
        p = json.loads(a.payload)
        ids.update(x['media_id'] for x in p.get('items', []))
        ids.update(x.get('keeper_media_id') for x in p.get('items', []))
        ids.update(p.get('kept_media_ids', []))
        ids.add(p.get('keeper_media_id'))
    return ids


def eligible(category):
    with session_scope() as s:
        excluded = reserved_ids(s)
        for group in s.query(AccidentGroup).filter(AccidentGroup.category == category, AccidentGroup.status != 'pending'):
            excluded.update(x['media_id'] for x in json.loads(group.payload)['members'])
        rows = s.query(Media, GpItem).join(GpItem, GpItem.media_id == Media.id).filter(
            Media.source == 'google-photos-thumbnail', Media.media_type == 'photo',
            GpItem.is_owned.is_(True), GpItem.trashed.is_(False)).order_by(GpItem.account, Media.taken_at, Media.id).all()
        seen, out = set(), []
        for m, g in rows:
            if m.id in excluded or m.id in seen:
                continue
            seen.add(m.id)
            out.append(dict(id=m.id, account=g.account, key=g.dedup_key, name=m.rel_name,
                time=m.taken_at, path=settings.thumbs_dir / m.thumb_path if m.thumb_path else None,
                signature=m.thumbnail_sha256 or '', sharpness=m.blur_score or 0,
                width=m.width or 0, height=m.height or 0))
        return out


def repeated_groups(rows, sensitivity='broad'):
    """Compare to each group's anchor; avoid chaining unrelated scenes across a whole event."""
    threshold = {'conservative': 12, 'broad': 20, 'very-broad': 26}[sensitivity]
    used, groups = set(), []
    for i, anchor in enumerate(rows):
        if anchor['id'] in used or not anchor.get('key') or not anchor.get('time') or 'hash' not in anchor:
            continue
        members = [anchor]
        for candidate in islice(rows, i+1, None):
            if candidate['account'] != anchor['account']:
                break
            if candidate['time'] is None:
                continue
            if (candidate['time'] - anchor['time']).total_seconds() > 120:
                break
            if candidate['id'] in used or 'hash' not in candidate or not candidate['key'] or candidate['key'] == anchor['key']:
                continue
            a, b = anchor['aspect'], candidate['aspect']
            if min(a, b) <= 0 or abs(a / b - 1) > .18:
                continue
            distance = (anchor['hash'] ^ candidate['hash']).bit_count()
            color = float(np.minimum(anchor['color'], candidate['color']).sum())
            if distance <= threshold and color >= .60:
                members.append(candidate)
                if len(members) >= 25:
                    break
        if len(members) >= 2:
            used.update(x['id'] for x in members)
            groups.append(members)
    return groups


def publish(category, drafts, handle):
    with mutation_lock, session_scope() as s:
        if handle.cancelled:
            return 0
        excluded = reserved_ids(s)
        for g in s.query(AccidentGroup).filter(AccidentGroup.category == category, AccidentGroup.status != 'pending'):
            excluded.update(x['media_id'] for x in json.loads(g.payload)['members'])
        s.query(AccidentGroup).filter(AccidentGroup.category == category, AccidentGroup.status == 'pending').delete()
        count = 0
        for account, payload in drafts:
            ids = [x['media_id'] for x in payload['members']]
            if excluded.intersection(ids):
                continue
            fingerprint = hashlib.sha256(json.dumps([category, account, sorted(ids)]).encode()).hexdigest()
            s.add(AccidentGroup(category=category, fingerprint=fingerprint, payload=json.dumps(payload)))
            count += 1
        return count


def analyze(handle, category, sensitivity='broad'):
    rows = eligible(category)
    missing = no_date = 0
    drafts = []
    if category == 'temporary':
        handle.update(0, 'Preparing local text recognition…')
        with LocalTextReader() as reader:
            for i, row in enumerate(rows):
                if handle.cancelled:
                    return {'cancelled': True}
                path = row['path']
                if not path or not path.is_file():
                    missing += 1
                    continue
                signature = 'temporary-v1:' + row['signature'] + ':' + row['name']
                with session_scope() as s:
                    cached = s.get(PreviewClassification, row['id'])
                    label = json.loads(cached.labels) if cached and cached.signature == signature else 'uncached'
                if label == 'uncached':
                    text = reader.read(path)
                    label = classify_text(text, row['name'])
                    with session_scope() as s:
                        s.merge(PreviewClassification(media_id=row['id'], signature=signature, labels=json.dumps(label)))
                if label:
                    drafts.append((row['account'], {'title': LABELS[label], 'label': label,
                        'members': [{'media_id': row['id'], 'reasons': [LABELS[label]]}], 'context_ids': [],
                        'taken_at': row['time'].isoformat() if row['time'] else None}))
                handle.update((i+1) / max(len(rows), 1), f'Checking text locally: {i+1:,} / {len(rows):,}; {len(drafts):,} candidates')
    elif category == 'attempts':
        for i, row in enumerate(rows):
            if handle.cancelled:
                return {'cancelled': True}
            if not row['time']:
                no_date += 1
                continue
            if not row['path'] or not row['path'].is_file():
                missing += 1
                continue
            try:
                with Image.open(row['path']) as im:
                    im = im.convert('RGB')
                    row['hash'] = int(str(imagehash.phash(im)), 16)
                    pixels = np.asarray(im.resize((64,64)), dtype=np.float32)
                    if pixels.std() < 7:  # Flat/dark previews belong in accidents, not visual matches.
                        del row['hash']
                        continue
                    hist = np.concatenate([np.histogram(pixels[:,:,c], bins=8, range=(0,256))[0] for c in range(3)])
                    row['color'] = hist / hist.sum()
                    row['aspect'] = im.width / im.height
            except (OSError, ValueError):
                missing += 1
                row.pop('hash', None)
            if i % 100 == 0:
                handle.update(.8*i/max(len(rows),1), f'Comparing cached previews: {i:,} / {len(rows):,}')
        for members in repeated_groups(rows, sensitivity):
            keeper = max(members, key=lambda x: (x['sharpness'], x['width']*x['height']))
            drafts.append((members[0]['account'], {'title': 'Repeated attempts',
                'keeper_id': keeper['id'], 'members': [{'media_id': x['id'], 'reasons':
                    ['Suggested keep: sharper preview; check expression and framing'] if x is keeper else
                    ['Similar appearance within two minutes']} for x in members],
                'context_ids': [], 'taken_at': members[0]['time'].isoformat()}))
    else:
        raise ValueError('Unknown cleanup collection')
    if handle.cancelled:
        return {'cancelled': True}
    if category == 'temporary':
        # Review related reference photos together without implying they are duplicates.
        grouped = {}
        for account, payload in drafts:
            month = (payload['taken_at'] or 'undated')[:7]
            key = (account, payload['label'], month)
            grouped.setdefault(key, []).append(payload)
        drafts = []
        for (account, label, month), photos in grouped.items():
            for start in range(0, len(photos), 25):
                chunk = photos[start:start+25]
                drafts.append((account, {'title': LABELS[label], 'label': label,
                    'members': [m for p in chunk for m in p['members']], 'context_ids': [],
                    'taken_at': chunk[0]['taken_at']}))
    count = publish(category, drafts, handle)
    return {'groups': count, 'checked': len(rows), 'unavailable': missing, 'without_date': no_date,
            'category': category}
