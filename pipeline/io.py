"""Validated scalar TIFF input and auditable configuration. Never writes source images."""
from pathlib import Path
import csv, hashlib, json, math, re, platform, importlib, sys
import numpy as np
from PIL import Image


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def json_write(path, value):
    def clean(v):
        if isinstance(v,dict): return {str(k):clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)): return [clean(x) for x in v]
        if isinstance(v,np.generic): v=v.item()
        if isinstance(v,float) and not math.isfinite(v): return None
        return v
    Path(path).write_text(json.dumps(clean(value),indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')


def validate_config(c):
    if c.get('schema_version')!=1: raise ValueError('Unsupported config schema_version')
    if c.get('pipeline_version') not in ['1.0.0','1.1.0']: raise ValueError('Config pipeline_version must be compatible with 1.0.0 or 1.1.0')
    if c['input']['dtype'] not in ['uint8','uint16']: raise ValueError('Input dtype must be uint8 or uint16; no automatic intensity conversion.')
    shape=c['input']['expected_shape']
    if len(shape)!=2 or any(not isinstance(x,int) or x<2 for x in shape): raise ValueError('expected_shape must contain height,width >=2.')
    px=c['input']['pixel_size_um']
    if len(px)!=2 or any(not np.isfinite(x) or x<=0 for x in px): raise ValueError('pixel_size_um must contain positive y,x pitch.')
    s=c['segmentation']
    for ch in ['green','red']:
        p=s[ch]
        if not (0<p['low']<=p['high']) or p['sigma_px']<=0 or not isinstance(p['min_area_px'],int) or p['min_area_px']<1: raise ValueError('Invalid segmentation settings for '+ch)
    if s['background_sigma_px']<=0 or s['min_peak_distance_px']<1 or not isinstance(s['peak_window_px'],int) or s['peak_window_px']<3 or s['peak_window_px']%2!=1: raise ValueError('Invalid peak/background settings.')
    if not isinstance(s['exclude_border'],bool): raise ValueError('exclude_border must be true or false.')
    m=c['matching']
    if m['max_distance_px']<=0 or m['dilation_px'] not in [0,1] or m['overlap_window_radius_px']<m['max_distance_px']+m['dilation_px']: raise ValueError('Invalid matching settings; dilation supports 0 or 1 pixel.')
    e=c['ebfp']
    if not (0<e['q_threshold']<1) or not (0<e['null_inner_px']<e['null_outer_px']) or e['min_null_locations']<1: raise ValueError('Invalid EBFP settings.')
    for k in ['roi_dilation_px','occupied_dilation_px']:
        if not isinstance(e[k],int) or e[k]<0: raise ValueError(k+' must be a nonnegative integer.')
    if e['q_scope']!='all_objects_in_this_run': raise ValueError('Only explicit pooled run-level EBFP q correction is supported.')
    for ch in ['green','red','ebfp']:
        low,high=c['display'][ch]
        if not high>low: raise ValueError('Invalid display bounds for '+ch)
    if not 0<c['display']['central_crop_fraction']<=1: raise ValueError('Invalid crop fraction.')
    if c['display']['scale_bar_um']<=0: raise ValueError('Scale bar must be positive.')
    if any(x<=0 for x in c['sensitivity']['segmentation_factors']): raise ValueError('Threshold factors must be positive.')
    if c['replicates']['aggregation']!='pool_nonoverlapping_fields_within_replicate_then_unweighted_replicate_mean_and_sample_sd': raise ValueError('Unsupported replicate aggregation.')
    return c


def read_scalar(path, config):
    with Image.open(path) as im:
        if getattr(im,'n_frames',1)!=1: raise ValueError(f'{path}: one focal plane per TIFF required; do not silently project a stack.')
        arr=np.asarray(im).copy()
        if arr.ndim!=2: raise ValueError(f'{path}: scalar 2D TIFF required; RGB composites are unsupported.')
        if str(arr.dtype)!=config['input']['dtype']: raise ValueError(f'{path}: dtype {arr.dtype} differs from configured {config["input"]["dtype"]}. No rescaling was performed.')
        if list(arr.shape)!=config['input']['expected_shape']: raise ValueError(f'{path}: shape {arr.shape} differs from configuration. Confirm pixel calibration before adapting parameters.')
        if im.mode=='P':
            pal=np.asarray(im.getpalette()).reshape(-1,3)
            index=np.arange(len(pal))
            mono=all(np.array_equal(pal[:,k],index) for k in range(3))
            single=any(np.array_equal(pal[:,k],index) and np.all(np.delete(pal,k,axis=1)==0) for k in range(3))
            if not (mono or single): raise ValueError(f'{path}: nonidentity indexed-color palette is not a validated scalar intensity mapping.')
    return arr.astype(float)


def load_manifest(path, config):
    path=Path(path).resolve()
    with path.open(newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f);required={'image_id','replicate_id','green','red'}
        if not required.issubset(reader.fieldnames or []): raise ValueError('Manifest requires columns: '+', '.join(sorted(required)))
        rows=list(reader)
    if not rows: raise ValueError('The input manifest has no images.')
    ids=set(); used=set(); result=[];files=[]
    for r in rows:
        for key in ['image_id','replicate_id']:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',r[key] or '') or r[key] in ['.','..']: raise ValueError(f'Invalid {key}: {r[key]!r}')
        if r['image_id'] in ids: raise ValueError('Duplicate image_id: '+r['image_id'])
        ids.add(r['image_id']);entry={'image_id':r['image_id'],'replicate_id':r['replicate_id']}
        for ch in ['green','red','ebfp']:
            value=(r.get(ch) or '').strip()
            if not value:
                if ch=='ebfp':
                    entry[ch]=None
                    continue
                raise ValueError(f'{r["image_id"]}: missing {ch} channel.')
            p=(path.parent/value).resolve()
            if not p.is_file(): raise FileNotFoundError(p)
            if p in used: raise ValueError('A channel file is listed more than once: '+str(p))
            used.add(p);entry[ch]=p
            arr=read_scalar(p,config)
            files.append({'image_id':r['image_id'],'replicate_id':r['replicate_id'],'channel':ch,'path':str(p),'filename':p.name,'sha256':sha256(p),'bytes':p.stat().st_size,'shape':list(arr.shape),'dtype':config['input']['dtype']})
        result.append(entry)
    return result,files


def environment():
    return {'python':sys.version,'platform':platform.platform(),**{name:importlib.import_module(name).__version__ for name in ['numpy','scipy','pandas','PIL','matplotlib']}}
