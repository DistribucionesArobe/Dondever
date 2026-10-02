import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import reel_storage
from post_reel import eligible
from generate_instagram import TZ_MX

class ReelTests(unittest.TestCase):
    def test_selection(self):
        now=datetime(2026,10,1,12,tzinfo=TZ_MX)
        game={'starts_at':(now+timedelta(hours=2)).isoformat(),'status':'STATUS_SCHEDULED','channel_verified':True,'mx_channels':['ESPN'],'league_slug':'nfl'}
        self.assertEqual(eligible([game],now),[game])
        for change in [{'status':'STATUS_IN_PROGRESS'},{'starts_at':(now+timedelta(minutes=5)).isoformat()},{'starts_at':(now+timedelta(days=1)).isoformat()},{'channel_verified':False}]:
            self.assertEqual(eligible([{**game,**change}],now),[])
    def test_checkpoint_and_private_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            reel_storage.ROOT=Path(folder); os.environ['INTERNAL_API_KEY']='test-secret'
            app=FastAPI(); app.include_router(reel_storage.router); client=TestClient(app)
            url='/api/internal/reels/2026-10-01'; headers={'x-internal-api-key':'test-secret'}
            self.assertEqual(client.get(url).status_code,403)
            self.assertEqual(client.post(url+'/reserve',headers=headers).status_code,200)
            self.assertEqual(client.post(url+'/reserve',headers=headers).status_code,409)
            self.assertEqual(client.post(url+'/checkpoint',headers=headers,json={'status':'published'}).status_code,409)
            for state in ['container','publishing','published']:
                self.assertEqual(client.post(url+'/checkpoint',headers=headers,json={'status':state,'container':'123','media':'456'}).status_code,200)
            self.assertEqual(client.get(url,headers=headers).json()['status'],'published')
            self.assertEqual(client.get('/reels/publications.sqlite').status_code,404)
            self.assertEqual(client.put(url+'/video',headers=headers,content=b'bad').status_code,400)

if __name__=='__main__': unittest.main()
