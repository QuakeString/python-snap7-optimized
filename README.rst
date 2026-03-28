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


Parallel Packet Dispatch
========================

For PLCs that support it (S7-1200, S7-400, S7-1500), multiple optimized read
packets are dispatched back-to-back on the single TCP connection. Responses are
matched by S7 sequence number as they arrive, maximizing throughput.

- Auto-tuned ``max_parallel`` based on PLC capabilities (CP info or PDU heuristic).
- Stale-packet detection with automatic retry.
- S7-300 / LOGO / S7-200 automatically fall back to sequential dispatch.


Optimization Plan Caching
=========================

The optimization pipeline result (sort → merge → packetize) is cached across
repeated ``read_multi_vars()`` calls with the same item list. This eliminates
re-computation overhead during cyclic polling — only the first call pays the
optimization cost; subsequent calls reuse the cached plan.


TCP Keepalive for Fast Dead-Peer Detection
==========================================

All TCP connections enable ``SO_KEEPALIVE`` with aggressive probe settings:

- **Linux**: ``TCP_KEEPIDLE=2s``, ``TCP_KEEPINTVL=1s``, ``TCP_KEEPCNT=2``
  — detects dead connections within ~4 seconds.
- **macOS**: ``TCP_KEEPALIVE=2s``.

This catches network-level failures (cable pull, PLC power loss, OS crash)
far faster than the default TCP timeout (typically 30–120 seconds), which is
critical for industrial applications where data gaps must be minimized.


Heartbeat Read for Redundant PLC Monitoring
===========================================

New ``Client.heartbeat_read(timeout_ms=500)`` method provides a lightweight
liveness check designed for monitoring standby PLCs in redundant S7 systems
(S7-300H, S7-400H, S7-1500R/H).

.. code-block:: python

    client = snap7.Client()
    client.connect("192.168.1.11", 0, 1)

    # Fast liveness check — reads 1 byte from M0
    if client.heartbeat_read(timeout_ms=300):
        print("Standby PLC is alive")
    else:
        print("Standby PLC is unreachable")

- Reads 1 byte from Merker area (M0) — negligible PLC scan cycle impact.
- Uses a short, independent timeout (default 500 ms) without affecting the
  client's normal socket timeout.
- Returns ``True`` / ``False`` — no exceptions on timeout or connection error.
- Designed for background heartbeat threads monitoring the inactive CPU in
  a dual-connection redundancy setup.


Model-Specific Tuning
=====================

Read optimization respects PLC-specific limits:

==================================  ================  ================
PLC Model                           Max Read Block    Max Parallel
==================================  ================  ================
S7-200 / S7-200 Smart / LOGO        100 bytes         1 (sequential)
S7-300                               200 bytes         1 (sequential)
S7-400                               200 bytes         4
S7-1200                              1000 bytes        8
S7-1500                              1000 bytes        8
==================================  ================  ================


Installation
============

Install using pip::

   $ pip install snap7-optimized

No native libraries or platform-specific dependencies are required - this is a
pure Python package that works on all platforms (Linux, Windows, macOS).
