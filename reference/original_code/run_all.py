"""Reproduce the analysis from the exact original TIFFs, without editing them."""
from pathlib import Path
import argparse,json,hashlib,subprocess,sys
root=Path(__file__).resolve().parent.parent
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);args=p.parse_args();source=args.source.resolve()
manifest=json.loads((root/'data'/'source_manifest.json').read_text())
def verify():
 for item in manifest:
  path=source/item['file'];actual=hashlib.sha256(path.read_bytes()).hexdigest()
  if actual!=item['sha256']:raise ValueError('Source checksum mismatch: '+path.name)
verify()
commands=[['segment.py','--source',str(source)],['sensitivity.py','--source',str(source)],['ebfp_qc.py','--source',str(source),'--masks',str(root/'data'),'--output',str(root/'data')],['summarize.py'],['make_figure.py','--source',str(source)],['make_qc.py','--source',str(source)]]
for command in commands:subprocess.run([sys.executable,str(root/'analysis'/command[0]),*command[1:]],check=True)
verify();print('Analysis complete; original-file checksums unchanged.')
