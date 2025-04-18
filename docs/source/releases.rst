Release History
###############


v1.5.3 (2025-04-09)
===================

Maintenance
-----------
- Fixes broken tests so they properly exercise the appropriate cli subcommands
- Unpins numpy, now that upstream packages are compatible
- Adjusts fonts to not rely on FontAwesome4 ("fa." prefix), which was deprecated in recent QtAwesome releases

Contributors
------------
- tangkong



v1.5.2 (2024-12-19)
===================

Maintenance
-----------
- Increase the limit on spinboxes generated from MultiInputFormDialog, in response to a request that set-value-step timeouts not be limited to 99
- Navigate BACK to the parent page when creating a new check in SetValueStep, preventing extra navigation clicks
- Improves the performance of the CLI entrypoint, deferring functional imports as long as possible

Contributors
------------
- tangkong



v1.5.1 (2024-09-16)
===================

Bugfixes
--------
- Store the setpoint pv when creating a Target if possible, instead of storing pvname (which could be the readback pv).
  Refactor OphydAttributeData slightly to this end.

Contributors
------------
- tangkong



v1.5.0 (2024-08-20)
===================

Features
--------
- Adds report output option to `atef check` cli tool
- Adds template step active and passive dataclasses, along with report support.
- Adds RegexFindReplace for serializable, regex specific FindReplaceAction
- Adds atef config GUI pages for passive and active templated checkouts
  featuring a clear staging area and tree-view for added clarity

Bugfixes
--------
- Unifies usage of ``create_tree_from_file``

Maintenance
-----------
- Pins numpy<2.0 to avoid issues with upstream incompatibilities
- Refactors find-replace logic and dataclasses into a separate module from the widgets that display them
- Specify bluesky-base and pin matplotlib in conda dependencies to avoid unintended qt6 dependencies

Contributors
------------
- tangkong



v1.4.0 (2024-02-20)
===================

Features
--------
- Adds script for converting pmgr configurations to atef checks.
- Adds `PagedTableWidget`, and applies it to passive checkout group pages to substantially improve the loading performance of group pages with many, many comparisons.

Bugfixes
--------
- Catch RuntimeErrors from widget deletion during enum filling
- Avoid running deleteLater on widgets that garbage collection handles, preventing segfaults

Maintenance
-----------
- Make selection behavior more consistent by using `QTreeView.setCurrentIndex()` instead of manipulating the selection model
- adds `atef scripts` subcommand for invoking existing scripts.  Includes `converter_v0` and `pmgr_check` scripts.

Contributors
------------
- tangkong



v1.3.0 (2023-12-19)
===================

Features
--------
- Adds results summary page accessible from run-mode in the `atef config` GUI
- Adds icons to run-mode tree view
- Adds page widget cache and lazy loading functionality to the atef config GUI

Bugfixes
--------
- :class:`~atef.widgets.config.data_passive.RangeWidget`'s visualizations update a bit more frequently, and also the label text actually updates. Closes #212
- Adds a menu option to open the welcome tab, since people like it.  Closes #201
- Properly shows an error message box when a file can't be opened.  Closes #202
- Allow tolerances to be `None` in `Equals` comparison.  Modifies the line-edit setup to allow null values (`''`, `None`) when casting the line edit value.  Closes #128

Maintenance
-----------
- Make comparisons against enum signals more robust by trying both the int and string versions if the check fails.
- Refactors tree-walking helpers to a separate submodle (`atef.walk`)
- Replaces use of `functools.partial` with `WeakPartialMethodSlot` in qt slots, cleaning up intermittent test suite failures (and hopefully production crashes)
- Refactors GUI backend to support lazy page loading
- Move tree-building logic to dataclasses
- Consolidate GUI backend classes (`EditTree` / `RunTree` -> `DualTree`, `AtefItem` / `TreeItem` -> `TreeItem`)

Contributors
------------
- tangkong



v1.2.0 (2023-09-27)
===================

Features
--------
- Adds :class:`~atef.widgets.config.utils.ScientificDoubleSpinbox` and uses it in MultiModeValueEdit.

Bugfixes
--------
- Waits for signal connection during :class:`~atef.widgets.config.data_active.ActionRowWidget` initialization to properly read enum strings from signal.

Contributors
------------
- tangkong



v1.1.0 (2023-09-14)
===================

Features
--------
- Adds find-replace functionality and helpers.  These procedures walk through the dataclass, rather than blindly modifying serialized json.
- Adds a simple find-replace widget and more fully-featured fill-template page.
- Adds backend dataclasses for running Bluesky plans in active checkouts.
- Prototypes auto-generated plan argument entry widgets.
- Annotates built-in Bluesky plans with bluesky-queueserver compatible type hints.
- Adds :class:`~atef.check.DynamicValue` (and subclasses :class:`~atef.check.HappiValue`, :class:`~atef.check.EpicsValue`) for comparing to dynamically changing data sources.
- Adds :class:`~atef.widgets.config.MultiModeValueEdit` widget for modifying values give a specified type, including dynamic values.

Bugfixes
--------
- Ensure filenames get cast as strings properly.
- Allow cast_dataclass to transfer objects from old to new dataclass, previously nested dataclasses would be converted to dicts.

Maintenance
-----------
- Adds bluesky-queueserver dependency and pins databroker.
- Add sphinx templates for autogenerated documentation.
- Reduce randomness in test suite, try all combo box options when available.

Contributors
------------
- tangkong


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
