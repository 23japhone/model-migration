#!/usr/bin/env python3
"""复验目标载体的实际注册，不修改源码、plugin 或模型资料。"""
import sys

sys.dont_write_bytecode = True
from registration.cli import main

if __name__ == '__main__':
    raise SystemExit(main('check'))
