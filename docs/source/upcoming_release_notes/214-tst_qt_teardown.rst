214 tst_qt_teardown
#################

API Breaks
----------
- N/A

Features
--------
- N/A

Bugfixes
--------
- N/A

Maintenance
-----------
- Replaces use of functools.partial with `WeakPartialMethodSlot` in qt slots, cleaning up intermittent test suite failures (and hopefully production crashes)

Contributors
------------
- tangkong
