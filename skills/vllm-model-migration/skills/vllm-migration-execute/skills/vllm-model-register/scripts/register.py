#!/usr/bin/env python3
"""注册已有模型实现，并输出可复用的只读复验请求。"""
import sys

sys.dont_write_bytecode = True
from registration.cli import main

if __name__ == '__main__':
    raise SystemExit(main('register'))
