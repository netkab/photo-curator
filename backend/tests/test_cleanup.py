import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from PIL import Image
from app.config import settings
from app.db import session_scope
from app.models import Media, GpItem, DupGroup, DupMember, ReviewAction, GpOperation
from app.pipeline import direct
from app.security import local_token

ACCOUNT = "photos-test@example.invalid"

def sync(client, items=None, **extra):
    if items is None:
        items = [dict(media_key=f"media-{i}", dedup_key=f"key-{i}", file_name=f"photo-{i}.jpg",
                      width=4000, height=3000, is_owned=True,
                      thumb_url=f"https://lh3.googleusercontent.com/picture-{i}") for i in range(3)]
    return client.post("/api/gp/sync", json={"account": ACCOUNT, "items": items, **extra})

def group(client):
    assert sync(client).status_code == 200
    with session_scope() as s:
        ids = [m.id for m in s.query(Media).order_by(Media.id).all()]
        g = DupGroup(method="thumbnail-phash", keeper_media_id=ids[0])
        s.add(g);s.flush()
        for mid in ids: s.add(DupMember(group_id=g.id, media_id=mid))
        gid = g.id
    return gid, ids

def review(client):
    gid, ids = group(client)
    r = client.post(f"/api/dedup/groups/{gid}/approve", json={})
    assert r.status_code == 200, r.text
    return r.json()["action_id"], ids

def operation(client, live=False):
    aid, ids = review(client)
    assert client.post(f"/api/review/{aid}/approve").status_code == 200
    settings.live_trash_enabled = live
    r = client.post(f"/api/review/{aid}/apply", json={"dry_run": not live})
    assert r.status_code == 200, r.text
    return r.json()["result"]["operation_id"], aid

def test_token_protects_api_and_thumbnails(client):
    client.headers.pop("Authorization")
    for path in ["/api/health", "/api/gp/status", "/media/thumbs/anything.jpg", "/docs", "/openapi.json"]:
        assert client.get(path).status_code == 401
    assert client.get("/api/health?token=" + local_token()).status_code == 401

