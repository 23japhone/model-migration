#!/usr/bin/env python3
"""服务链路三步定界公开入口（three-step-demarcation skill）。"""
import sys

sys.dont_write_bytecode = True

from demarcation.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
