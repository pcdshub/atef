224 perf_lazy_page
##################

API Breaks
----------
- N/A

Features
--------
- Adds page widget cache and lazy loading functionality to the atef config GUI

Bugfixes
--------
- N/A

Maintenance
-----------
- Refactors GUI backend to support lazy page loading
- Move tree-building logic to dataclasses
- Consolidate GUI backend classes (EditTree/RunTree -> DualTree, AtefItem/TreeItem -> TreeItem)

Contributors
------------
- tangkong