def test_origins_hosts_and_preflight(client):
    assert client.get("/api/health", headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.invalid"}).status_code == 400
    for origin in ["http://localhost:5177", "chrome-extension://" + "a"*32]:
        r = client.options("/api/health", headers={"Origin": origin,
                     "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-private-network" not in r.headers
    assert client.get("/api/health", headers={"Origin": "chrome-extension://" + "b"*32}).status_code == 403

def test_pair_cookie_and_csrf(client):
    client.headers.pop("Authorization")
    assert client.post("/api/auth/login", json={"token": local_token()}).status_code == 403
    r = client.post("/api/auth/login", json={"token": local_token()}, headers={"Origin": "http://localhost:5177"})
    assert r.status_code == 200
    assert "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"Origin": "http://localhost:5177"}).status_code == 200
    assert client.get("/api/health").status_code == 401
    if os.name != "nt": assert (settings.data_dir / ".local-token").stat().st_mode & 0o777 == 0o600

def test_direct_catalog_idempotent_checkpoint_and_account(client):
    assert sync(client, next_page_id="page2").json()["inserted"] == 3
    assert sync(client, next_page_id="page2").json()["inserted"] == 0
    state = client.get("/api/gp/scan-state", params={"account": ACCOUNT}).json()
    assert state["page_id"] == "page2" and state["items"] == 3
    assert sync(client, items=[], page_id="page2").status_code == 200
    assert client.get("/api/gp/scan-state", params={"account": ACCOUNT}).json()["complete"]
    with session_scope() as s:
        assert s.query(Media).count() == 3
        assert all(g.media_id and g.match_method == "direct" for g in s.query(GpItem))
        assert all(m.source == "google-photos-thumbnail" and m.thumbnail_sha256 is None for m in s.query(Media))
    assert client.post("/api/gp/sync", json={"account":"another@example.invalid", "items":[]}).status_code == 409
    assert client.post("/api/gp/sync", json={"account":"/u/0", "items":[]}).status_code == 422

def test_no_permanent_delete_or_unrelated_endpoints(client):
    for name in ["delete", "permanent_delete", "empty_trash", "set_description", "add_to_album"]:
        assert client.post("/api/gp/operations", json={"op":name,"keys":["key"],"account":ACCOUNT}).status_code == 400
    for path in ["/api/enhance/run", "/api/videos/highlights", "/api/maps/clusters", "/api/reclaim/run", "/api/ingest",
                 "/api/dedup/groups/1/trash-all", "/api/gp/link"]:
        assert client.post(path, json={}).status_code == 404

def test_pending_cannot_apply_and_dry_run_default(client):
    aid, _ = review(client)
    assert client.post(f"/api/review/{aid}/apply").status_code == 409
    assert client.post(f"/api/review/{aid}/approve").status_code == 200
    r = client.post(f"/api/review/{aid}/apply")
    oid = r.json()["result"]["operation_id"]
    assert client.get(f"/api/gp/operations/{oid}").json()["dry_run"] is True
    assert client.post(f"/api/review/{aid}/apply", json={"dry_run":False}).status_code == 403

def test_arbitrary_keys_cannot_reuse_approval(client):
    oid, aid = operation(client, live=True)
    for keys in [["key-0"], ["unknown"], ["key-1", "key-0"]]:
        r = client.post("/api/gp/operations", json={"op":"trash", "account":ACCOUNT,
                        "keys":keys,"review_action_id":aid,"dry_run":False})
        assert r.status_code == 409
    assert client.post("/api/gp/operations", json={"op":"restore", "account":ACCOUNT,
                     "keys":["key-1"],"dry_run":False}).status_code == 400

def test_keeper_content_alias_protected(client):
    aid, ids = review(client)
    with session_scope() as s:
        s.query(GpItem).filter(GpItem.media_id == ids[1]).one().dedup_key = "key-0"
    assert client.post(f"/api/review/{aid}/approve").status_code == 200
    with session_scope() as s:
        payload = json.loads(s.get(ReviewAction, aid).payload)
        assert payload["approved_keys"] == ["key-2"]
        assert payload["protected_keys"] == ["key-0"]

def test_unknown_ownership_blocks_review(client):
    aid, ids = review(client)
    with session_scope() as s:
        s.query(GpItem).filter(GpItem.media_id == ids[1]).one().is_owned = None
    assert client.post(f"/api/review/{aid}/approve").status_code == 409

def test_keeper_must_belong_to_group(client):
    gid, ids = group(client)
    assert client.post(f"/api/dedup/groups/{gid}/keeper", json={"media_id":9876}).status_code == 400
    assert client.post("/api/dedup/approve-bulk", json={}).status_code == 400

def test_idempotent_apply_and_no_double_lease(client):
    oid, aid = operation(client, live=True)
    with ThreadPoolExecutor(2) as pool:
        calls = list(pool.map(lambda _: client.post(f"/api/review/{aid}/apply", json={"dry_run":False}), range(2)))
    assert {r.json()["result"]["operation_id"] for r in calls} == {oid}
    with ThreadPoolExecutor(2) as pool:
        calls = list(pool.map(lambda _: client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}), range(2)))
    assert sorted(r.status_code for r in calls) == [200,409]

def test_results_account_dryrun_and_undo(client):
    oid, aid = operation(client)
    assert client.get(f"/api/gp/operations/{oid}/next").status_code == 409
    assert client.get(f"/api/gp/operations/{oid}/next", params={"account":"other"}).status_code == 409
    batch = client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).json()
    assert client.post(f"/api/gp/operations/{oid}/result", json={"cursor":0,"succeeded":["intruder"]}).status_code == 400
    good={"cursor":0, "succeeded":batch["batch"]}
    assert client.post(f"/api/gp/operations/{oid}/result", json=good).json()["status"] == "done"
    assert client.post(f"/api/gp/operations/{oid}/result", json=good).json()["ignored"] == "stale cursor"
    assert client.post(f"/api/gp/operations/{oid}/undo").status_code == 400
    with session_scope() as s:
        assert s.query(GpItem).filter(GpItem.trashed.is_(True)).count() == 0
        assert s.get(ReviewAction, aid).status == "approved"

def test_live_trash_record_and_reversible_undo(client):
    oid, aid = operation(client, live=True)
    batch=client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).json()
    assert client.post(f"/api/gp/operations/{oid}/undo").status_code == 409
    client.post(f"/api/gp/operations/{oid}/result", json={"cursor":0,"succeeded":batch["batch"]})
    inverse=client.post(f"/api/gp/operations/{oid}/undo").json()
    assert inverse["op"] == "restore" and inverse["dry_run"] is False
    rid=inverse["id"]
    restored=client.get(f"/api/gp/operations/{rid}/next", params={"account":ACCOUNT}).json()
    client.post(f"/api/gp/operations/{rid}/result", json={"cursor":0,"succeeded":restored["batch"]})
    with session_scope() as s: assert s.query(GpItem).filter(GpItem.trashed.is_(True)).count() == 0

