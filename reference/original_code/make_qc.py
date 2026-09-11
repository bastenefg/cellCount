"""Numbered detections over display-only composites; original TIFFs unchanged."""
from pathlib import Path
import argparse
from PIL import Image,ImageDraw,ImageFont
import pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parent.parent
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);a=p.parse_args()
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',12)
obj=pd.read_csv(ROOT/'data'/'ebfp_objects.csv')
for s,df in obj.groupby('sample'):
 channels={c:np.asarray(Image.open(a.source/f'CHO-{s}-{c}.tif'),dtype=float) for c in ['live','dead','ebfp']}
 rgb=np.stack([np.clip(channels['dead']/30,0,1),np.clip(channels['live']/120,0,1),np.clip(channels['ebfp']/34,0,1)],axis=2)
 base=Image.fromarray(np.uint8(rgb*255)).resize((2048,2048),Image.Resampling.NEAREST)
 im=Image.new('RGB',(2048,2118),'white');im.paste(base,(0,70));d=ImageDraw.Draw(im)
 d.text((12,12),f'{s}  Numbered object detections on original-data display composite',fill='black',font=font)
 d.text((12,34),'Green circle = live only; red = dead only; amber = double positive; cyan outer ring = detectable EBFP. IDs match ebfp_objects.csv.',fill='black',font=font)
 for r in df.itertuples():
  x,y=r.x*2,r.y*2+70;color={'live_only':'#00ff70','dead_only':'#ff6050','double_positive':'#ffd34d'}[r.status]
  d.ellipse((x-9,y-9,x+9,y+9),outline=color,width=1)
  if r.ebfp_detected:d.ellipse((x-12,y-12,x+12,y+12),outline='#30e8ff',width=1)
  d.text((x+10,y-9),str(r.object_id),fill=color,font=font)
 im.save(ROOT/'qc'/f'{s}_numbered_detections.png')
