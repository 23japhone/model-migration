#!/usr/bin/env python3
"""vLLM-Ascend部署与真实功能验证的独立入口。"""
import sys

sys.dont_write_bytecode = True
from verification.cli import main

if __name__ == '__main__':raise SystemExit(main())