def test_live_disabled_rechecked_at_execution(client):
    oid, _ = operation(client, live=True)
    settings.live_trash_enabled=False
    assert client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).status_code == 403

def test_cancelled_inflight_results_recorded_without_restart(client):
    oid, _ = operation(client, live=True)
    batch=client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).json()
    client.post(f"/api/gp/operations/{oid}/cancel")
    assert client.post(f"/api/gp/operations/{oid}/undo").status_code == 409
    r=client.post(f"/api/gp/operations/{oid}/result", json={"cursor":0,"succeeded":batch["batch"]})
    assert r.json()["status"] == "cancelled" and r.json()["done_count"] == 2
    assert client.post(f"/api/gp/operations/{oid}/undo").status_code == 200

@pytest.mark.parametrize("url", ["http://lh3.googleusercontent.com/x", "https://evil.invalid/x",
    "https://lh3.googleusercontent.com.evil.invalid/x", "https://localhost/x",
    "https://127.0.0.1/x", "https://user@lh3.googleusercontent.com/x", "https://lh3.googleusercontent.com:99/x",
    "https://photos.fife.usercontent.google.com.evil.invalid/x", "https://evil.usercontent.google.com/x",
    "http://photos.fife.usercontent.google.com/x", "https://photos.fife.usercontent.google.com:99/x",
    "https://user@photos.fife.usercontent.google.com/x"])
def test_thumbnail_url_allowlist(url):
    with pytest.raises(ValueError): direct.thumbnail_url(url)

def test_thumbnail_normalization():
    assert direct.thumbnail_url("https://lh3.googleusercontent.com/a=w2000?secret=x") == "https://lh3.googleusercontent.com/a=w512-h512-no"
    assert direct.thumbnail_url("https://photos.fife.usercontent.google.com/a=w2000?secret=x") == "https://photos.fife.usercontent.google.com/a=w512-h512-no"

class Handle:
    cancelled=False
    def update(self, *args): pass

def photo():
    pixels=np.random.default_rng(17).integers(0,256,(120,160,3),dtype=np.uint8)
    out=io.BytesIO();Image.fromarray(pixels).save(out,format="PNG");return out.getvalue()

def test_thumbnail_analysis_resume_and_review_persistence(client, monkeypatch):
    assert sync(client).status_code == 200
    calls=[]
    def fetch(url): calls.append(url);return photo()
    monkeypatch.setattr(direct, "fetch_thumbnail", fetch)
    result=direct.analyze(Handle())
    assert result["cached"] == 3 and result["groups"] == 1
    with session_scope() as s:
        group=s.query(DupGroup).one();group.reviewed=True
        assert len(group.members) == 3
        assert all(m.thumbnail_sha256 != m.sha256 for m in s.query(Media))
    again=direct.analyze(Handle())
    assert again["cached"] == 0 and len(calls) == 3 and again["groups"] == 0
    assert again["already_cached"] == 3
    with session_scope() as s: assert s.query(DupGroup).count() == 1

def test_low_information_and_bad_images():
    out=io.BytesIO();Image.new("RGB",(100,100),"white").save(out,format="PNG")
    assert direct.image_features(out.getvalue())[1] is None
    with pytest.raises(Exception): direct.image_features(b"not an image")
    with pytest.raises(ValueError): direct.image_features(b"x"*(direct.MAX_BYTES+1))

def test_videos_excluded_and_failed_thumbs_retry(client, monkeypatch):
    sync(client)
    with session_scope() as s:
        s.query(Media).first().media_type="video"
    monkeypatch.setattr(direct,"fetch_thumbnail",lambda _: (_ for _ in ()).throw(ValueError("expired")))
    with pytest.raises(ValueError, match="No photos could be analyzed: 2 thumbnail downloads failed.*expired"):
        direct.analyze(Handle())
    monkeypatch.setattr(direct,"fetch_thumbnail",lambda _:photo())
    assert direct.analyze(Handle())["cached"] == 2

