#!/usr/bin/env python3
"""EvalScope 默认精度测试公开入口，兼容 AISBench 和旧精度参数。"""
import sys

sys.dont_write_bytecode = True

from precision.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
