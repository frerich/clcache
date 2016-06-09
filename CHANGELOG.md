clcache changelog
=================

## Upcoming release

  * Nothing yet.

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
