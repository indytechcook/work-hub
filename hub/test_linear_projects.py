import tempfile
import unittest
from pathlib import Path
from linear_projects import refresh_projects
from store import Store,digest
from test_support import isolate_storage


def panel(groups,total=None):
    rows=[dict(key=key,label=name,rows=[dict(url=url+'/-') for url in urls]) for key,name,urls in groups]
    shown=sum(len(g['rows']) for g in rows)
    return dict(state=dict(kind='rows',groups=rows,total=shown if total is None else total,shown=shown))


class LinearProjectsTests(unittest.TestCase):
    def test_grouping_uses_workspace_links_and_tracks_project_moves_and_removals(self):
        isolate_storage(self)
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'work.db')
            self.addCleanup(s.close)
            base='https://linear.app/example/issue/'
            a,b=base+'ENG-1',base+'ENG-2'
            for key,url in [('ENG-1',a),('ENG-2',b)]:
                s.ingest('linear',[dict(external_id=key,title=key,url=url,text=b)])
            s.ingest('bb',[dict(external_id='thr_shared',title='Review',text=a+' '+b)])
            work=digest('bb','thr_shared')
            s.save_intent(work,0,'Review',project='Manual')
            def paged(method,request):
                if method=='facets':return {'states':[{'id':'active'}]}
                if 'stateIds' not in request['filters']:return panel([('one','Alpha',[a])],total=2)
                self.assertEqual(request['filters']['stateIds'],['active'])
                return panel([('two','Beta',[b]),('other','Incorrect',['https://linear.app/unrelated/issue/ENG-2'])])
            refresh_projects(s,paged)
            self.assertEqual(s.work_detail(work)['work']['project'],'Alpha · Beta')
            self.assertEqual(s.work_detail(digest('linear','ENG-1'))['work']['project'],'Alpha')
            self.assertEqual(len(s.work_index('Beta')['items']),2)
            with self.assertRaises(RuntimeError):refresh_projects(s,lambda *_:dict(state=dict(kind='loading')))
            self.assertEqual(s.work_detail(work)['work']['project'],'Alpha · Beta')
            refresh_projects(s,lambda *_:panel([('one','Renamed',[b]),('no-project','No project',[a])]))
            self.assertEqual(s.work_detail(work)['work']['project'],'Renamed')
            refresh_projects(s,lambda *_:panel([('no-project','No project',[a,b])]))
            self.assertEqual(s.work_detail(work)['work']['project'],'Manual')
