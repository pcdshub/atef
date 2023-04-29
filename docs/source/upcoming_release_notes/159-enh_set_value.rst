159 enh_set_value
#################

API Changes
-----------
- N/A

Features
--------
- Adds SetValueStep tothe active checkout suite, allowing for a list of actions to be taken (setting values to targets), followed by a list of checks (Comparisons) for verifying the actions succeeded.
- Adds a ``TableWidgetWithAddRow``, a subclass of ``QTableWidget`` that includes a AddRowWidget. This add row contains a button for adding rows of a specified widget. (for better space efficiency)
- Adds GUI support for placing a ``Comparison`` within a ``ProcedureStep``
- Adds a busy cursor Thread worker (disables click interaction and changes to a wait cursor while a function runs) and a busy cursor decorator (not recommended, but necessary when wrapping slots that create widgets)

Bugfixes
--------
- fixes type hint parsing in ``QDataclassBridge`` for Optional type hints.
- carefully unsubscribes callbacks that might persist after toggling between run and edit mode, avoiding slots from referencing deleted RunTree widgets

Maintenance
-----------
- N/A

Contributors
------------
- tangkong
