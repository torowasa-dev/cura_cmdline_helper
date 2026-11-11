"""Private, per-run bridge for cura_headless_reslice.py."""

import os


def getMetaData():
    return {}


def register(app):
    if not os.environ.get("CURA_HEADLESS_RESLICE_JOB"):
        return {}
    from .HeadlessResliceBridge import HeadlessResliceBridge
    return {"extension": HeadlessResliceBridge(app)}
