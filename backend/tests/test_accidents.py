import json
from datetime import datetime, timedelta
import numpy as np
from PIL import Image
from app.config import settings
from app.db import session_scope
from app.models import Media, GpItem, ReviewAction, GpOperation
from app.pipeline.accidents import analyze, find_bursts, preview_signals

class Handle:
    cancelled = False
    def update(self, *args): pass

def seed():
    with session_scope() as s:
        ids=[]
        for i in range(5):
            path=f'accident-{i}.jpg'
            Image.new('RGB', (64,64), (2,2,2)).save(settings.thumbs_dir / path)
            m=Media(abs_path='', rel_name=path, source='google-photos-thumbnail', media_type='photo',
                    sha256=str(i), thumb_path=path, taken_at=datetime(2020,1,1)+timedelta(seconds=i*2))
            s.add(m);s.flush();ids.append(m.id)
            s.add(GpItem(media_key=f'm{i}', dedup_key=f'k{i}', account='stable', media_id=m.id, is_owned=i!=4))
    return ids

def test_signals_and_burst_limits(tmp_path):
    p=tmp_path/'x.png'
    Image.new('L',(64,64),0).save(p)
    assert 'Almost entirely dark' in preview_signals(p)
    Image.fromarray(np.random.default_rng(1).integers(0,256,(64,64),dtype=np.uint8)).save(p)
    assert preview_signals(p)==[]
    t=datetime(2020,1,1)
    rows=[dict(id=i,account='a',time=t+timedelta(seconds=i*2),reasons=['dark']) for i in range(30)]
    assert [len(g) for g in find_bursts(rows)]==[25,5]
    assert not find_bursts(rows[:2])
    rows[2]['account']='b'
    assert not find_bursts(rows[:3])

def test_owned_review_only_and_protection(client):
    ids=seed()
    assert analyze(Handle())['groups']==1
    g=client.get('/api/accidents').json()['groups'][0]
    assert {p['id'] for p in g['photos']}==set(ids[:4])
    gid=g['id']
    assert client.post(f'/api/accidents/{gid}/review',json={'media_ids':[ids[4]]}).status_code==400
    assert client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids[:4]}).status_code==409
    r=client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids[:3]})
    assert r.status_code==200,r.text
    aid=r.json()['action_id']
    with session_scope() as s:
        a=s.get(ReviewAction,aid)
        assert a.status=='pending'
        assert json.loads(a.payload)['kept_media_ids']==[ids[3]]
        assert s.query(GpOperation).count()==0
    assert client.post(f'/api/review/{aid}/approve').status_code==200
    r=client.post(f'/api/review/{aid}/apply').json()
    assert r['result']['dry_run'] is True
    assert analyze(Handle())['groups']==0

def test_ignore_cancel_and_stale_ids(client):
    seed(); analyze(Handle())
    gid=client.get('/api/accidents').json()['groups'][0]['id']
    analyze(Handle())
    assert client.post(f'/api/accidents/{gid}/ignore').status_code==409
    gid=client.get('/api/accidents').json()['groups'][0]['id']
    h=Handle();h.cancelled=True
    analyze(h)
    assert client.get('/api/accidents').json()['groups'][0]['id']==gid
    assert client.post(f'/api/accidents/{gid}/ignore').status_code==200
    assert analyze(Handle())['groups']==0

def test_ownership_rechecked_at_review(client):
    ids=seed();analyze(Handle())
    gid=client.get('/api/accidents').json()['groups'][0]['id']
    with session_scope() as s:
        s.query(GpItem).filter(GpItem.media_id==ids[0]).one().is_owned=None
    assert client.post(f'/api/accidents/{gid}/review',json={'media_ids':[ids[0]]}).status_code==409
