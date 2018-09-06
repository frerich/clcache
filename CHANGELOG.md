clcache changelog
=================

## clcache 4.2.0 (2018-09-06)

 * Feature: Enable installation directly from GitHub via 'pip install'
 * Bugfix: Fixed potential corruption of cache in case clcache is terminated
   while writing a cache entry or updating the cache statistics.
 * Internal: The automatic clcache tests are now executed with Python 3.6, too.
 * Internal: Support for Python 3.3 and Python 3.4 has been dropped.
 * Bugfix: Fixed invoking the real compiler when using the `/Tc` or `/Tp`
   switches (GH #289)
 * Feature: Allow specifying just a file name via `CLCACHE_CL`, the absolute
   path will be computed automatically by scanning the `PATH`.
 * Bugfix: clcache correctly honours termination signals such as Ctrl+C again
   (GH #261).
 * Bugfix: Fixed some cases in which clcache might have written corrupted cache
   entries in case it gets terminated in the middle of copying a file (GH #263).
 * Feature: Added 'monkey' script to simplify integrating clcache with distutils-based
   workflows (GH #284).
 * Feature: Drop version in directory structure of chocolatey (nuget) target
   package (GH #318).

## clcache 4.1.0 (2017-05-23)

 * Bugfix: Fixed resource leak in clcachesrv.
 * Improvement: clcachesrv now shuts down cleanly when receiving SIGTERM.
 * Feature: clcachesrv now supports blacklisting directories to watch for file
   changes; this avoids problems with deleting such directories because clcachesrv
   still has them opened (in order to get notified to changes to header files).
 * Internal: The clcache source code now passes the checks performed by pylint 1.7.1
 * Documentation: Installation & integration instructions have been moved from
   the README file, into the clcache wiki at https://github.com/frerich/clcache/wiki
 * Feature: clcache now supports using a [memcached](https://memcached.org/)
   setup as the storage mechanism in addition to storing cached files in the file
   system.

## clcache 4.0.0 (2016-12-15)

 * Bugfix: Fixed occasional 'Cannot create a file when that file already
   exists' error when adding new objects to the cache (GH #155).
 * Improvement: Extended handling of CLCACHE_BASEDIR variable to improve cache
   hit rates when using different build directories.
 * Improvement: Improved interaction between concurrent clcache invocations
   when encountering multiple cache misses in parallel; the risk of triggering
   timeout exceptions is greatly reduced (GH #229).
 * Bugfix: Improved error handling in case statistics files or manifest files
   in the cache are malformed, which could happen when clcache got terminated
   abnormally.
 * Bugfix: Fixed off-by-one error when compiling source files concurrently,
   clcache would always launch one concurrent instance more than intended.
 * Feature: Added support for a 'CLCACHE_SERVER' environment variable which, when
   set to '1', makes 'clcache' use a separate 'clcachesrv' server process to cache
   hash sums. This improves performance, especially so for warm caches.
 * Improvement: Improved performance when invoking clcache with multiple source
   files; it will no longer launch itself recursively but rather use multiple
   threads to process the individual source files.
 * Internal: clcache now uses semantic versioning.

## clcache 3.3.1 (2016-10-25)

 * Bugfix: Aborting clcache via Ctrl+C or SIGTERM will no longer have the risk
   of leaving the cache in a defective state.
 * Internal: Fixed running integration tests when the clcache source code is
   stored in a path with spaces (GH #206).
 * Improvement: Optimized communicating with real compiler.
 * Bugfix: Fixed a potential CacheLockException apparently caused by
   a race condition between calling CreateMutexW() and
   WaitForSingleObject().

## clcache 3.3.0 (2016-09-07)

 * Bugfix: /MP no longer causes a greatly reduced cache hit rate.
 * Bugfix: In direct mode, clcache will no longer try to access non-existant
   header files (GH #200, GH #209).
 * Bugfix: Correctly cache and restore stdout/stderr output of compiler when
   building via the Visual Studio IDE.
 * Add support for the `CL` and `_CL_` environment variables (GH #196).
 * Feature: A new `CLCACHE_PROFILE` environment variable is now recognised
   which can be used to make clcache generate profiling information. The
   generated data can be processed into a final report using a new
   `showprofilereport.py` script.
 * Improvement: Timeout errors when accessing the cache now generate friendlier
   error messages mentioning the possibility to work around the issue using the
   `CLCACHE_OBJECT_CACHE_TIMEOUT_MS` environment variable.
 * Improvement: Greatly improved concurrency of clcache such that concurrent
   invocations of the tool no longer block each other.
 * Improvement: Improve hit rate when alternating between two identical
   versions of the same source file that transitively get different contents of
   the included files (a common case when switching back and forth between
   branches).

## clcache 3.2.0 (2016-07-28)

 * Bugfix: When preprocessing was used together with an /Fo argument (which makes
   no sense), the handling was wrong.
 * Bugfix: Properly handle /Fi arguments
 * Bugfix: Fixed printing cached compiler output when using Python 2.x.
 * Dropped support for caching preprocessor invocations. The number of such
   invocations is now printed in the statistics (`ccache -s`).
 * Bugfix: In MSVS, arguments use the formats `/NAMEparameter` (no space, required value),
   `/NAME[parameter]` (no space, optional value), `/NAME[ ]parameter` (optional space),
   and `/NAME parameter` (required space). Before we always tried `/NAMEparameter`
   and if there if no parameter, tried `/NAME parameter`. This strategy was too simple
   and failed for like e.g. `/Fo`, which must not consume the following argument when
   no parameter is set.
 * Rework manifests: use JSON to store manifests. This makes all existing manifests
   invalid. Cleaning and clearing now removes old manifest files as well, so the old
   .dat files are automatically removed.
 * clcache now requires Python 3.3 or newer.
 * py2exe support was dropped, PyInstaller is now recommended for generating
   .exe files.

## clcache 3.1.1 (2016-06-25)

 * Improvement: better protection against storing corrupt objects in the cache
   in case clcache is terminated in the middle of storing a new cache entry.
 * Improvement: The README now explains an approach to making Visual Studio
   pick up clcache.
 * Bugfix: Command files with multiple lines are now handled correctly.
 * Bugfix: Command files which contribute arguments ending in a backslash are
   now parsed correctly (GH #108).
 * Bugfix: Properly handle non-ASCII compiler output (GH #64)

## clcache 3.1.0 (2016-06-09)

 * Cached objects are no longer shared between different clcache versions to
   avoid restoring objects which were stored incorrectly in older clcache
   versions.
 * Feature: The cache statistics now count the number of calls with /Zi (which
   causes debug information to be stored in separate `.pdb` files)
 * Feature: a new `-c` switch is now recognized which can be used to clean the
   cache. Cleaning the cache means trimming the cache to 90% of it's maximum
   size by deleting the oldest object. Doing this explicitly avoids that this
   happens automatically during a build.
 * Feature: a new `-C` switch was added which can be used to clear the cache.
   Clearing the cache removes all cached objects, but keeps all hit/miss
   statistics.
 * Improvement: The `ccache -s` output is now prettier.
 * Improvement: cleaning outdated cache entries is now a lot faster
 * Bugfix: Support use of py2exe with Python 3.4
 * Bugfix: Support includes parsing when cl language is not English (GH #65)
 * Bugfix: Fix bug causing statistics to get corrupted after concurrent invocations (GH #70).
 * Bugfix: Invalid values passed via `-M` are now handle gracefully.
 * Bugfix: The cache size and the number of cached entries is now updated
   correctly after pruning outdated objects.
 * Internal: major overhaul to the test infrastructure: a lot more tests are
   now executed on each change, and they are executed automatically via
   AppVeyor.
 * Internal: a lot of issues (both cosmetic as well as semantic) reported by
   pylint have been fixed. pylint is now also executed for each change to the
   codebase via AppVeyor.

## clcache 3.0.2 (2016-01-29)

 * Python 3 compatibility
 * Add new env variable to control clcache lock timeout
 * Bugfix: Fix recompile bug in direct mode when header changed
 * Bugfix: Fix compile error when clcache is used with precompiled headers
 * Bugfix: `/MP[processMax]` was not properly parsed when `processMax` was unset
 * Bugfix: Properly handle arguments including `\"` (e.g. `/Fo"Release\"`)
 * Bugfix: Ensure the destination folder exists when copying object
 * Bugfix: Fix crash when using CLCACHE_NODIRECT by restoring missing argument
 * Bugfix: Fix fork-bomb when py2exe is used

**Implementation details**

 * Setup CI system
 * Add some basic tests
