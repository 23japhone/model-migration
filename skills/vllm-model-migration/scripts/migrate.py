#!/usr/bin/env python3
"""模型迁移总入口；提供静态计划与三个阶段的确定性编排。"""
import sys

sys.dont_write_bytecode = True

from coordination.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
