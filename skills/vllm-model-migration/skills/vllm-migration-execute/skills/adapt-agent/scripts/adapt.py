"""模型实现适配的独立公开入口。"""
import sys

sys.dont_write_bytecode = True
from adaptation.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
