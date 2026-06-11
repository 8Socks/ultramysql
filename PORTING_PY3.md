# Python 3 port of ultramysql (umysql)

This branch ports the umysql CPython extension to **Python 3** while keeping it
building and working unchanged on **Python 2.7** (dual-version via
`#if PY_MAJOR_VERSION >= 3` guards). The goal: keep umysql's two defining
properties on py3 -- **C-level speed** and **gevent-cooperative, non-blocking
I/O** -- which the pure-Python alternatives (PyMySQL) give up.

## What changed

Only the CPython binding needed porting. The MySQL protocol core in `lib/` is
pure C++ (no `Python.h`) and is untouched.

- **`python/py3compat.h`** (new) -- centralizes the py2/py3 C-API translations:
  `PyInt_*`->`PyLong_*`, `PyExc_StandardError`->`PyExc_Exception`, native-str vs
  bytes helpers, `PyFloat_FromString` arity change, `PyUnicode_GET_SIZE`->
  `PyUnicode_GET_LENGTH`.
- **`python/umysql.c`**
  - Module init: dual `initumysql` (py2) / `PyInit_umysql` + `PyModuleDef` (py3).
  - Type objects: `PyObject_HEAD_INIT(NULL) + ob_size` -> `PyVarObject_HEAD_INIT(NULL, 0)`.
  - Result decoding (output): column **names** and `DECIMAL`/`ENUM`/`SET` come
    back as `str` on py3; `BINARY`/`BLOB` stay `bytes`; text columns already
    decoded to unicode via `DecodeString` (unchanged). Integers via `PyLong`.
  - Query/param escaping (input): accepts py3 `str` (encoded to bytes through the
    connection's codec via `PyUnicode_AsEncodedString`) and `bytes`. The internal
    wire buffer uses `PyBytes_*` (an alias for `PyString_*` on py2.6+), so the
    escaping logic is written once for both versions. Replaced the removed
    `PyUnicode_AS_UNICODE` / `PyUnicode_Encode*` APIs.
- **`python/io_cpython.c`** -- socket method names (`recv`/`send`/`connect`/
  `settimeout`) are `str` on py3; recv/send payloads are `bytes`. **The I/O still
  goes through the Python `socket` module object** -- this is what makes umysql
  cooperate with gevent's monkey-patching, and it is deliberately unchanged.
- **`setup.py`** -- prefers `setuptools` (falls back to `distutils`), so it
  builds on py3.12+ where distutils was removed.

## Build

```bash
python3 setup.py build_ext --inplace     # or python2.7 for py2
```

If your environment lacks distutils/setuptools, the binding compiles directly
(`.c` as C, `.cpp` as C++, linked with g++):

```bash
PYINC=$(python3 -c 'import sysconfig; print(sysconfig.get_path("include"))')
gcc -O2 -fPIC -c -I./python -I./lib -I$PYINC python/umysql.c     -o umysql.o
gcc -O2 -fPIC -c -I./python -I./lib -I$PYINC python/io_cpython.c -o io.o
for f in capi Connection PacketReader PacketWriter SHA1; do g++ -O2 -fPIC -c -I./python -I./lib lib/$f.cpp -o $f.o; done
g++ -shared umysql.o io.o capi.o Connection.o PacketReader.o PacketWriter.o SHA1.o -o umysql.so
```

## Validation (Python 3.9 + MySQL 8.0, utf8mb4)

17/17 behavioral contracts pass: write returns `(rowcount, lastrowid)`; SELECT
returns a ResultSet with `.rows` (list of tuples) and `.fields` (name at `[0]`);
column names and text are `str`; DECIMAL is a string (faithful to umysql); int
columns are `int`; utf8mb4 emoji round-trips; `None`<->`NULL`; `umysql.SQLError`
carries `(int code, str message)`; a closed connection raises
`RuntimeError('Not connected')`. The same source also builds and smoke-tests on
Python 2.7 (no behavior change).

### Performance (single-threaded, vs the alternatives)

| driver | point SELECT | large fetch (5k rows) | insert |
|--------|-------------:|----------------------:|-------:|
| umysql py2 (C) | 0.055 ms | 1.3 ms | 0.33 ms |
| **umysql py3 (this port)** | **0.055 ms** | **1.1 ms** | **0.22 ms** |
| PyMySQL (pure Python) | 0.110 ms | 25.9 ms | 0.24 ms |

The port keeps full C speed -- ~24x faster than PyMySQL on large result-set
decoding, the case where pure-Python drivers hurt most.

### gevent cooperation

The I/O mechanism (driving a Python `socket` object) is unchanged, and was
measured cooperative on py2 (10 concurrent `SELECT SLEEP(0.5)` complete in
~0.5s, not 5s). Re-confirm empirically on py3 with gevent installed.

## Edge-case test suite (`tests/test_py3_port.py`)

A 79-test suite covering type decoding (every MySQL type incl. JSON/BIT/SET/
unsigned boundaries), py3 str/bytes boundaries, param escaping + SQL-injection,
connection/charset/auth lifecycle, ResultSet/protocol edges, gevent concurrency,
and refcount/leak behavior on the rewritten paths. Runs green on both py2.7
(gevent active -> the 7 concurrency tests execute) and py3.9 (those 7 skip).

Authoring it surfaced and fixed three real defects:
- **BIT/GEOMETRY decoded as text** -> on py3 a non-ASCII byte (e.g. `BIT(8)=0xFF`)
  raised `UnicodeDecodeError`. These binary types now decode to `bytes`.
- **JSON (type 245) unhandled** -> a direct `SELECT` of a JSON column raised
  "Unable to convert field of type 245". JSON now decodes as text (`str` on py3).
- **Output-buffer overflow** -> non-string params reserved a fixed 64 bytes in
  `EscapeQueryArguments`, so a >64-char rendering (huge int / high-precision
  `Decimal`) overflowed the buffer. The estimate is now sized from the value.

## Security review

A multi-agent security review (escaping, buffer safety, untrusted-packet parsing,
integer overflow, refcounts, auth) found and **fixed** the following. Several are
pre-existing upstream bugs but all are in scope and now patched:

**Param-controlled (app-reachable):**
- **Escape output-buffer overflow** -- `AppendAndEscapeString` wrote without
  checking its `buffEnd`, and the non-string size estimate counted code points
  while the write encodes UTF-8 bytes (and called `PyObject_Str` twice, a TOCTOU
  on a side-effecting `__str__`). Now: the writer is hard-bounded (returns -1 ->
  query fails rather than overflowing), and the estimate is sized from the
  encoded byte length. (Regression tests added.)
- **NULL-deref** when a param's `__str__` raises (`PyObject_Str` -> NULL used
  unchecked). Now checked.

