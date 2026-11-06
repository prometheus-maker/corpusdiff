"""Toy targets for the tests: each takes a file path."""

import ctypes
import os
import time


def upper(path):
    return open(path).read().upper()


def picky(path):
    text = open(path).read()
    if "bad" in text:
        raise ValueError("found bad")
    if "slow" in text:
        time.sleep(30)
    if "boom" in text:
        ctypes.string_at(0)  # segfault
    if "exit" in text:
        os._exit(3)
    print("noise that must not break the protocol")
    return {"n": len(text)}


def empty(path):
    return ""
