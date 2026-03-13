About
=====

Python-snap7 is a pure Python S7 communication library for interfacing with Siemens S7 PLCs.

Python-snap7 is tested with Python 3.10+, on Windows, Linux and OS X.

The full documentation is available on `Read The Docs <https://python-snap7.readthedocs.io/en/latest/>`_.


Version 3.0 - Breaking Changes
===============================

Version 3.0 is a major release that rewrites python-snap7 as a pure Python
implementation. The C snap7 library is no longer required.

This release may contain breaking changes. If you experience issues, you can
pin to the last pre-3.0 release::

    $ pip install "python-snap7<3"

The latest stable pre-3.0 release is version 2.1.0.


Fork: Optimized Multi-Read Pipeline
=====================================

This fork (`python-snap7-optimized <https://github.com/QuakeString/python-snap7-optimized>`_)
adds a transparent **nodeS7-style read optimization pipeline** to the upstream
`python-snap7 <https://github.com/gijzelaerr/python-snap7>`_ library.

When ``read_multi_vars()`` is called with multiple items targeting standard memory
areas (DB, M, I, Q), the library now automatically:

1. **Sorts** requests by area, DB number, and byte offset.
2. **Merges** adjacent or nearby requests (gap ≤ 5 bytes) into contiguous blocks,
   eliminating redundant reads of overlapping or neighboring addresses.
3. **Packetizes** merged blocks into minimal S7 PDU-sized packets, respecting both
   request and reply budget limits.

The result is dramatically fewer network round-trips — hundreds of scattered variable
reads can collapse into just a few multi-item S7 protocol exchanges.

**No API changes required.** Existing code using ``read_multi_vars()`` benefits
automatically. Counter/Timer areas (CT/TM) fall back to individual reads due to
their different addressing semantics.

New modules:

- ``snap7/optimizer.py`` — Pure-logic 3-stage pipeline (sort → merge → packetize).
- Extended ``snap7/s7protocol.py`` — Multi-item S7 read request building and response parsing.
- Extended ``snap7/server/__init__.py`` — Server-side multi-item read support for testing.


Installation
============

Install using pip::

   $ pip install python-snap7

No native libraries or platform-specific dependencies are required - python-snap7 is a pure Python package that works on all platforms.