def test_paused_batch_needs_explicit_resume(client):
    import time
    oid, _ = operation(client)
    client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT})
    client.post(f"/api/gp/operations/{oid}/result", json={"cursor":0,"error":"tab closed"})
    assert client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).json()["batch"] == []
    with session_scope() as s:
        op=s.get(GpOperation,oid)
        args=json.loads(op.args);args["lease_until"]=time.time()-1;op.args=json.dumps(args)
    client.post(f"/api/gp/operations/{oid}/resume")
    batch=client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).json()
    assert batch["cursor"] == 0 and batch["batch"] == ["key-1","key-2"]

def test_changed_keeper_blocks_existing_live_queue(client):
    oid, _ = operation(client, live=True)
    with session_scope() as s:
        s.query(GpItem).filter(GpItem.dedup_key == "key-0").one().trashed=True
    assert client.get(f"/api/gp/operations/{oid}/next", params={"account":ACCOUNT}).status_code == 409

def test_clip_candidates_require_time_proximity(client):
    from datetime import datetime, timedelta
    sync(client)
    with session_scope() as s:
        rows=s.query(Media).order_by(Media.id).all()
        for i,m in enumerate(rows):
            m.phash = ["aabbccddaabbccdd", "eeff1122eeff1122", "1199ee771199ee77"][i]
            m.clip_embedding=np.array([1,0],np.float32).tobytes()
            m.taken_at=datetime(2024,1,1)+timedelta(seconds=i*60 if i<2 else 600)
    assert direct.cluster(Handle(),use_clip=False) == 0
    assert direct.cluster(Handle(),use_clip=True) == 1
    with session_scope() as s:
        g=s.query(DupGroup).one()
        assert g.method == "thumbnail-clip" and len(g.members) == 2

def test_redirects_and_oversize_downloads_rejected(monkeypatch):
    class Response:
        status_code=302
        headers={"Content-Type":"image/jpeg"}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_content(self,size): yield b'x'*(direct.MAX_BYTES+1)
    response=Response()
    class Http:
        trust_env=True
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,url,**kwargs):
            assert self.trust_env is False and kwargs['allow_redirects'] is False
            assert kwargs['timeout'] == (5,20)
            return response
    monkeypatch.setattr(direct.requests,'Session',Http)
    with pytest.raises(ValueError,match='HTTP 302'): direct.fetch_thumbnail('https://lh3.googleusercontent.com/x')
    response.status_code=200
    with pytest.raises(ValueError,match='4 MB'): direct.fetch_thumbnail('https://lh3.googleusercontent.com/x')

def test_browser_preview_queue_upload_and_cached_analysis(client, monkeypatch):
    import base64
    sync(client)
    queue=client.get('/api/direct/thumbnail-queue?limit=2').json()['items']
    assert len(queue)==2
    first=queue[0]
    body={'account':ACCOUNT,'data':base64.b64encode(photo()).decode()}
    path=f"/api/direct/thumbnails/{first['media_id']}"
    assert client.post(path,json={**body,'account':'another'}).status_code==409
    assert client.post(path,json={**body,'data':'not base64'}).status_code==400
    assert client.post(path,json={**body,'data':base64.b64encode(b'not an image').decode()}).status_code==400
    assert client.post(path,json=body).json()['cached']
    assert client.post(path,json=body).json()['cached'] is False
    remaining=client.get('/api/direct/thumbnail-queue').json()['items']
    assert len(remaining)==2 and all(i['media_id']!=first['media_id'] for i in remaining)
    for item in remaining:
        assert client.post(f"/api/direct/thumbnails/{item['media_id']}",json=body).status_code==200
    assert client.get('/api/direct/thumbnail-queue').json()['items']==[]
    monkeypatch.setattr(direct,'fetch_thumbnail',lambda _: pytest.fail('Cached analysis must not fetch URLs'))
    result=direct.analyze(Handle(),cached_only=True)
    assert result['already_cached']==3 and result['groups']==1
    client.headers.pop('Authorization')
    assert client.get('/api/direct/thumbnail-queue').status_code==401
    assert client.post(path,json=body).status_code==401

def test_all_failed_downloads_preserve_previous_groups(client,monkeypatch):
    gid,ids=group(client)
    monkeypatch.setattr(direct,'fetch_thumbnail',lambda _: (_ for _ in ()).throw(ValueError('Thumbnail HTTP 403')))
    with pytest.raises(ValueError,match='No photos could be analyzed'):
        direct.analyze(Handle())
    with session_scope() as s:
        assert s.get(DupGroup,gid) is not None
        assert s.query(DupMember).count()==3
