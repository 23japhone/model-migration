#!/usr/bin/env python3
"""性能启动前只读核验 EvalScope 交接；不安装或运行压测。"""
import sys

sys.dont_write_bytecode = True

from customer_workflow.evaluation.handoff_cli import main

if __name__ == '__main__':
    raise SystemExit(main())
