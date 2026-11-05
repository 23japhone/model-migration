#!/usr/bin/env python3
"""将完整迁移的有效基线交接为三份下游配置；不执行性能调优。"""
import sys

sys.dont_write_bytecode = True

from tuning_handoff.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
