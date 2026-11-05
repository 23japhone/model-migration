#!/usr/bin/env python3
"""客户部署观察、容量与精确容器替换入口。"""
import sys
sys.dont_write_bytecode = True
from verification.customer.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
