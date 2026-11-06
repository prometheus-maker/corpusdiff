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


def lines(path):
    text = open(path).read()
    return {"n": len(text), "text": text.upper() + "\n"}


def hungry(path):
    if "big" in open(path).read():
        block = bytearray(400 * 2**20)  # touched, so it is resident
        for i in range(0, len(block), 4096):
            block[i] = 1
        import time

        time.sleep(5)
    return "ok"


def scored(path):
    """Reports a number about its own output: stored per file as row["metrics"]."""
    text = open(path).read()
    return {"text": text, "metrics": {"chars": len(text), "flag": True, "name": "x"}}
