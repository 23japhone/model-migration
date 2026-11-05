"""AISBench 子任务路径引号适配，不改评测或生成逻辑。"""
import os
import shlex
import sys


def task_command(self,cfg_path,template):
    sys.path.append(os.getcwd())
    argv=[sys.executable,'-I','-B',self._precision_task_script,str(cfg_path)]
    return template.format(task_cmd=shlex.join(argv))


def install():
    from ais_bench.benchmark.tasks import openicl_api_infer,openicl_eval
    for module,kind in ((openicl_api_infer,openicl_api_infer.OpenICLApiInferTask),
                        (openicl_eval,openicl_eval.OpenICLEvalTask)):
        kind._precision_task_script=module.__file__
        kind.get_command=task_command


# multiprocessing spawn 重新导入入口时也安装路径适配；不重复启动 CLI。
if __name__ in ('__main__','__mp_main__'):
    install()
    if __name__=='__main__':
        from ais_bench.benchmark.cli.main import main
        main()
