#!/usr/bin/env python3
"""客户环境和镜像解析公开入口。"""
import sys
sys.dont_write_bytecode = True
from customer_assets.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
