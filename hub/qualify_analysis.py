"""Small semantic gate using synthetic traffic; never substitutes for a real-workload audit."""
from analyze import classify,SYNTHETIC_PROMPT
import json
import time
import argparse
parser=argparse.ArgumentParser()
parser.add_argument("--provider",choices=["claude","codex"])
args=parser.parse_args()
known={'id':'draft-obligation','title':'Send draft to Jamie','owner':'Alex Taylor','direction':'i_owe','state':'open','due_at':None}
cases=[('acknowledgment','Jamie: Thanks for your message. I will take a look next week.',[known]),('fulfillment','Alex Taylor: I sent the draft to Jamie at 10 AM today.',[known]),('proposal','Assistant: You could email Jamie a draft if you decide to pursue this idea.',[])]
for name,body,obligations in cases:
    result,kinds=classify({'title':'Synthetic qualification','source':'gmail','occurred_at':time.time(),'text':body,'obligations':obligations},prompt=SYNTHETIC_PROMPT,provider=args.provider)
    resolved=[o for o in result['obligations'] if o['existing_id']=='draft-obligation' and o['state']=='fulfilled']
    if name=='acknowledgment':assert not resolved,'An acknowledgment must not fulfill a draft promise'
    if name=='fulfillment':assert len(resolved)==1,'Explicit fulfillment must close the known promise'
    if name=='proposal':assert not result['obligations'],'A proposal is not a commitment'
    print(json.dumps({'case':name,'passed':True,'text_only':all(k=='agent_message' for k in kinds)}),flush=True)
