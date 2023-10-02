213 mnt_bug_misc_fixes
######################

API Breaks
----------
- N/A

Features
--------
- N/A

Bugfixes
--------
- `RangeWidget`'s visualizations update a bit more frequently, and also the label text actually updates. Closes #212
- Adds a menu option to open the welcome tab, since people like it.  Closes #201
- Properly shows an error message box when a file can't be opened.  Closes #202
- Removes child `AtefItem` from a ProcedureStep when it's changed from the specific-step-combobox.  Closes #195
- Allow tolerances to be `None` in `Equals` comparison.  Modifies the line-edit setup to allow null values (`''`, `None`) when casting the line edit value.  Closes #128


Maintenance
-----------
- N/A

Contributors
------------
- N/A
