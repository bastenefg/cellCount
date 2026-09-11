"""Verify experimental-unit aggregation and missing-measurement denominators."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,unittest
import numpy as np,pandas as pd
from pipeline.reporting import summarize,count_summary

class ReportingCorrectnessTests(unittest.TestCase):
    def test_pool_fields_before_computing_between_gel_statistics(self):
        rows=[]
        for image,rep,live,total in [('a','gel1',1,10),('b','gel1',9,10),('c','gel2',1,1)]:
            for i in range(total):rows.append({'image_id':image,'replicate_id':rep,'status':'live_only' if i<live else 'dead_only','ebfp_scorable':True,'ebfp_detected':False})
        entries=[{'image_id':'a','replicate_id':'gel1'},{'image_id':'b','replicate_id':'gel1'},{'image_id':'c','replicate_id':'gel2'}]
        config=json.loads((Path(__file__).resolve().parents[1]/'configs/reference_48h.json').read_text())
        with TemporaryDirectory() as tmp:
            images,reps,stats=summarize(pd.DataFrame(rows),entries,config,Path(tmp))
        np.testing.assert_allclose(reps.viability_percent,[50,100])
        self.assertAlmostEqual(stats['viability_percent']['mean'],75)
        self.assertAlmostEqual(stats['viability_percent']['sample_sd'],np.std([50,100],ddof=1))
    def test_unscorable_ebfp_is_reported_without_calling_it_negative(self):
        frame=pd.DataFrame({'status':['live_only','live_only','dead_only'],'ebfp_scorable':[True,False,True],'ebfp_detected':pd.array([True,pd.NA,False],dtype='boolean')})
        result=count_summary(frame)
        self.assertEqual(result['ebfp_all_count'],1);self.assertEqual(result['ebfp_all_scorable'],2);self.assertEqual(result['ebfp_all_percent'],50)
        self.assertEqual(result['ebfp_live_unscorable'],1);self.assertEqual(result['ebfp_live_percent'],100)
        self.assertAlmostEqual(result['viability_percent'],200/3)
