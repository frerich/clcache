#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# The contents of this file are subject to the BSD 3-Clause License, the
# full text of which is available in the accompanying LICENSE file at the
# root directory of this project.
#
import os
import fnmatch
import pstats

stats = pstats.Stats()

for basedir, _, filenames in os.walk(os.getcwd()):
    for filename in filenames:
        if fnmatch.fnmatch(filename, 'clcache-*.prof'):
            path = os.path.join(basedir, filename)
            print('Reading {}...'.format(path))
            stats.add(path)

stats.strip_dirs()
stats.sort_stats('cumulative')
stats.print_stats()
stats.print_callers()

