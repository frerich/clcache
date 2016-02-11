clcache changelog
=================

## Upcoming release

 * Bugfix: Support use of py2exe with Python 3.4

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

### Implementation details

 * Setup CI system
 * Add some basic tests
