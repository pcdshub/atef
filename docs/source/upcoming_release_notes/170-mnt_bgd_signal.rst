170 mnt_bgd_signal
#################

API Changes
-----------
- N/A

Features
--------
- N/A

Bugfixes
--------
- ``BusyCursorThread.raised_exception`` now properly expects to emit an ``Exception``

Maintenance
-----------
- places a stray sig.wait_for_connection call into a ``BusyCursorThread``

Contributors
------------
- tangkong