**Malicious / MITM server (parses untrusted wire data):**
- **DATETIME/TIMESTAMP stack buffer overflow** -- `memcpy(temp /*char[20]*/,
  value, cbValue)` with a server-controlled length up to 16MB. The copy was never
  even read; removed it and added a length guard. (DATE got the same guard.)
- **Result field-count OOB write** -- the field loop was unbounded and mixed two
  different column counts, so a server sending more field packets than advertised
  wrote past the `alloca`'d type array and the Python tuple. Now: one authoritative
  count, capped to MySQL's 4096-column max, with the loop bounded.
- **`readLengthCodedBinary` over-read** -- it advanced by a server-controlled
  length guarded only by `assert()` (compiled out under `NDEBUG` in release
  builds), so a returned (pointer,length) could reference memory past the packet.
  Now: real runtime bounds checks + payload clamped to the packet.

Verified after fixes: byte-identical to the original on py2, 75/75 compat, full
edge suite green on both interpreters.

**Second pass -- hostile user input flowing into the library (param values/types,
the params iterable, encoding).** This is the realistic app-level threat. Found
and fixed three more:
- **Injection via the unquoted str() fallback** -- any param that is not a real
  str/bytes/None/datetime/bool (a custom object, an `int` subclass, a `list`/
  `dict`, `float('inf')`) was rendered into the SQL UNQUOTED, so an object whose
  `__str__` returns `1 OR 1=1` injected. Now the str() fallback is emitted
  unquoted ONLY for a genuine numeric literal (digits/sign/dot/exponent); anything
  else is quoted+escaped into an inert string literal. (bool keeps unquoted 1/0;
  genuine int/float/Decimal stay unquoted so `LIMIT %s` etc. still work.)
- **Heap overflow via the datetime sprintf** -- the datetime/date write used an
  unbounded `sprintf` that bypassed the escaper's bound; a `datetime` subclass
  with a shrinking `__str__` undersized the estimate (proven with ASAN). Now
  bounds-checked before the write.
- **NULL-deref crash** when a params object's `__iter__` raises (`PyObject_GetIter`
  was unchecked on both the sizing and writing passes). Now checked.

The escaping of genuine str/bytes params was confirmed sound across all byte
values, control chars, and every selectable charset (no multibyte break-out --
only single-byte and utf8 charsets are selectable). 8 user-input regression
tests added.

Remaining lower-severity hardening (NULL checks on `PyObject_Malloc`/`PyObject_New`,
handshake fixed-offset reads, the broader assert-only bounds in `PacketReader`
read primitives, `scramble()` over-read -- all malicious-SERVER only) is noted
for follow-up.

## Known limitations / follow-ups

- Tested against `mysql_native_password` on MySQL 8. `caching_sha2_password`
  (MySQL 8 default for new users) is not implemented by this driver and is a
  separate piece of work if required.
- A `>65`-digit numeric literal sent to MySQL 8 hangs on py2 (the recv path);
  this reproduces on the *original* umysql too (pre-existing, not a port issue).
- py3+gevent concurrency benchmark still to be run in a gevent-enabled env (the
  cooperation property itself is verified on py2).
