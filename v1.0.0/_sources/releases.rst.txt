Release History
###############


v1.0.0 (2023-06-22)
========================
Many changes have taken place since the last tag (08/2022).  Checkouts can now
be run inside the GUI, and active checkouts have been prototyped.

Notably the structure of the checkout files changed, and checkouts before that
tag must be converted to the modern format.  Most users will not have issues
with this.

Shoutout to all the contributors who helped before the pre-release notes framework
was added.

Features
--------
- Replaces the welcome dialog with a welcome landing tab
- Enable the close-tab button
- adds run and edit widgets for ``PassiveStep``, a step that allows passive checkouts to be run as a component of an active checkout
- Adds Enum support to the ``SetValueStep``'s actions
- Adds SetValueStep tothe active checkout suite, allowing for a list of actions to be taken (setting values to targets), followed by a list of checks (Comparisons) for verifying the actions succeeded.
- Adds a ``TableWidgetWithAddRow``, a subclass of ``QTableWidget`` that includes a AddRowWidget. This add row contains a button for adding rows of a specified widget. (for better space efficiency)
- Adds GUI support for placing a ``Comparison`` within a ``ProcedureStep``
- Adds a busy cursor Thread worker (disables click interaction and changes to a wait cursor while a function runs) and a busy cursor decorator (not recommended, but necessary when wrapping slots that create widgets)
- Adds report generation for active checkouts

Bugfixes
--------
- Fixes a bug where False-y observed values would fail to be reported
- ``BusyCursorThread.raised_exception`` now properly expects to emit an ``Exception``
- fixes more NoneType handling bugs during report generation.
- only subscribe the close-tab function once.
- disconnect update_value slots in ``ActionRowWidget``, preventing them from piling up whenever signal type changes.
- Fixes optional type hint handling in ``QDataclassBridge`` (again)
- Improve missing field handling in report generation
- fixes type hint parsing in ``QDataclassBridge`` for Optional type hints.
- carefully unsubscribes callbacks that might persist after toggling between run and edit mode, avoiding slots from referencing deleted RunTree widgets
- Cast values read from the config to a string in AnyValue widget
- Properly identify up Sequences in ``QDataclassBridge``
- Sets the comparison widget type based on the loaded datatype
- Allows device selection via double-click in the ``HappiSearchWidget`` tree-view

Maintenance
-----------
- Improves ``ResultStatus`` refresh handling, now also updates on paint events
- In the case of errors during a mode switch, the error will be revealed to the user and the switch will be reverted.
- Improve result icon refresh behavior by emitting a sigal whenever a step is run.
- Add result property to passive checkout configurations in order to re-compute the overall_result when .result is requested.
- places a stray sig.wait_for_connection call into a ``BusyCursorThread``
- fleshes out the test suite, adding fixtures where appropriate.
- display enum strings in ``SetValueStep`` run view.
- Differentiates between read and write (set) PV's in ``OphydDeviceTableView``
- Wraps signal.get call used for setting input type validators in ``BusyCursorThread``

Contributors
------------
- tangkong
