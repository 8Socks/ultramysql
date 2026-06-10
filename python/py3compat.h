/*
Python 2 / Python 3 C-API compatibility shims for the umysql binding.

The MySQL protocol core in lib/ is pure C++ and needs no changes. All the
Python-version differences are confined to python/umysql.c and
python/io_cpython.c; this header centralizes the mechanical translations so
both files build unchanged on CPython 2.7 and 3.x.

Design notes:
- Byte buffers (the escaped wire SQL, socket recv/send payloads) use the
  PyBytes_* API directly at their call sites. PyBytes_* exists as an alias for
  PyString_* on CPython 2.6+, so those sites are written once and work on both.
- "Native str" (column names, DECIMAL/ENUM text, socket method names, host
  names) becomes unicode (str) on py3 and bytes (str) on py2 via UM_NATIVE_STR*.
- Unicode-to-bytes encoding for query/params uses PyUnicode_AsEncodedString with
  the connection's Python codec name (e.g. "utf-8"), which exists on both
  versions -- avoiding PyUnicode_AS_UNICODE / PyUnicode_Encode*, removed in 3.9+.
*/
#ifndef UMYSQL_PY3COMPAT_H
#define UMYSQL_PY3COMPAT_H

#include <Python.h>

#if PY_MAJOR_VERSION >= 3

  /* Integers unified into PyLong on py3 */
  #define PyInt_FromLong            PyLong_FromLong
  #define PyInt_AsLong              PyLong_AsLong

  /* StandardError was folded into Exception */
  #define PyExc_StandardError       PyExc_Exception

  /* Native text -> str (unicode). Column names, DECIMAL/ENUM/SET values,
     socket method names and host strings are all textual on py3. */
  #define UM_NATIVE_STR(s)          PyUnicode_FromString(s)
  #define UM_NATIVE_STR_N(s, n)     PyUnicode_DecodeUTF8((const char *)(s), (n), "strict")

  /* PyUnicode_GET_SIZE was removed in 3.12 and deprecated earlier; GET_LENGTH
     is the supported replacement (counts code points, fine for sizing). */
  #define UM_UNICODE_LEN(o)         PyUnicode_GET_LENGTH(o)

  /* PyFloat_FromString lost its second (char **) argument in py3. */
  #define UM_FLOAT_FROM_STRING(o)   PyFloat_FromString(o)

#else

  #define UM_NATIVE_STR(s)          PyString_FromString(s)
  #define UM_NATIVE_STR_N(s, n)     PyString_FromStringAndSize((const char *)(s), (n))
  #define UM_UNICODE_LEN(o)         PyUnicode_GET_SIZE(o)
  #define UM_FLOAT_FROM_STRING(o)   PyFloat_FromString((o), NULL)

#endif

#endif /* UMYSQL_PY3COMPAT_H */
