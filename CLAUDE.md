# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

This project uses mise for tool/script management.

```bash
# Install tools and sync dependencies
mise install

# Run all checks (ruff, mypy, pytest)
mise run check

# Run tests only
uv run pytest

# Run a single test file
uv run pytest tests/test_scan.py

# Run a specific test
uv run pytest tests/test_matching.py::test_whole_word_match
```

## Architecture

**ok-serial** is a PySerial wrapper providing improved serial port discovery, sharing semantics, and async support.

### Core Components

- **`_connection.py`**: `SerialConnection` - the main connection class wrapping PySerial
  - Provides both sync (`read_sync`, `drain_sync`) and async (`read_async`, `drain_async`) APIs
  - Uses dedicated reader/writer threads (`_IoThreads`) for non-blocking I/O
  - Thread-safe via `threading.Condition` monitor pattern

- `_matching.py`: `compile_match()` - turns a match string or callable into a `SerialPort -> bool` predicate (simple whole-word glob; pass a callable for anything fancier)

- **`_scan.py`**: Port discovery
  - `scan_serial_ports()` - returns `SerialPortAttributes` for all ports
  - Supports `OK_SERIAL_SCAN_OVERRIDE` env var for testing with fake port data

- **`_tracker.py`**: `SerialPortTracker` - auto-reconnecting connection manager
  - Periodically scans for matching ports and maintains connection
  - Handles disconnect/reconnect transparently

- **`_lock.py`**: Multi-layer port locking with sharing modes:
  - `"oblivious"` - no locking
  - `"polite"` - shared flock, respects others
  - `"exclusive"` - exclusive flock + TIOCEXCL
  - `"stomp"` - kills existing owner if needed
  - Uses `/var/lock/LCK..*` lockfiles plus flock/TIOCEXCL

- **`_exceptions.py`**: Exception hierarchy rooted at `SerialException` (extends `OSError`)

### Testing

Tests use pseudo-TTYs (`pty.openpty()`) to simulate serial ports without hardware. The `pty_serial` fixture in `conftest.py` provides a test serial connection.
