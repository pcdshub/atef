178 tst_imp_cov
###############

API Changes
-----------
- N/A

Features
--------
- N/A

Bugfixes
--------
- fixes more NoneType handling bugs during report generation.
- only subscribe the close-tab function once.
- disconnect update_value slots in ActionRowWidget, preventing them from piling up whenever signal type changes.

Maintenance
-----------
- fleshes out the test suite, adding fixtures where appropriate.
- display enum strings in SetValueStep run view.

Contributors
------------
- tangkong
