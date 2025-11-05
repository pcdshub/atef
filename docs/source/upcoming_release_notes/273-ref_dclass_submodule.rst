273 ref_dclass_submodule
########################

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
- Move dataclasses into dedicated submodule: config_model

    - Adjust "config" -> "passive", "procedure" -> "active", to better reflect the distinction between the two
    - Adds helper functions to "tree_manipulation" sub-sub-module (naming things is hard)
    - Adjusts the AnyDataclass type hint, apparently Pylance only picks up __dataclass_fields_ as a ClassVar

Contributors
------------
- tangkong
