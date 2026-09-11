"""Deterministic command-line runs, scientific sensitivity, and reference checks."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,csv,itertools,json,sys,traceback
import numpy as np,pandas as pd
from . import __version__
from .core import segment,combine,measure_ebfp,bh,EBFP_COLUMNS
from .io import load_manifest,validate_config,sha256,json_write,read_scalar,environment
from .reporting import summarize,count_summary,figures

ROOT=Path(__file__).resolve().parent.parent


def measure_optional_ebfp(blue,labels,objects,sample,config,dilation=None,shift=(0,0)):
    """An absent acquisition is unknown, not an image containing zero intensity."""
    if blue is not None:
        result=measure_ebfp(blue,labels,objects,sample,config,dilation=dilation,shift=shift)
        result['ebfp_channel_provided']=True
        return result
    result=objects.copy()
    for column in EBFP_COLUMNS:
        result[column]=np.nan
    result['sample']=sample
    result['ebfp_scorable']=False
    result['ebfp_status']='channel_not_provided'
    result['ebfp_channel_provided']=False
    return result


def classify(rows,config):
    rows=rows.copy();rows['ebfp_q_pooled']=bh(rows.ebfp_p.to_numpy(float));rows['ebfp_q_sample']=np.nan
    for _,ix in rows.groupby('sample',sort=False).groups.items():rows.loc[ix,'ebfp_q_sample']=bh(rows.loc[ix,'ebfp_p'].to_numpy(float))
    detection=pd.Series(pd.NA,index=rows.index,dtype='boolean');ok=rows.ebfp_scorable.fillna(False).astype(bool)
    detection.loc[ok]=rows.loc[ok,'ebfp_q_pooled']<=config['ebfp']['q_threshold'];rows['ebfp_detected']=detection
    return rows


def scientific_sensitivity(entries,cache,objects,config,out,extended):
    records=[];factors=config['sensitivity']['segmentation_factors']
    for e in entries:
        s=e['image_id'];sets={}
        for ch,f in itertools.product(['green','red'],factors):
            sets[ch,f]=segment(cache[s][ch],ch,config,f)
        for fg,fr in itertools.product(factors,repeat=2):
            lg,g,_=sets['green',fg];lr,r,_=sets['red',fr];_,d=combine(g,r,lg,lr,config)
            n=len(d);nl=int(d.status.eq('live_only').sum());nd=int(d.status.eq('dead_only').sum());nb=int(d.status.eq('double_positive').sum())
            records.append({'image_id':s,'replicate_id':e['replicate_id'],'green_threshold_factor':fg,'red_threshold_factor':fr,'live_only':nl,'dead_only':nd,'double_positive':nb,'total':n,'viability_percent':100*nl/n if n else np.nan})
    sensitivity=pd.DataFrame(records);sensitivity.to_csv(out/'threshold_sensitivity.csv',index=False)
    rep=sensitivity.groupby(['green_threshold_factor','red_threshold_factor','replicate_id'],sort=False)[['live_only','dead_only','double_positive','total']].sum().reset_index();rep['viability_percent']=np.where(rep.total>0,100*rep.live_only/rep.total,np.nan);rep.to_csv(out/'threshold_sensitivity_by_replicate.csv',index=False)
    aggregate=rep.groupby(['green_threshold_factor','red_threshold_factor']).viability_percent.agg(['mean','std','count']).reset_index();aggregate.to_csv(out/'threshold_sensitivity_aggregate.csv',index=False)
    records=[]
    for q in config['sensitivity']['ebfp_q_thresholds']:
        for sample,d in objects.groupby('image_id',sort=False):
            sc=d.ebfp_scorable.fillna(False).astype(bool);live=d.status.eq('live_only');positive=d.ebfp_q_pooled<=q
            records.append({'image_id':sample,'q_threshold':q,'ebfp_all_count':int((positive&sc).sum()),'ebfp_all_scorable':int(sc.sum()),'ebfp_all_percent':100*(positive&sc).sum()/sc.sum() if sc.sum() else np.nan,'ebfp_live_count':int((positive&sc&live).sum()),'ebfp_live_scorable':int((sc&live).sum()),'ebfp_live_percent':100*(positive&sc&live).sum()/(sc&live).sum() if (sc&live).sum() else np.nan})
    pd.DataFrame(records).to_csv(out/'ebfp_q_sensitivity.csv',index=False)
    if extended and any(e.get('ebfp') is not None for e in entries):
        rows=[]
        for dilation in config['sensitivity']['ebfp_dilation_px']:
            d=pd.concat([measure_optional_ebfp(cache[e['image_id']]['ebfp'],cache[e['image_id']]['labels'],cache[e['image_id']]['objects'],e['image_id'],config,dilation=dilation) for e in entries],ignore_index=True);d=classify(d,config)
            for s,x in d.groupby('sample',sort=False):rows.append({'diagnostic':'roi_dilation','value':str(dilation),'image_id':s,**count_summary(x)})
        shift=tuple(config['sensitivity']['spatial_null_shift_px']);d=pd.concat([measure_optional_ebfp(cache[e['image_id']]['ebfp'],cache[e['image_id']]['labels'],cache[e['image_id']]['objects'],e['image_id'],config,shift=shift) for e in entries],ignore_index=True)
        cutoff=objects.loc[objects.ebfp_detected.fillna(False),'ebfp_p'].max()
        d=classify(d,config);d.loc[d.ebfp_scorable,'ebfp_detected']=d.loc[d.ebfp_scorable,'ebfp_p']<=cutoff if pd.notna(cutoff) else False
        for s,x in d.groupby('sample',sort=False):rows.append({'diagnostic':'blue_spatial_shift_fixed_original_p','value':str(shift),'image_id':s,'fixed_p_cutoff':cutoff,**count_summary(x)})
        pd.DataFrame(rows).to_csv(out/'extended_ebfp_qc.csv',index=False)


def reference_check(out):
    expected=ROOT/'reference'/'expected';new=pd.read_csv(out/'image_summary.csv');old=pd.read_csv(expected/'summary.csv');checks=[]
    for s in ['S5','S6','S7']:
        a=new.set_index('image_id').loc[s];b=old.set_index('sample').loc[s]
        cols=[c for c in old.columns if c in new.columns]
        for c in cols:
            np.testing.assert_allclose(a[c],b[c],atol=1e-10,rtol=1e-12,equal_nan=True,err_msg=f'{s} {c}')
        with np.load(out/'labels'/f'{s}.npz') as actual,np.load(expected/f'{s}_masks.npz') as wanted:
            for c in ['live','dead','objects']:np.testing.assert_array_equal(actual[c],wanted[c],err_msg=s+' '+c)
        checks.append({'image_id':s,'exact_masks':['live','dead','objects'],'summary_columns_checked':cols})
    a=pd.read_csv(out/'objects.csv').sort_values(['sample','object_id']).reset_index(drop=True);b=pd.read_csv(expected/'ebfp_objects.csv').sort_values(['sample','object_id']).reset_index(drop=True)
    if len(a)!=len(b):raise AssertionError('Object count differs from reference.')
    for c in b.columns:
        if pd.api.types.is_numeric_dtype(b[c]):np.testing.assert_allclose(a[c],b[c],atol=1e-12,rtol=1e-12,equal_nan=True,err_msg=c)
        else:np.testing.assert_array_equal(a[c],b[c],err_msg=c)
    n_object_rows=len(a);n_ebfp_calls=int(a.ebfp_detected.sum())
    for s in ['S5','S6','S7']:
        for ch in ['green','red']:
            a=pd.read_csv(out/'channel_detections'/f'{s}_{ch}.csv');b=pd.read_csv(expected/f'{s}_{ch}.csv')
            pd.testing.assert_frame_equal(a[b.columns],b,check_exact=False,rtol=1e-12,atol=1e-12)
    result={'status':'passed','reference_images':checks,'objects_checked':n_object_rows,'ebfp_calls_checked':n_ebfp_calls,'all_legacy_object_columns_match':True,'float_tolerance':{'atol':1e-12,'rtol':1e-12},'rendered_image_bytes_are_not_part_of_numeric_reference_check':True}
    json_write(out/'reference_validation.json',result);return result


def verify_run(out):
    out=Path(out).resolve();meta=json.loads((out/'run_manifest.json').read_text())
    if meta['status']!='completed':raise ValueError('Run did not complete.')
    for item in meta['input_files']:
        if sha256(item['path'])!=item['sha256']:raise ValueError('Input file is missing or changed: '+item['path'])
    for name,digest in meta['code_sha256'].items():
        if sha256(ROOT/name)!=digest:raise ValueError('Pipeline code changed since the run: '+name)
    hashes=json.loads((out/'output_checksums.json').read_text())
    for name,digest in hashes.items():
        if sha256(out/name)!=digest:raise ValueError('Saved output changed: '+name)
    print('Verified unchanged source files, pipeline code, and saved outputs. This check does not re-run the analysis.')


def run_analysis(manifest,config_path,out,extended=False,reference=False):
    manifest=Path(manifest).resolve();config_path=Path(config_path).resolve();out=Path(out).resolve()
    if out.exists():raise FileExistsError('Output directory already exists. Choose a new run directory; results are never silently overwritten: '+str(out))
    config=validate_config(json.loads(config_path.read_text()));entries,files=load_manifest(manifest,config)
    if reference:
        expected={r['file']:r['sha256'] for r in json.loads((ROOT/'reference'/'expected'/'source_manifest.json').read_text())}
        if any(f['sha256']!=expected.get(f['filename']) for f in files):raise ValueError('Bundled reference TIFF checksum mismatch.')
    codepaths=[ROOT/'run_pipeline.py',*sorted((ROOT/'pipeline').glob('*.py'))]
    codehash={str(p.relative_to(ROOT)):sha256(p) for p in codepaths}
    out.mkdir(parents=True);(out/'labels').mkdir();(out/'channel_detections').mkdir()
    json_write(out/'effective_config.json',config)
    with (out/'resolved_samples.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['image_id','replicate_id','green','red','ebfp']);w.writeheader();w.writerows(entries)
    meta={'pipeline_version':__version__,'started_utc':datetime.now(timezone.utc).isoformat(),'status':'running','reference_run':reference,'extended_qc':extended,'input_manifest_sha256':sha256(manifest),'config_sha256':sha256(config_path),'input_files':files,'code_sha256':codehash,'environment':environment(),'replicate_interpretation':config['replicates'],'q_family':'all scorable objects across the complete input manifest for this run','warnings':[]}
    json_write(out/'run_manifest.json',meta)
    try:
        cache={};measurements=[]
        for e in entries:
            s=e['image_id'];print('Analyzing '+s,flush=True);chans={ch:read_scalar(e[ch],config) if e.get(ch) is not None else None for ch in ['green','red','ebfp']}
            lg,g,_=segment(chans['green'],'green',config);lr,r,_=segment(chans['red'],'red',config);labels,df=combine(g,r,lg,lr,config)
            g.to_csv(out/'channel_detections'/f'{s}_green.csv',index=False);r.to_csv(out/'channel_detections'/f'{s}_red.csv',index=False);np.savez_compressed(out/'labels'/f'{s}.npz',live=lg,dead=lr,objects=labels)
            measured=measure_optional_ebfp(chans['ebfp'],labels,df,s,config);measured['image_id']=s;measured['replicate_id']=e['replicate_id'];measurements.append(measured)
            cache[s]={**chans,'labels':labels,'objects':df}
        objects=classify(pd.concat(measurements,ignore_index=True),config);objects.to_csv(out/'objects.csv',index=False)
        image_df,rep_df,stats=summarize(objects,entries,config,out)
        lost=int(objects.mask_lost.sum());overlap=int((objects.mask_pixels_overwritten>0).sum());unscorable=int((~objects.ebfp_scorable.astype(bool)).sum())
        if overlap:meta['warnings'].append(f'{overlap} object masks lose overlapping pixels under the original last-write rule. Inspect mask_pixels_final and mask_pixels_overwritten; legacy behavior retained for reproducibility.')
        if lost:meta['warnings'].append(f'{lost} objects have no retained mask and cannot be scored for EBFP.')
        missing=sum(e.get('ebfp') is None for e in entries)
        meta['ebfp_images_provided']=len(entries)-missing;meta['ebfp_images_not_provided']=missing
        if missing:meta['warnings'].append(f'EBFP was not provided for {missing} field(s). Their EBFP measurements are missing, not negative; live/dead analysis includes these fields.')
        if unscorable:meta['warnings'].append(f'{unscorable} objects have unavailable/unscorable EBFP measurements. They are excluded from EBFP percentage denominators only; counts and reasons are reported separately.')
        if extended and missing==len(entries):meta['warnings'].append('Extended EBFP diagnostics skipped because no EBFP images were provided.')
        if len(rep_df)<2:meta['warnings'].append('Fewer than two replicate groups: between-replicate sample SD is undefined.')
        print('Computing threshold sensitivity'+(' and extended EBFP diagnostics' if extended else ''),flush=True)
        scientific_sensitivity(entries,cache,objects,config,out,extended);figures(entries,objects,image_df,rep_df,stats,config,out)
        for f in files:
            if sha256(f['path'])!=f['sha256']:raise RuntimeError('Source file changed during analysis: '+f['path'])
        if reference:reference_check(out);print('REFERENCE PASSED: exact masks, object classifications, and EBFP calls; numeric measurements within 1e-12.',flush=True)
        meta['status']='completed';meta['finished_utc']=datetime.now(timezone.utc).isoformat();meta['source_checksums_unchanged']=True;json_write(out/'run_manifest.json',meta)
        outputs={str(p.relative_to(out)):sha256(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='output_checksums.json'};json_write(out/'output_checksums.json',outputs)
        print(rep_df.to_string(index=False));print('Saved run: '+str(out))
    except Exception as exc:
        meta['status']='failed';meta['error']=str(exc);meta['finished_utc']=datetime.now(timezone.utc).isoformat();json_write(out/'run_manifest.json',meta);raise


def main():
    parser=argparse.ArgumentParser(description='Reproducible live/dead and EBFP microscopy analysis.');sub=parser.add_subparsers(dest='command',required=True)
    ref=sub.add_parser('reference',help='Reproduce included S5–S7 and compare every object and mask.');ref.add_argument('--output',type=Path,required=True);ref.add_argument('--extended-qc',action='store_true')
    ana=sub.add_parser('analyze',help='Analyze one prespecified experiment/condition from a manifest.');ana.add_argument('--manifest',type=Path,required=True);ana.add_argument('--config',type=Path,required=True);ana.add_argument('--output',type=Path,required=True);ana.add_argument('--extended-qc',action='store_true')
    ver=sub.add_parser('verify',help='Verify provenance and output hashes of a completed run.');ver.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='verify':verify_run(args.run)
        elif args.command=='reference':run_analysis(ROOT/'reference'/'samples.csv',ROOT/'configs'/'reference_48h.json',args.output,args.extended_qc,True)
        else:run_analysis(args.manifest,args.config,args.output,args.extended_qc)
    except Exception as exc:print(f'ERROR: {exc}',file=sys.stderr);return 1
    return 0
