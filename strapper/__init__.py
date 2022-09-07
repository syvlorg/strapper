#!/usr/bin/env python3
import rich.traceback as RichTraceback
RichTraceback.install(show_locals = True)

import hy

hy.macros.require('strapper.strapper',
    # The Python equivalent of `(require strapper.strapper *)`
    None, assignments = 'ALL', prefix = '')
hy.macros.require_reader('strapper.strapper', None, assignments = 'ALL')
from strapper.strapper import *

from addict import Dict

if __name__ == "__main__":
    tailapi(obj=Dict(dict()))
