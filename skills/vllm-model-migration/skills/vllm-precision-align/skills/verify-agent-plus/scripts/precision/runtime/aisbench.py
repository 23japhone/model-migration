"""AISBench 适配；版本与配置探测在独立评测解释器内执行。"""
from ..evidence import load, write
from .process import execute as run_process
from .capture import recording
from pathlib import Path
from urllib.parse import urlsplit


def prepare(request,inputs,attempt):
    from pathlib import Path
    payload=attempt/'probe-request.json'
    write(payload,{'request':request,'handoff':inputs['handoff'],'asset':inputs['asset'],
                   'identity':inputs['identity'],'service_snapshot':inputs.get('service_snapshot',{}),'attempt':str(attempt)})
    output=attempt/'config-index.json'
    run_process([request['evaluator']['python'],'-I','-B',str(Path(__file__).with_name('probe.py')),str(payload),str(output)],
                attempt/'configuration',60)
    return load(output)


def execute(request,task,attempt):
    root=Path(task['output_root']);root.mkdir(parents=True,exist_ok=False)
    timeout=request['evaluator'].get('timeout_seconds',14400)
    with recording(task['endpoint'],root/'http-capture',min(timeout,3600)) as endpoint:
        parsed=urlsplit(endpoint)
        config=root/'execution.py'
        script='exec(compile(open('+repr(task['config'])+',encoding="utf-8").read(),'+repr(task['config'])+',"exec"))\n'
        script+='models[0]["host_ip"]='+repr(parsed.hostname)+'\nmodels[0]["host_port"]='+str(parsed.port)+'\n'
        script+='models[0]["url"]=""\n'
        config.write_text(script,encoding='utf-8')
        argv=[request['evaluator']['python'],'-I','-B',str(Path(__file__).with_name('aisbench_cli.py')),str(config),
              '--mode','all','--num-warmups','0','--dump-eval-details','--work-dir',str(root/'aisbench')]
        return run_process(argv,attempt/('process-'+task['dataset']),timeout,check=False)
