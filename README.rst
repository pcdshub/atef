===============================
atef
===============================

.. image:: https://img.shields.io/travis/pcdshub/atef.svg
        :target: https://travis-ci.org/pcdshub/atef

.. image:: https://img.shields.io/pypi/v/atef.svg
        :target: https://pypi.python.org/pypi/atef


ATEF: "Automated Test Execution Framework"

This is alpha-level software and does not include much in the way of working functionality just yet.

While the project has "automated" in its name, that is only one part of atef.
At this point - this is all subject to change - the project is broken up into several
different parts:

* Passive testing of control system devices/state
    * Fully-automated
    * Non-intrusive (will not move your motors)
    * Report generated
    * For daemon-backed passive tests, a control system-linked status report
* Active testing of control system devices
    * Guided, interactive checkout process with
    * Perhaps also fully automated versions of the above
    * Report generation
* General user-facing GUI (TUI)
    * Synoptic for launching tests ("device dashboard")
    * Command-line tools for doing the same

Some plans and ramblings are detailed in the discussions section for now:
https://github.com/pcdshub/atef/discussions
