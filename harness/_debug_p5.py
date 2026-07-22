import sys, os, json, asyncio, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from baby.models import BabyProfile
from baby.store import BabyProfileStore
from baby.resolution import resolve_and_extract

store = BabyProfileStore(os.path.join(tempfile.mkdtemp(), 'baby.db'))
cid1 = store.get_or_create_customer('ent1', 'emp1', '张姐')
bid1 = store.create_baby(BabyProfile(None, 'ent1', 'emp1', cid1, '壮壮', baby_age='6个月', status='confirmed'))
known = store.list_for_employee('ent1', 'emp1')

class SpyProvider:
    def __init__(self): self.calls = 0
    async def complete(self, messages, retrieved_hits=None, **kw):
        self.calls += 1
        return '{}'

spy = SpyProvider()
r0 = asyncio.run(resolve_and_extract('user: 壮壮6个月', '今天天气不错', known, bid1, spy))
print('calls=%d action=%s baby_id=%s bid1=%s' % (spy.calls, r0.action, r0.baby_id, bid1))
print('action==chat: %s baby_id==bid1: %s' % (r0.action == 'chat', r0.baby_id == bid1))
