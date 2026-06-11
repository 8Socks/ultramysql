# -*- coding: utf-8 -*-
"""
Edge-case suite for the Python 3 port of umysql (dual py2.7/py3.x).
Generated from an 8-dimension gap analysis; each test targets a specific code
path in python/umysql.c / io_cpython.c. Requires MySQL 8 + utf8mb4 and a
mysql_native_password user (gevent_test/gevent_test/gevent_test by default).
"""
from __future__ import print_function
import sys, gc, unittest

try:
    import gevent
    from gevent import monkey
    monkey.patch_all()
    HAVE_GEVENT = True
except ImportError:
    HAVE_GEVENT = False

import umysql

PY3 = sys.version_info[0] >= 3
int_types_alias = (int,) if PY3 else (int, long)  # noqa: F821

DB_HOST = '127.0.0.1'
DB_PORT = 3306
DB_USER = 'gevent_test'
DB_PASSWD = 'gevent_test'
DB_DB = 'gevent_test'


def conn(charset='utf8mb4'):
    c = umysql.Connection()
    c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, charset)
    return c

def run_ddl(sql):
    if not sql or not sql.strip():
        return
    c = conn()
    try:
        for stmt in sql.split(';'):
            if stmt.strip():
                c.query(stmt)
    finally:
        c.close()


class PortEdgeCases(unittest.TestCase):
    def test_type_decode__decode_json_column_unhandled_type(self):
        run_ddl('DROP TABLE IF EXISTS umysql_json_t; CREATE TABLE umysql_json_t (id int primary key, j json) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        import umysql
        c = conn()
        c.query("INSERT INTO umysql_json_t (id, j) VALUES (1, %s)", ('{\"a\": 1, \"b\": [2, 3]}',))
        # Selecting the raw JSON column: type 245 is unhandled by the switch.
        try:
            rs = c.query("SELECT j FROM umysql_json_t WHERE id = 1")
            raised = None
            got = rs.rows[0][0]
        except Exception as e:
            raised = e
            got = None
        # Ported behavior: JSON (type 245) now decodes as text (str on py3).
        assert raised is None, "JSON should decode, not raise: %r" % (raised,)
        gs = got.decode('utf-8') if isinstance(got, bytes) else got
        assert gs.replace(' ', '') == '{"a":1,"b":[2,3]}', repr(got)
        # Sanity: casting JSON to CHAR avoids the gap and decodes as text (str on py3).
        rs2 = c.query("SELECT CAST(j AS CHAR) FROM umysql_json_t WHERE id = 1")
        v = rs2.rows[0][0]
        if PY3:
            assert isinstance(v, str), type(v)
        assert v.replace(' ', '') == '{\"a\":1,\"b\":[2,3]}'.replace(' ', ''), repr(v)
        c.close()

    def test_type_decode__decode_bit_utf8_strict_crash(self):
        run_ddl('DROP TABLE IF EXISTS umysql_bit_t; CREATE TABLE umysql_bit_t (id int primary key, b8 bit(8), b7 bit(7)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("INSERT INTO umysql_bit_t (id, b8, b7) VALUES (1, b'11111111', b'1111111')")
        # BIT(7) = 0x7f is valid ASCII -> decodes on both versions.
        rs7 = c.query("SELECT b7 FROM umysql_bit_t WHERE id = 1")
        v7 = rs7.rows[0][0]
        assert isinstance(v7, bytes), type(v7)
        assert v7 == b'\x7f', repr(v7)
        # BIT(8) = 0xff is NOT valid UTF-8. On py3 the strict UTF-8 decode raises.
        try:
            rs8 = c.query("SELECT b8 FROM umysql_bit_t WHERE id = 1")
            err = None
            v8 = rs8.rows[0][0]
        except Exception as e:
            err = e
            v8 = None
        assert err is None, "BIT(8) must not raise (binary -> bytes), got %r" % (err,)
        assert isinstance(v8, bytes) and v8 == b'\xff', repr(v8)
        c.close()

    def test_type_decode__decode_int_signed_boundaries(self):
        run_ddl('DROP TABLE IF EXISTS umysql_sint_t; CREATE TABLE umysql_sint_t (id int primary key, t tinyint, s smallint, m mediumint, i int, b bigint) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("INSERT INTO umysql_sint_t (id,t,s,m,i,b) VALUES (1,-128,-32768,-8388608,-2147483648,-9223372036854775808)")
        c.query("INSERT INTO umysql_sint_t (id,t,s,m,i,b) VALUES (2,127,32767,8388607,2147483647,9223372036854775807)")
        rs = c.query("SELECT t,s,m,i,b FROM umysql_sint_t ORDER BY id")
        lo = rs.rows[0]
        hi = rs.rows[1]
        assert lo == (-128, -32768, -8388608, -2147483648, -9223372036854775808), lo
        assert hi == (127, 32767, 8388607, 2147483647, 9223372036854775807), hi
        for v in lo + hi:
            assert isinstance(v, int_types_alias), (v, type(v))
        c.close()

    def test_type_decode__decode_int_unsigned_boundaries(self):
        run_ddl('DROP TABLE IF EXISTS umysql_uint_t; CREATE TABLE umysql_uint_t (id int primary key, ui int unsigned, ub bigint unsigned) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("INSERT INTO umysql_uint_t (id, ui, ub) VALUES (1, 4294967295, 18446744073709551615)")
        c.query("INSERT INTO umysql_uint_t (id, ui, ub) VALUES (2, 0, 9223372036854775808)")
        rs = c.query("SELECT ui, ub FROM umysql_uint_t ORDER BY id")
        r1 = rs.rows[0]
        r2 = rs.rows[1]
        assert r1[0] == 4294967295, r1[0]
        assert r1[1] == 18446744073709551615, r1[1]   # 2^64-1, must NOT be -1
        assert r2[0] == 0, r2[0]
        assert r2[1] == 9223372036854775808, r2[1]    # 2^63 exactly, must NOT be negative
        for v in (r1[0], r1[1], r2[0], r2[1]):
            assert v >= 0, v
        c.close()

    def test_type_decode__decode_decimal_str_precision(self):
        run_ddl('DROP TABLE IF EXISTS umysql_dec_t; CREATE TABLE umysql_dec_t (id int primary key, d decimal(30,10)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        rows = [
            (1, '-12345.6789000000'),
            (2, '12345678901234567890.1234567890'),
            (3, '0.0001000000'),
            (4, '0.0000000000'),
            (5, '100.0000000000'),
        ]
        for rid, lit in rows:
            c.query("INSERT INTO umysql_dec_t (id, d) VALUES (%s, " + lit + ")", (rid,))
        rs = c.query("SELECT id, d FROM umysql_dec_t ORDER BY id")
        got = dict((r[0], r[1]) for r in rs.rows)
        expected = {1: '-12345.6789000000', 2: '12345678901234567890.1234567890', 3: '0.0001000000', 4: '0.0000000000', 5: '100.0000000000'}
        for rid in expected:
            v = got[rid]
            if PY3:
                assert isinstance(v, str), (rid, type(v))
            assert v == expected[rid], (rid, v)
        c.close()

    def test_type_decode__decode_enum_set_year(self):
        run_ddl("DROP TABLE IF EXISTS umysql_es_t; CREATE TABLE umysql_es_t (id int primary key, e enum('red','green','blue'), s set('a','b','c','d'), y year) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        c = conn()
        c.query("INSERT INTO umysql_es_t (id,e,s,y) VALUES (1,'green','a,c,d',2024)")
        rs = c.query("SELECT e, s, y FROM umysql_es_t WHERE id = 1")
        e, s, y = rs.rows[0]
        if PY3:
            assert isinstance(e, str) and isinstance(s, str), (type(e), type(s))
        assert isinstance(y, bytes), type(y)   # YEAR is binary-flagged -> bytes (faithful to umysql)
        assert e == 'green', repr(e)
        assert s == 'a,c,d', repr(s)
        assert y == b'2024', repr(y)
        c.close()

    def test_type_decode__decode_time_and_fractional_datetime(self):
        run_ddl('DROP TABLE IF EXISTS umysql_time_t; CREATE TABLE umysql_time_t (id int primary key, t time, t2 time, dt datetime(6)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        import datetime
        c = conn()
        c.query("INSERT INTO umysql_time_t (id,t,t2,dt) VALUES (1,'13:37:42','-01:02:03','2024-06-15 12:30:45.123456')")
        rs = c.query("SELECT t, t2, dt FROM umysql_time_t WHERE id = 1")
        t, t2, dt = rs.rows[0]
        # TIME is binary-flagged -> bytes (faithful to umysql), not timedelta/str
        assert isinstance(t, bytes) and isinstance(t2, bytes), (type(t), type(t2))
        assert t == b'13:37:42', repr(t)
        assert t2 == b'-01:02:03', repr(t2)
        # DATETIME(6) -> datetime with microsecond truncated to 0 (hard-coded in decoder)
        assert isinstance(dt, datetime.datetime), type(dt)
        assert dt == datetime.datetime(2024, 6, 15, 12, 30, 45, 0), dt
        assert dt.microsecond == 0, dt.microsecond
        c.close()

    def test_type_decode__decode_char_text_vs_binary_flag(self):
        run_ddl('DROP TABLE IF EXISTS umysql_strbin_t; CREATE TABLE umysql_strbin_t (id int primary key, c char(10) CHARACTER SET utf8mb4, v varchar(50) CHARACTER SET utf8mb4, t text CHARACTER SET utf8mb4, bin binary(4), vbin varbinary(50), bl blob) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        binval = b'\x00\x01\xfe\xff'
        c.query("INSERT INTO umysql_strbin_t (id,c,v,t,bin,vbin,bl) VALUES (1,%s,%s,%s,%s,%s,%s)", ('cval', 'vval', 'tval', binval, binval, binval))
        rs = c.query("SELECT c,v,t,bin,vbin,bl FROM umysql_strbin_t WHERE id = 1")
        cv, vv, tv, binv, vbinv, blv = rs.rows[0]
        if PY3:
            assert isinstance(cv, str) and isinstance(vv, str) and isinstance(tv, str), (type(cv), type(vv), type(tv))
            assert isinstance(binv, bytes) and isinstance(vbinv, bytes) and isinstance(blv, bytes), (type(binv), type(vbinv), type(blv))
        assert cv == 'cval' and vv == 'vval' and tv == 'tval', (cv, vv, tv)
        # CHAR(4) binary is right-padded to its full width with NUL
        assert binv == b'\x00\x01\xfe\xff', repr(binv)
        assert vbinv == b'\x00\x01\xfe\xff', repr(vbinv)
        assert blv == b'\x00\x01\xfe\xff', repr(blv)
        c.close()

    def test_type_decode__decode_null_per_type(self):
        run_ddl("DROP TABLE IF EXISTS umysql_null_t; CREATE TABLE umysql_null_t (id int primary key, i int, ub bigint unsigned, d decimal(10,2), f double, dt datetime, dd date, tm time, b bit(8), e enum('x','y'), s set('a','b'), bl blob, vc varchar(20)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        c = conn()
        c.query("INSERT INTO umysql_null_t (id) VALUES (1)")  # all other columns default NULL
        rs = c.query("SELECT i, ub, d, f, dt, dd, tm, b, e, s, bl, vc FROM umysql_null_t WHERE id = 1")
        row = rs.rows[0]
        assert len(row) == 12, len(row)
        for idx, v in enumerate(row):
            assert v is None, "column index %d expected None, got %r" % (idx, v)
        c.close()

    def test_type_decode__decode_float_vs_double_precision(self):
        run_ddl('DROP TABLE IF EXISTS umysql_fd_t; CREATE TABLE umysql_fd_t (id int primary key, f float, d double) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        # 0.1 has no exact binary repr; DOUBLE recovers it exactly via MySQL's text repr.
        c.query("INSERT INTO umysql_fd_t (id, f, d) VALUES (1, 0.1, 0.1)")
        # A value needing full double precision.
        c.query("INSERT INTO umysql_fd_t (id, f, d) VALUES (2, 1.23456789012345, 1.23456789012345)")
        rs = c.query("SELECT id, f, d FROM umysql_fd_t ORDER BY id")
        r1 = rs.rows[0]
        r2 = rs.rows[1]
        assert isinstance(r1[1], float) and isinstance(r1[2], float), (type(r1[1]), type(r1[2]))
        # DOUBLE 0.1 recovered exactly.
        assert r1[2] == 0.1, repr(r1[2])
        # FLOAT 0.1 is single-precision: close to but generally not exactly 0.1.
        assert abs(r1[1] - 0.1) < 1e-6, repr(r1[1])
        # DOUBLE keeps ~15-16 significant digits.
        assert abs(r2[2] - 1.23456789012345) < 1e-12, repr(r2[2])
        # FLOAT collapses to ~7 significant digits -- materially less precise than the double.
        assert abs(r2[1] - 1.23456789012345) > 1e-8, 'FLOAT should have lost precision: %r' % (r2[1],)
        c.close()

    def test_str_bytes_py3__py3_colname_nonascii(self):
        if not PY3:
            return
        c = conn()
        rs = c.query(u'SELECT 1 AS `café`, 2 AS `naïve`')
        names = [f[0] for f in rs.fields]
        assert names == [u'café', u'naïve'], repr(names)
        assert isinstance(names[0], str), type(names[0])
        assert isinstance(names[1], str), type(names[1])
        # row values still decode correctly alongside the non-ascii names
        assert rs.rows == [(1, 2)], repr(rs.rows)
        c.close()

    def test_str_bytes_py3__py3_emoji_colname(self):
        if not PY3:
            return
        c = conn()
        rs = c.query(u'SELECT 1 AS `rocket_\U0001f680`')
        name = rs.fields[0][0]
        assert isinstance(name, str), type(name)
        # MySQL transmits column-name metadata in a charset that maps 4-byte chars
        # to '?', so the emoji becomes '?'. The driver returns str either way.
        assert name in (u'rocket_\U0001f680', u'rocket_?'), repr(name)
        c.close()

    def test_str_bytes_py3__py3_decimal_enum_set_str(self):
        run_ddl("DROP TABLE IF EXISTS umysql_py3_des; CREATE TABLE umysql_py3_des (d DECIMAL(10,2), e ENUM('a','b','c'), s SET('x','y','z')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        if not PY3:
            return
        c = conn()
        c.query('DROP TABLE IF EXISTS umysql_py3_des')
        c.query("CREATE TABLE umysql_py3_des (d DECIMAL(10,2), e ENUM('a','b','c'), s SET('x','y','z')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        c.query('INSERT INTO umysql_py3_des (d, e, s) VALUES (%s, %s, %s)', ('123.45', 'b', 'x,z'))
        rs = c.query('SELECT d, e, s FROM umysql_py3_des')
        d, e, s = rs.rows[0]
        assert isinstance(d, str), type(d)
        assert d == '123.45', repr(d)
        assert isinstance(e, str), type(e)
        assert e == 'b', repr(e)
        assert isinstance(s, str), type(s)
        assert s == 'x,z', repr(s)
        c.query('DROP TABLE IF EXISTS umysql_py3_des')
        c.close()

    def test_str_bytes_py3__py3_binary_vs_text_types(self):
        run_ddl('DROP TABLE IF EXISTS umysql_py3_bin; CREATE TABLE umysql_py3_bin (b VARBINARY(32), bl BLOB, t TEXT CHARACTER SET utf8mb4) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        if not PY3:
            return
        c = conn()
        c.query('DROP TABLE IF EXISTS umysql_py3_bin')
        c.query('CREATE TABLE umysql_py3_bin (b VARBINARY(32), bl BLOB, t TEXT CHARACTER SET utf8mb4) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        raw = b'\x00\x01\xfe\xff_bin'
        c.query('INSERT INTO umysql_py3_bin (b, bl, t) VALUES (%s, %s, %s)', (raw, raw, u'café'))
        rs = c.query('SELECT b, bl, t FROM umysql_py3_bin')
        b, bl, t = rs.rows[0]
        assert isinstance(b, bytes), type(b)
        assert b == raw, repr(b)
        assert isinstance(bl, bytes), type(bl)
        assert bl == raw, repr(bl)
        assert isinstance(t, str), type(t)
        assert t == u'café', repr(t)
        c.query('DROP TABLE IF EXISTS umysql_py3_bin')
        c.close()

    def test_str_bytes_py3__py3_bytes_param_roundtrip(self):
        run_ddl('DROP TABLE IF EXISTS umysql_py3_bp; CREATE TABLE umysql_py3_bp (b VARBINARY(64)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        if not PY3:
            return
        c = conn()
        c.query('DROP TABLE IF EXISTS umysql_py3_bp')
        c.query('CREATE TABLE umysql_py3_bp (b VARBINARY(64)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        payload = b"a'b\\c\x00d\x1ae\"f\n\r"
        c.query('INSERT INTO umysql_py3_bp (b) VALUES (%s)', (payload,))
        rs = c.query('SELECT b FROM umysql_py3_bp')
        got = rs.rows[0][0]
        assert isinstance(got, bytes), type(got)
        assert got == payload, repr(got)
        c.query('DROP TABLE IF EXISTS umysql_py3_bp')
        c.close()

    def test_str_bytes_py3__py3_mixed_str_bytes_params(self):
        run_ddl('DROP TABLE IF EXISTS umysql_py3_mix; CREATE TABLE umysql_py3_mix (t VARCHAR(64) CHARACTER SET utf8mb4, b VARBINARY(64)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        if not PY3:
            return
        c = conn()
        c.query('DROP TABLE IF EXISTS umysql_py3_mix')
        c.query('CREATE TABLE umysql_py3_mix (t VARCHAR(64) CHARACTER SET utf8mb4, b VARBINARY(64)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        text = u'café \U0001f600'
        blob = b'\x00\xff raw'
        c.query('INSERT INTO umysql_py3_mix (t, b) VALUES (%s, %s)', (text, blob))
        rs = c.query('SELECT t, b FROM umysql_py3_mix')
        gt, gb = rs.rows[0]
        assert isinstance(gt, str) and gt == text, repr(gt)
        assert isinstance(gb, bytes) and gb == blob, repr(gb)
        c.query('DROP TABLE IF EXISTS umysql_py3_mix')
        c.close()

    def test_str_bytes_py3__py3_latin1_param_unencodable_raises(self):
        if not PY3:
            return
        import umysql
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'latin1')
        try:
            raised = False
            try:
                c.query('SELECT %s', (u'\U0001f600',))
            except (UnicodeEncodeError, ValueError):
                raised = True
            assert raised, 'expected encode error for emoji on latin1 connection'
            # connection must remain usable after the cleanly-handled error
            rs = c.query('SELECT %s', (u'plain ascii',))
            assert rs.rows == [(u'plain ascii',)], repr(rs.rows)
        finally:
            c.close()

    def test_str_bytes_py3__py3_query_string_unencodable_raises(self):
        if not PY3:
            return
        import umysql
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'latin1')
        try:
            raised = False
            try:
                c.query(u"SELECT '\U0001f600' AS x")
            except (UnicodeEncodeError, ValueError):
                raised = True
            assert raised, 'expected encode error for non-latin1 query text on latin1 connection'
            # still usable
            rs = c.query(u'SELECT 1 AS x')
            assert rs.rows == [(1,)], repr(rs.rows)
        finally:
            c.close()

    def test_str_bytes_py3__py3_latin1_column_decode(self):
        run_ddl('DROP TABLE IF EXISTS umysql_py3_l1; CREATE TABLE umysql_py3_l1 (v VARCHAR(32) CHARACTER SET latin1) ENGINE=InnoDB DEFAULT CHARSET=latin1;')
        if not PY3:
            return
        c = conn()
        c.query('DROP TABLE IF EXISTS umysql_py3_l1')
        c.query('CREATE TABLE umysql_py3_l1 (v VARCHAR(32) CHARACTER SET latin1) ENGINE=InnoDB DEFAULT CHARSET=latin1')
        # insert the latin1 a-umlaut via a server-side literal so storage byte is 0xE4
        c.query("INSERT INTO umysql_py3_l1 (v) VALUES (_latin1 0xE4)")
        rs = c.query('SELECT v FROM umysql_py3_l1')
        got = rs.rows[0][0]
        assert isinstance(got, str), type(got)
        assert got == u'ä', repr(got)
        assert len(got) == 1, len(got)
        c.query('DROP TABLE IF EXISTS umysql_py3_l1')
        c.close()

    def test_str_bytes_py3__py3_str_param_4byte_roundtrip_utf8mb4(self):
        run_ddl('DROP TABLE IF EXISTS umysql_py3_sp; CREATE TABLE umysql_py3_sp (v VARCHAR(16) CHARACTER SET utf8mb4) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        if not PY3:
            return
        c = conn()
        c.query('DROP TABLE IF EXISTS umysql_py3_sp')
        c.query('CREATE TABLE umysql_py3_sp (v VARCHAR(16) CHARACTER SET utf8mb4) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        val = u'A\U0001f603B'
        c.query('INSERT INTO umysql_py3_sp (v) VALUES (%s)', (val,))
        rs = c.query('SELECT v FROM umysql_py3_sp WHERE v = %s', (val,))
        assert len(rs.rows) == 1, repr(rs.rows)
        got = rs.rows[0][0]
        assert isinstance(got, str), type(got)
        assert got == val, repr(got)
        assert len(got) == 3, len(got)
        c.query('DROP TABLE IF EXISTS umysql_py3_sp')
        c.close()

    def test_param_escaping__esc_bare_percent_valueerror(self):
        c = conn()
        try:
            c.query("SELECT %d", (5,))
            assert False, "expected ValueError for bare % not followed by s or %"
        except ValueError as e:
            msg = str(e)
            assert 'expected' in msg, repr(msg)
            # the format string is: 'Found character %c expected %%' -> 'Found character d expected %'
            assert 'd' in msg, repr(msg)
        # connection must still be usable after the rejected query (error raised before send)
        rs = c.query("SELECT 1")
        assert rs.rows == [(1,)], rs.rows
        c.close()

    def test_param_escaping__esc_too_many_placeholders(self):
        c = conn()
        try:
            c.query("SELECT %s, %s", (1,))
            assert False, "expected ValueError for too many placeholders"
        except ValueError as e:
            assert 'Unexpected end of iterator' in str(e), repr(str(e))
        rs = c.query("SELECT 1")
        assert rs.rows == [(1,)], rs.rows
        c.close()

    def test_param_escaping__esc_too_few_placeholders_ignored(self):
        c = conn()
        # 1 placeholder, 3 params -> extras ignored, query succeeds using only the first
        rs = c.query("SELECT %s", (7, 8, 9))
        assert rs.rows == [(7,)], rs.rows
        c.close()

    def test_param_escaping__esc_bool_param_str_fallback(self):
        c = conn()
        rs = c.query("SELECT %s, %s", (True, False))
        # bools render as unquoted 1/0 (explicit bool branch in the escaper)
        assert rs.rows == [(1, 0)], rs.rows
        c.close()

    def test_param_escaping__esc_date_colon_format_bug(self):
        run_ddl('DROP TABLE IF EXISTS umysql_esc_date; CREATE TABLE umysql_esc_date (id INT NOT NULL, d DATE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        import datetime
        c = conn()
        c.query("DROP TABLE IF EXISTS umysql_esc_date")
        c.query("CREATE TABLE umysql_esc_date (id INT NOT NULL, d DATE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        import datetime
        # Driver formats date params with ':' ('2010:02:11'); MySQL accepts any
        # punctuation as a date delimiter, so it stores correctly.
        c.query("INSERT INTO umysql_esc_date (id, d) VALUES (%s, %s)", (1, datetime.date(2010, 2, 11)))
        got = c.query("SELECT d FROM umysql_esc_date WHERE id = %s", (1,)).rows[0][0]
        assert got == datetime.date(2010, 2, 11), repr(got)
        c.query("DROP TABLE IF EXISTS umysql_esc_date")
        c.close()

    def test_param_escaping__esc_embedded_nul_roundtrip(self):
        run_ddl('DROP TABLE IF EXISTS umysql_esc_nul; CREATE TABLE umysql_esc_nul (id INT NOT NULL, v VARBINARY(32)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("DROP TABLE IF EXISTS umysql_esc_nul")
        c.query("CREATE TABLE umysql_esc_nul (id INT NOT NULL, v VARBINARY(32)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        payload = b'a\x00b\x00c' if PY3 else 'a\x00b\x00c'
        c.query("INSERT INTO umysql_esc_nul (id, v) VALUES (%s, %s)", (1, payload))
        rs = c.query("SELECT v FROM umysql_esc_nul WHERE id = %s", (1,))
        got = rs.rows[0][0]
        expected = b'a\x00b\x00c' if PY3 else 'a\x00b\x00c'
        assert got == expected, repr(got)
        assert len(got) == 5, len(got)
        if PY3:
            assert isinstance(got, bytes), type(got)
        c.query("DROP TABLE IF EXISTS umysql_esc_nul")
        c.close()

    def test_param_escaping__esc_all_256_bytes_py3_bytes_param(self):
        run_ddl('DROP TABLE IF EXISTS umysql_esc_allbytes; CREATE TABLE umysql_esc_allbytes (id INT NOT NULL, b BLOB) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("DROP TABLE IF EXISTS umysql_esc_allbytes")
        c.query("CREATE TABLE umysql_esc_allbytes (id INT NOT NULL, b BLOB) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        if PY3:
            allbytes = bytes(range(256))
        else:
            allbytes = ''.join(chr(i) for i in range(256))
        c.query("INSERT INTO umysql_esc_allbytes (id, b) VALUES (%s, %s)", (1, allbytes))
        rs = c.query("SELECT b FROM umysql_esc_allbytes WHERE id = %s", (1,))
        got = rs.rows[0][0]
        assert len(got) == 256, len(got)
        expected = bytes(range(256)) if PY3 else ''.join(chr(i) for i in range(256))
        assert got == expected, repr(got[:16])
        if PY3:
            assert isinstance(got, bytes), type(got)
        c.query("DROP TABLE IF EXISTS umysql_esc_allbytes")
        c.close()

    def test_param_escaping__esc_injection_comment_and_stacked(self):
        run_ddl('DROP TABLE IF EXISTS umysql_esc_inj; CREATE TABLE umysql_esc_inj (id INT NOT NULL, name VARCHAR(128)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("DROP TABLE IF EXISTS umysql_esc_inj")
        c.query("CREATE TABLE umysql_esc_inj (id INT NOT NULL, name VARCHAR(128)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        payloads = [
            "'; DROP TABLE umysql_esc_inj; --",
            "admin'-- ",
            "x' OR '1'='1",
            'y" OR "1"="1',
        ]
        for i, p in enumerate(payloads):
            c.query("INSERT INTO umysql_esc_inj (id, name) VALUES (%s, %s)", (i, p))
        # table must still exist (stacked DROP did not execute) and store payloads verbatim
        for i, p in enumerate(payloads):
            rs = c.query("SELECT id FROM umysql_esc_inj WHERE name = %s", (p,))
            assert rs.rows == [(i,)], (i, rs.rows)
        rs = c.query("SELECT COUNT(*) FROM umysql_esc_inj")
        assert rs.rows == [(4,)], rs.rows
        c.query("DROP TABLE IF EXISTS umysql_esc_inj")
        c.close()

    def test_param_escaping__esc_long_param_crosses_heap_threshold(self):
        run_ddl('DROP TABLE IF EXISTS umysql_esc_long; CREATE TABLE umysql_esc_long (id INT NOT NULL, v LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("DROP TABLE IF EXISTS umysql_esc_long")
        c.query("CREATE TABLE umysql_esc_long (id INT NOT NULL, v LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        # 100000 chars; with a quote every 10 chars to exercise escaping inside the heap buffer
        unit = u"abcdefghi'" if PY3 else "abcdefghi'"
        big = unit * 10000  # 100000 chars, well over the 64KB estimate threshold
        c.query("INSERT INTO umysql_esc_long (id, v) VALUES (%s, %s)", (1, big))
        rs = c.query("SELECT v FROM umysql_esc_long WHERE id = %s", (1,))
        got = rs.rows[0][0]
        assert len(got) == 100000, len(got)
        assert got == big, (len(got), got[:20])
        assert got.count("'") == 10000, got.count("'")
        c.query("DROP TABLE IF EXISTS umysql_esc_long")
        c.close()

    def test_param_escaping__esc_empty_string_and_noparams_vs_empty_tuple(self):
        c = conn()
        # (1) empty string param -> empty quoted literal, length 0, type preserved on py3
        rs = c.query("SELECT %s", (b'' if PY3 else '',))
        got = rs.rows[0][0]
        assert len(got) == 0, len(got)
        gs = got.decode('utf-8') if isinstance(got, bytes) else got
        assert gs == '', repr(got)
        # (2) no-params-arg vs empty-tuple produce identical results for a param-free query
        a = c.query("SELECT 1 AS x").rows
        b = c.query("SELECT 1 AS x", ()).rows
        assert a == b == [(1,)], (a, b)
        c.close()

    def test_param_escaping__esc_huge_int_64byte_estimate(self):
        run_ddl('DROP TABLE IF EXISTS umysql_esc_bigint; CREATE TABLE umysql_esc_bigint (id INT NOT NULL, v VARCHAR(255)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query("DROP TABLE IF EXISTS umysql_esc_bigint")
        c.query("CREATE TABLE umysql_esc_bigint (id INT NOT NULL, v VARCHAR(255)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        big = 10 ** 100  # str() = 101 chars -> exceeds the old 64-byte per-arg estimate
        if PY3:
            # The PyObject_Str fallback writes 101 bytes; the buffer-size fix
            # prevents the client-side output-buffer overflow, and MySQL 8 returns
            # the literal (as DOUBLE) without error.
            rs = c.query("SELECT %s", (big,))
            assert len(rs.rows) == 1, rs.rows
        # NOTE: on py2 the *original* umysql also hangs sending a >65-digit numeric
        # literal to MySQL 8 (pre-existing, not a port regression), so we don't send
        # one there. A moderately large int (fits DECIMAL(65)) round-trips on both:
        mid = 10 ** 40
        got = c.query("SELECT %s", (mid,)).rows[0][0]
        gs = got.decode('utf-8') if isinstance(got, bytes) else got
        assert gs == str(mid), repr(got)
        c.query("DROP TABLE IF EXISTS umysql_esc_bigint")
        c.close()

    def test_conn_lifecycle__conn_5arg_form(self):
        c = umysql.Connection()
        # exactly 5 positional args -- exercises the |Os optional tail being absent
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB)
        assert c.is_connected() is True
        rs = c.query('SELECT %s', (u'héllo',))  # non-ascii unicode, default utf-8 codec must encode it
        assert rs.rows[0][0] == u'héllo'
        if PY3:
            assert isinstance(rs.rows[0][0], str)
        else:
            assert isinstance(rs.rows[0][0], unicode)
        c.close()

    def test_conn_lifecycle__conn_invalid_charset_valueerror(self):
        c = umysql.Connection()
        try:
            c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'utf16')
            assert False, 'expected ValueError'
        except ValueError as e:
            assert str(e) == "Unsupported character set 'utf16' specified", repr(str(e))
        assert c.is_connected() is False
        c.close()

    def test_conn_lifecycle__conn_latin1_charset_roundtrip(self):
        run_ddl('DROP TABLE IF EXISTS umysql_lc_latin1; CREATE TABLE umysql_lc_latin1 (v VARCHAR(32)) ENGINE=InnoDB DEFAULT CHARSET=latin1;')
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'latin1')
        c.query('DROP TABLE IF EXISTS umysql_lc_latin1')
        c.query('CREATE TABLE umysql_lc_latin1 (v VARCHAR(32)) ENGINE=InnoDB DEFAULT CHARSET=latin1')
        c.query('INSERT INTO umysql_lc_latin1 (v) VALUES (%s)', (u'café',))  # encoded via latin-1 codec
        rs = c.query('SELECT v FROM umysql_lc_latin1')
        assert rs.rows[0][0] == u'café', repr(rs.rows[0][0])
        if PY3:
            assert isinstance(rs.rows[0][0], str)
        c.query('DROP TABLE umysql_lc_latin1')
        c.close()

    def test_conn_lifecycle__conn_ascii_charset_encode_fails(self):
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'ascii')
        raised = False
        try:
            c.query('SELECT %s', (u'snöw',))  # non-ascii -> cannot encode as ascii
        except UnicodeEncodeError:
            raised = True
        except ValueError:
            raised = True  # UnicodeEncodeError is a ValueError subclass
        assert raised, 'expected UnicodeEncodeError for non-ascii param on ascii connection'
        # connection is still alive (encode failed client-side, no server round-trip)
        assert c.is_connected() is True
        assert c.query('SELECT %s', ('plain_ascii',)).rows[0][0] == 'plain_ascii'
        c.close()

    def test_conn_lifecycle__conn_autocommit_false_rollback_isolation(self):
        run_ddl('DROP TABLE IF EXISTS umysql_lc_ac; CREATE TABLE umysql_lc_ac (id INT PRIMARY KEY AUTO_INCREMENT, v INT) ENGINE=InnoDB;')
        h,p,u,pw,db = DB_HOST,DB_PORT,DB_USER,DB_PASSWD,DB_DB
        setup = umysql.Connection(); setup.connect(h,p,u,pw,db,True,'utf8mb4')
        setup.query('DROP TABLE IF EXISTS umysql_lc_ac')
        setup.query('CREATE TABLE umysql_lc_ac (id INT PRIMARY KEY AUTO_INCREMENT, v INT) ENGINE=InnoDB')
        setup.close()
        a = umysql.Connection(); a.connect(h,p,u,pw,db,False,'utf8mb4')  # autocommit OFF
        a.query('INSERT INTO umysql_lc_ac (v) VALUES (%s)', (42,))
        # a sees its own uncommitted row
        assert a.query('SELECT COUNT(*) FROM umysql_lc_ac').rows[0][0] == 1
        b = umysql.Connection(); b.connect(h,p,u,pw,db,True,'utf8mb4')  # separate connection
        assert b.query('SELECT COUNT(*) FROM umysql_lc_ac').rows[0][0] == 0, 'uncommitted write leaked across connections'
        a.close()  # rolls back (QUIT without commit)
        assert b.query('SELECT COUNT(*) FROM umysql_lc_ac').rows[0][0] == 0
        b.query('DROP TABLE umysql_lc_ac')
        b.close()

    def test_conn_lifecycle__conn_double_connect_error_shape(self):
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'utf8mb4')
        try:
            c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'utf8mb4')
            assert False, 'expected umysql.Error on double connect'
        except umysql.Error as e:
            assert isinstance(e.args[0], int), type(e.args[0])
            assert e.args[0] == 0, e.args[0]
            assert e.args[1] == 'Socket already connected', repr(e.args[1])
        # UME_OTHER setError closed the socket
        assert c.is_connected() is False
        c.close()

    def test_conn_lifecycle__conn_query_before_connect(self):
        c = umysql.Connection()  # never connected
        try:
            c.query('SELECT 1')
            assert False, 'expected RuntimeError'
        except RuntimeError as e:
            assert str(e) == 'Not connected', repr(str(e))
        assert c.is_connected() is False

    def test_conn_lifecycle__conn_close_twice_idempotent(self):
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'utf8mb4')
        assert c.close() is None
        assert c.is_connected() is False
        # second close on the same object -- idempotent, no raise
        assert c.close() is None
        # close on an object that was never connected
        fresh = umysql.Connection()
        assert fresh.close() is None
        assert fresh.is_connected() is False

    def test_conn_lifecycle__conn_wrong_creds_error_type_not_value(self):
        c = umysql.Connection()
        raised = False
        try:
            c.connect(DB_HOST, DB_PORT, 'no_such_user_xyz', 'wrong_pw', DB_DB, True, 'utf8mb4')
        except umysql.SQLError as e:
            raised = True
            assert isinstance(e.args[0], int), type(e.args[0])
            assert e.args[0] != 0  # a real MySQL errno, do not hardcode 1045
            assert isinstance(e.args[1], (str, bytes)) and len(e.args[1]) > 0
        assert raised, 'expected umysql.SQLError for bad credentials'
        # is_connected() state after a failed auth is driver-internal; not asserted.
        c.close()

    def test_conn_lifecycle__conn_reconnect_same_object(self):
        args = (DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'utf8mb4')
        c = umysql.Connection()
        c.connect(*args)
        assert c.query('SELECT 1').rows == [(1,)]
        c.close()
        assert c.is_connected() is False
        # reconnect on the SAME object
        c.connect(*args)
        assert c.is_connected() is True
        assert c.query('SELECT %s', (7,)).rows == [(7,)]
        c.close()

    def test_conn_lifecycle__conn_cp1250_charset_roundtrip(self):
        run_ddl('DROP TABLE IF EXISTS umysql_lc_cp1250; CREATE TABLE umysql_lc_cp1250 (v VARCHAR(32) CHARACTER SET cp1250) ENGINE=InnoDB;')
        c = umysql.Connection()
        c.connect(DB_HOST, DB_PORT, DB_USER, DB_PASSWD, DB_DB, True, 'cp1250')
        c.query('DROP TABLE IF EXISTS umysql_lc_cp1250')
        c.query('CREATE TABLE umysql_lc_cp1250 (v VARCHAR(32) CHARACTER SET cp1250) ENGINE=InnoDB')
        c.query('INSERT INTO umysql_lc_cp1250 (v) VALUES (%s)', (u'ő',))  # cp1250-only char, encoded via cp1250 codec
        rs = c.query('SELECT v FROM umysql_lc_cp1250')
        assert rs.rows[0][0] == u'ő', repr(rs.rows[0][0])
        if PY3:
            assert isinstance(rs.rows[0][0], str)
        c.query('DROP TABLE umysql_lc_cp1250')
        c.close()

    def test_resultset_protocol__rs_field_type_codes(self):
        run_ddl("DROP TABLE IF EXISTS rs_typecodes;\nCREATE TABLE rs_typecodes (i int, b bigint, bu bigint unsigned, d decimal(10,2), t text, vb varbinary(20)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nINSERT INTO rs_typecodes VALUES (1, 2, 3, 4.50, 'hi', _binary'xy');")
        c = conn()
        rs = c.query('SELECT i, b, bu, d, t, vb FROM rs_typecodes')
        codes = [f[1] for f in rs.fields]
        # MFTYPE: LONG=3, LONGLONG=8, NEWDECIMAL=246, BLOB(text)=252, VAR_STRING(varbinary)=253
        assert codes == [3, 8, 8, 246, 252, 253], codes
        # every field[1] is a plain int type code
        assert all(isinstance(f[1], int) for f in rs.fields)
        # field count matches column count
        assert len(rs.fields) == 6
        c.close()

    def test_resultset_protocol__rs_decimal_text_binary_decode(self):
        run_ddl("DROP TABLE IF EXISTS rs_decode;\nCREATE TABLE rs_decode (d decimal(10,2), t text, vb varbinary(20)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nINSERT INTO rs_decode VALUES (4.50, 'hi', _binary'xy');")
        c = conn()
        d, t, vb = c.query('SELECT d, t, vb FROM rs_decode').rows[0]
        if PY3:
            assert isinstance(d, str), type(d)
            assert isinstance(t, str), type(t)
            assert isinstance(vb, bytes), type(vb)
        else:
            assert isinstance(d, str), type(d)        # DECIMAL -> bytes/str on py2 (faithful)
            assert isinstance(t, unicode), type(t)
            assert isinstance(vb, str), type(vb)
        # decimal keeps its textual form including trailing zero (not coerced to float)
        assert d == (u'4.50' if PY3 else u'4.50'), repr(d)
        assert t == u'hi'
        assert vb == (b'xy' if PY3 else 'xy')
        c.close()

    def test_resultset_protocol__rs_all_null_row(self):
        run_ddl('DROP TABLE IF EXISTS rs_allnull;\nCREATE TABLE rs_allnull (a int, b text, d decimal(8,2), dt datetime, x bigint) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nINSERT INTO rs_allnull (a) VALUES (NULL);')
        c = conn()
        rs = c.query('SELECT a, b, d, dt, x FROM rs_allnull')
        assert len(rs.rows) == 1
        row = rs.rows[0]
        assert row == (None, None, None, None, None), row
        # each element must be the singleton None (identity), not a falsy lookalike
        assert all(v is None for v in row)
        assert len(row) == 5
        # inline 5-column all-NULL SELECT (no table) gives the same shape
        assert c.query('SELECT NULL, NULL, NULL').rows[0] == (None, None, None)
        c.close()

    def test_resultset_protocol__rs_multirow_insert_lastrowid_first(self):
        run_ddl('DROP TABLE IF EXISTS rs_multi;\nCREATE TABLE rs_multi (id int primary key auto_increment, v int) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nALTER TABLE rs_multi AUTO_INCREMENT=50;')
        c = conn()
        rc = c.query('INSERT INTO rs_multi (v) VALUES (1),(2),(3)')
        assert isinstance(rc, tuple) and len(rc) == 2
        rowcount, lastrowid = rc
        assert rowcount == 3, rowcount
        # lastrowid is the FIRST inserted id (50), not the last (52)
        assert lastrowid == 50, lastrowid
        # confirm the three ids actually landed at 50,51,52
        ids = [r[0] for r in c.query('SELECT id FROM rs_multi ORDER BY id').rows]
        assert ids == [50, 51, 52], ids
        c.close()

    def test_resultset_protocol__rs_noop_update_matched(self):
        run_ddl('DROP TABLE IF EXISTS rs_noop;\nCREATE TABLE rs_noop (id int primary key, v int) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nINSERT INTO rs_noop VALUES (1, 5);')
        c = conn()
        # value already 5 -> no actual change, but the row IS matched
        rc = c.query('UPDATE rs_noop SET v=5 WHERE id=1')
        assert rc == (1, 0), rc  # matched row counted, lastrowid 0
        # an UPDATE that matches NO row reports 0
        rc2 = c.query('UPDATE rs_noop SET v=9 WHERE id=999')
        assert rc2 == (0, 0), rc2
        # a real change also reports 1
        rc3 = c.query('UPDATE rs_noop SET v=6 WHERE id=1')
        assert rc3 == (1, 0), rc3
        c.close()

    def test_resultset_protocol__rs_zero_column_statements(self):
        c = conn()
        r1 = c.query('DO 1')
        assert isinstance(r1, tuple) and r1 == (0, 0), r1
        assert not hasattr(r1, 'rows')
        r2 = c.query('SET @x = 1')
        assert isinstance(r2, tuple) and r2 == (0, 0), r2
        # and a normal SELECT right after still returns a ResultSet (state reset)
        rs = c.query('SELECT @x')
        assert hasattr(rs, 'rows') and rs.rows == [(1,)], rs.rows
        c.close()

    def test_resultset_protocol__rs_fresh_resultset_per_query(self):
        run_ddl('DROP TABLE IF EXISTS rs_state;\nCREATE TABLE rs_state (id int) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nINSERT INTO rs_state VALUES (1),(2),(3);')
        c = conn()
        a = c.query('SELECT id FROM rs_state ORDER BY id')
        first_snapshot = list(a.rows)
        assert first_snapshot == [(1,), (2,), (3,)]
        b = c.query('SELECT id FROM rs_state WHERE id = 1')
        # distinct ResultSet objects and distinct underlying lists
        assert a is not b
        assert a.rows is not b.rows
        # the second query must NOT have mutated the first result
        assert a.rows == [(1,), (2,), (3,)], a.rows
        assert b.rows == [(1,)], b.rows
        c.close()

    def test_resultset_protocol__rs_large_single_text_value(self):
        c = conn()
        n = 2 * 1024 * 1024  # 2 MB, within the ~16MB rx buffer, spans many wire packets
        rs = c.query('SELECT REPEAT(%s, %s)', ('a', n))
        v = rs.rows[0][0]
        assert len(v) == n, len(v)
        if PY3:
            assert isinstance(v, str), type(v)
        else:
            assert isinstance(v, unicode), type(v)
        assert v == (u'a' * n)
        # fields/shape still correct for the single huge column
        assert len(rs.fields) == 1
        assert len(rs.rows) == 1
        c.close()

    def test_resultset_protocol__rs_many_rows_integrity(self):
        run_ddl('DROP TABLE IF EXISTS rs_many;\nCREATE TABLE rs_many (id int primary key, v int) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        # bulk-load 5000 rows quickly via a recursive CTE -> single INSERT...SELECT
        c.query('SET SESSION cte_max_recursion_depth = 20000')
        c.query('INSERT INTO rs_many (id, v) SELECT n, n*2 FROM (WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n < 5000) SELECT n FROM seq) t')
        rs = c.query('SELECT id, v FROM rs_many ORDER BY id')
        assert len(rs.rows) == 5000, len(rs.rows)
        # order preserved and values intact at the boundaries and a midpoint
        assert rs.rows[0] == (1, 2)
        assert rs.rows[2499] == (2500, 5000)
        assert rs.rows[-1] == (5000, 10000)
        # full integrity: every row satisfies v == id*2 and ids are 1..5000 in order
        assert all(r == (i + 1, (i + 1) * 2) for i, r in enumerate(rs.rows))
        c.close()

    def test_resultset_protocol__single_large_value_under_buffer(self):
        # A large single value UNDER the ~16MB rx buffer round-trips correctly.
        # The value is built server-side with REPEAT() so the query text stays
        # tiny -- otherwise the INSERT literal would exceed the 4MB TX buffer and
        # raise "Query too big" (a separate send-side limit, unrelated to the rx
        # path under test). (A single value LARGER than the rx buffer is a
        # pre-existing umysql limitation: MySQL splits it into multi-part
        # protocol packets that umysql does not reassemble; this affects the
        # production 2.63.7 build too. The freeSpace fix handles results that are
        # large in TOTAL across many rows, not one value > 16MB.)
        BIG = 8 * 1024 * 1024   # 8MB < 16MB rx buffer
        c = conn()
        assert c.rxBufferSize >= 16 * 1024 * 1024
        c.query('DROP TABLE IF EXISTS sv')
        c.query('CREATE TABLE sv(t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO sv VALUES (REPEAT(%s, %s))', ('y', BIG))
        got = c.query('SELECT t FROM sv').rows[0][0]
        assert len(got) == BIG, len(got)
        assert got == ('y' * BIG) if isinstance(got, str) else got == (b'y' * BIG)
        c.query('DROP TABLE sv'); c.close()

    def test_concurrency_gevent__gevent_nonblocking_parallel_sleep(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        try:
            import gevent
            from gevent import monkey
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        import time
        N = 5
        SLEEP = 0.5
        conns = [conn() for _ in range(N)]
        results = {}
        def work(i, cn):
            rs = cn.query('SELECT SLEEP(%s)', (SLEEP,))
            results[i] = rs.rows
        start = time.time()
        jobs = [gevent.spawn(work, i, conns[i]) for i in range(N)]
        gevent.joinall(jobs, timeout=30)
        elapsed = time.time() - start
        for j in jobs:
            assert j.successful(), 'greenlet raised: %r' % (j.exception,)
        # All N queries ran concurrently: wall time must be close to one SLEEP,
        # far below the serialized lower bound of N*SLEEP.
        assert elapsed < (N * SLEEP) * 0.6, 'queries serialized (no yield): elapsed=%.3f vs serial=%.3f' % (elapsed, N*SLEEP)
        assert all(results[i] == [(0,)] for i in range(N)), results
        for cn in conns:
            cn.close()

    def test_concurrency_gevent__gevent_error_isolation_across_greenlets(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        run_ddl("DROP TABLE IF EXISTS gevent_iso_test;\nCREATE TABLE gevent_iso_test (id INT PRIMARY KEY, v VARCHAR(16)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nINSERT INTO gevent_iso_test VALUES (1,'ok'),(2,'ok'),(3,'ok');")
        try:
            import gevent
            from gevent import monkey
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        outcomes = {}
        def good(i, cn):
            rs = cn.query('SELECT SLEEP(0.2); ')  # yields, then real read below
            rs = cn.query('SELECT v FROM gevent_iso_test WHERE id=%s', (i,))
            outcomes[i] = ('ok', rs.rows)
        def bad(cn):
            try:
                cn.query('SELECT * FROM no_such_table_zzz')
                outcomes['bad'] = ('noerror', None)
            except umysql.SQLError as e:
                outcomes['bad'] = ('sqlerror', e.args[0])
        c1, c2, c3, cbad = conn(), conn(), conn(), conn()
        jobs = [gevent.spawn(good, 1, c1), gevent.spawn(bad, cbad), gevent.spawn(good, 2, c2), gevent.spawn(good, 3, c3)]
        gevent.joinall(jobs, timeout=30)
        assert outcomes['bad'][0] == 'sqlerror', outcomes['bad']
        assert outcomes['bad'][1] == 1146, outcomes['bad']  # ER_NO_SUCH_TABLE
        for i in (1, 2, 3):
            assert outcomes[i] == ('ok', [('ok',)]), (i, outcomes.get(i))
        # The connection that errored is still usable after the error
        rs = cbad.query('SELECT 1')
        assert rs.rows == [(1,)], rs.rows
        for cn in (c1, c2, c3, cbad):
            cn.close()

    def test_concurrency_gevent__gevent_many_short_queries_independent(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        try:
            import gevent
            from gevent import monkey
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        N = 40
        conns = [conn() for _ in range(N)]
        results = {}
        def work(i, cn):
            # interleave several round-trips so greenlets yield repeatedly
            for _ in range(3):
                rs = cn.query('SELECT %s AS a, %s AS b', (i, i * 1000))
                results[i] = rs.rows
        gevent.joinall([gevent.spawn(work, i, conns[i]) for i in range(N)], timeout=60)
        for i in range(N):
            assert results[i] == [(i, i * 1000)], 'cross-talk: greenlet %d got %r' % (i, results.get(i))
        for cn in conns:
            cn.close()

    def test_concurrency_gevent__gevent_shared_connection_mid_query_corrupts(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        try:
            import gevent
            from gevent import monkey
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        shared = conn()
        errors = []
        rows_seen = []
        def work(i):
            try:
                rs = shared.query('SELECT SLEEP(0.2), %s', (i,))
                rows_seen.append(rs.rows)
            except Exception as e:
                errors.append(type(e).__name__)
        jobs = [gevent.spawn(work, i) for i in range(4)]
        gevent.joinall(jobs, timeout=30)
        # Concurrent use of one connection must NOT silently return 4 correct independent
        # results -- it either raises (protocol/runtime error) or yields garbage.
        clean = (len(errors) == 0 and sorted(rows_seen) == [[(0, 0)], [(0, 1)], [(0, 2)], [(0, 3)]])
        assert not clean, 'shared connection unexpectedly behaved as if safe: rows=%r errors=%r' % (rows_seen, errors)
        assert len(errors) >= 1 or len(rows_seen) < 4, 'expected corruption signal, got rows=%r errors=%r' % (rows_seen, errors)
        try:
            shared.close()
        except Exception:
            pass

    def test_concurrency_gevent__gevent_concurrent_connect_nonblocking(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        try:
            import gevent
            from gevent import monkey
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        import time
        N = 8
        opened = []
        def opener():
            cn = conn()  # conn() performs connect()
            rs = cn.query('SELECT 1')
            assert rs.rows == [(1,)], rs.rows
            opened.append(cn)
        start = time.time()
        gevent.joinall([gevent.spawn(opener) for _ in range(N)], timeout=30)
        elapsed = time.time() - start
        assert len(opened) == N, 'only %d/%d connections opened' % (len(opened), N)
        # Opening N connections concurrently should be far faster than N sequential
        # round-trips; mainly we assert all succeeded and it finished promptly.
        assert elapsed < 10.0, 'concurrent connect too slow: %.2fs' % elapsed
        for cn in opened:
            cn.close()

    def test_concurrency_gevent__gevent_timeout_interrupts_query(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        try:
            import gevent
            from gevent import monkey, Timeout
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        import time
        cn = conn()
        fired = []
        start = time.time()
        try:
            with Timeout(0.3):
                cn.query('SELECT SLEEP(3)')
            fired.append('no_timeout')
        except Timeout:
            fired.append('timeout')
        elapsed = time.time() - start
        assert fired == ['timeout'], fired
        # Timeout must fire near 0.3s, NOT after the full 3s sleep -- proving recv yielded.
        assert elapsed < 1.5, 'Timeout did not interrupt blocking query (recv not yielding): %.2fs' % elapsed
        try:
            cn.close()
        except Exception:
            pass
        # A fresh connection still works fine -- the hub was not wedged.
        cn2 = conn()
        assert cn2.query('SELECT 7').rows == [(7,)]
        cn2.close()

    def test_concurrency_gevent__gevent_high_concurrency_no_leak_under_load(self):
        if not HAVE_GEVENT:
            self.skipTest("gevent not installed")
        try:
            import gevent
            from gevent import monkey
            monkey.patch_all()
        except ImportError:
            raise unittest.SkipTest('gevent not importable')
        WORKERS = 12
        ITERS = 25
        bad = []
        def worker(w):
            cn = conn()
            try:
                for k in range(ITERS):
                    val = w * 1000 + k
                    rs = cn.query("SELECT %s AS n, 'gevent_load' AS s", (val,))
                    r = rs.rows
                    if r != [(val, 'gevent_load')]:
                        bad.append((w, k, r))
                    rs = None  # drop ResultSet ref each iter to exercise dealloc path
            finally:
                cn.close()
        gevent.joinall([gevent.spawn(worker, w) for w in range(WORKERS)], timeout=120)
        assert bad == [], 'incorrect rows under load: %r' % bad[:5]
        if PY3:
            rs = conn().query("SELECT 'x'")
            assert isinstance(rs.rows[0][0], str)
        else:
            rs = conn().query("SELECT 'x'")
            assert isinstance(rs.rows[0][0], unicode)

    def test_resource_refcount__mem_01(self):
        import sys, gc
        c = conn()
        s = u'leak_probe_é中' * 5
        gc.collect()
        before = sys.getrefcount(s)
        for _ in range(2000):
            c.query('SELECT %s', (s,))
        gc.collect()
        after = sys.getrefcount(s)
        c.close()
        assert after == before, 'unicode param refcount drifted: %d -> %d (leak or over-decref in PyUnicode_AsEncodedString escape path)' % (before, after)

    def test_resource_refcount__mem_02(self):
        import sys, gc
        c = conn()
        n = 1234567890123 + 777   # large, not a cached small int
        f = 3.14159265358979 + 0.0001
        gc.collect()
        bn = sys.getrefcount(n); bf = sys.getrefcount(f)
        for _ in range(2000):
            c.query('SELECT %s', (n,))
        for _ in range(2000):
            c.query('SELECT %s', (f,))
        gc.collect()
        an = sys.getrefcount(n); af = sys.getrefcount(f)
        c.close()
        assert an == bn, 'int param refcount drifted %d -> %d (py3 PyObject_Str fallback leak)' % (bn, an)
        assert af == bf, 'float param refcount drifted %d -> %d (py3 PyObject_Str fallback leak)' % (bf, af)

    def test_resource_refcount__mem_03(self):
        import sys, gc
        c = conn()
        s = u'shared_arg_é'
        five = (s, s, s, s, s)
        gc.collect()
        before = sys.getrefcount(s)
        for _ in range(2000):
            c.query('SELECT %s,%s,%s,%s,%s', five)
        gc.collect()
        after = sys.getrefcount(s)
        c.close()
        assert after == before, 'shared-arg refcount drifted %d -> %d (per-placeholder decref imbalance in EscapeQueryArguments)' % (before, after)

    def test_resource_refcount__mem_04(self):
        import sys, gc
        c = conn('ascii')
        bad = u'caf\xe9 \u20ac leak'   # non-ASCII: unencodable in the ascii connection charset on py2 + py3
        gc.collect()
        before = sys.getrefcount(bad)
        errs = 0
        for _ in range(2000):
            try:
                c.query('SELECT %s', (bad,))
            except (UnicodeError, ValueError):
                errs += 1
        gc.collect()
        after = sys.getrefcount(bad)
        assert errs == 2000, 'expected 2000 encode failures, got %d' % errs
        assert after == before, 'encode-fail arg refcount drifted %d -> %d (leak/over-decref on error path)' % (before, after)
        assert c.query('SELECT 1').rows == [(1,)], 'connection unusable after encode-failure error path'
        c.close()

    def test_resource_refcount__mem_05(self):
        import sys, gc
        import umysql
        c = conn()
        probe = u'mismatch_probe'
        gc.collect()
        before = sys.getrefcount(probe)
        errs = 0
        for _ in range(2000):
            try:
                c.query('SELECT %s, %s', (probe,))   # 2 placeholders, 1 arg
            except ValueError:
                errs += 1
        gc.collect()
        after = sys.getrefcount(probe)
        assert errs == 2000, 'expected 2000 ValueErrors for count mismatch, got %d' % errs
        assert after == before, 'count-mismatch arg refcount drifted %d -> %d' % (before, after)
        assert c.query('SELECT 1').rows == [(1,)], 'connection unusable after count-mismatch error path'
        c.close()

    def test_resource_refcount__mem_06(self):
        import sys, gc
        c = conn()
        badq = u'SELECT 100 % 7'   # bare '%' followed by space -> strict ValueError
        gc.collect()
        before = sys.getrefcount(badq)
        errs = 0
        for _ in range(2000):
            try:
                c.query(badq, ())
            except ValueError:
                errs += 1
        gc.collect()
        after = sys.getrefcount(badq)
        assert errs == 2000, 'expected 2000 ValueErrors for bare percent, got %d' % errs
        assert after == before, 'percent-error query refcount drifted %d -> %d (encoded-query/buffer leak on strict-reject path)' % (before, after)
        assert c.query('SELECT 1').rows == [(1,)]
        c.close()

    def test_resource_refcount__mem_07(self):
        import sys, gc
        c = conn()
        qu = u'SELECT %s'
        qb = b'SELECT %s'
        gc.collect()
        bu = sys.getrefcount(qu); bb = sys.getrefcount(qb)
        for _ in range(2000):
            c.query(qu, (1,))
        for _ in range(2000):
            c.query(qb, (1,))
        gc.collect()
        au = sys.getrefcount(qu); ab = sys.getrefcount(qb)
        c.close()
        assert au == bu, 'unicode query refcount drifted %d -> %d (PyUnicode_AsEncodedString not balanced in Connection_query)' % (bu, au)
        assert ab == bb, 'bytes query refcount drifted %d -> %d (INCREF/DECREF imbalance in Connection_query)' % (bb, ab)

    def test_resource_refcount__mem_08(self):
        import gc, os
        import umysql
        HOST='127.0.0.1'; PORT=3306; USER='gevent_test'; PWD='gevent_test'; DB='gevent_test'
        def fdcount():
            return len(os.listdir('/proc/self/fd'))
        warm = conn(); warm.close()
        gc.collect()
        start = fdcount()
        for _ in range(400):
            cc = umysql.Connection()
            cc.connect(HOST, PORT, USER, PWD, DB, True, 'utf8mb4')
            cc.query('SELECT 1')
            cc.close()
            del cc
        gc.collect()
        end = fdcount()
        assert end - start <= 2, 'fd leak across 400 connect/close cycles: %d -> %d' % (start, end)
        gc.collect()
        s2 = fdcount()
        fails = 0
        for _ in range(200):
            cc = umysql.Connection()
            try:
                cc.connect(HOST, 31337, 'x', 'x', 'x')   # connection refused
            except Exception:
                fails += 1
            try:
                cc.close()
            except Exception:
                pass
            del cc
        gc.collect()
        e2 = fdcount()
        assert fails == 200, 'expected 200 connect failures, got %d' % fails
        assert e2 - s2 <= 2, 'fd leak across 200 failed-connect cycles: %d -> %d' % (s2, e2)

    def test_resource_refcount__mem_09(self):
        run_ddl('DROP TEMPORARY TABLE IF EXISTS leakbig;')
        import sys, gc
        c = conn()
        c.query('DROP TEMPORARY TABLE IF EXISTS leakbig')
        c.query('CREATE TEMPORARY TABLE leakbig (id INT, s VARCHAR(60))')
        for i in range(0, 4000, 200):
            vals = ','.join('(%d,"row%d")' % (j, j) for j in range(i, i + 200))
            c.query('INSERT INTO leakbig VALUES ' + vals)
        rs = c.query('SELECT id, s FROM leakbig ORDER BY id')
        assert isinstance(rs.rows, list) and len(rs.rows) == 4000
        assert isinstance(rs.rows[0], tuple)
        assert rs.rows[0][0] == 0 and rs.rows[-1][0] == 3999
        if PY3:
            assert isinstance(rs.rows[0][1], str), 'text column should be py3 str, got %r' % type(rs.rows[0][1])
        assert sys.getrefcount(rs) == 2, 'fresh ResultSet refcount expected 2, got %d' % sys.getrefcount(rs)
        del rs
        gc.collect()
        for _ in range(300):
            r = c.query('SELECT id, s FROM leakbig ORDER BY id')
            assert len(r.rows) == 4000
            del r
        gc.collect()
        assert c.query('SELECT 1').rows == [(1,)], 'connection unusable after 300 large-ResultSet build/free cycles'
        c.close()

    def test_security_injection__esc_trailing_backslash(self):
        run_ddl('DROP TABLE IF EXISTS esc_bs; CREATE TABLE esc_bs (id INT NOT NULL AUTO_INCREMENT, v VARCHAR(64) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        payload = '\\'  # a single backslash character
        c.query('INSERT INTO esc_bs (v) VALUES (%s)', (payload,))
        c.query('INSERT INTO esc_bs (v) VALUES (%s)', ('decoy',))
        # round-trip byte exact
        rs = c.query('SELECT v FROM esc_bs WHERE v = %s', (payload,))
        assert len(rs.rows) == 1, rs.rows
        stored = rs.rows[0][0]
        if isinstance(stored, bytes):
            stored = stored.decode('utf-8')
        assert stored == u'\\', repr(stored)
        # structural proof: the trailing backslash did not turn the closing quote into part of the literal;
        # a non-matching value must return 0 rows (not all rows)
        rs2 = c.query('SELECT v FROM esc_bs WHERE v = %s', ('nope',))
        assert rs2.rows == [], rs2.rows
        c.close()

    def test_security_injection__esc_param_is_closing_quote(self):
        run_ddl('DROP TABLE IF EXISTS esc_q; CREATE TABLE esc_q (id INT NOT NULL AUTO_INCREMENT, v VARCHAR(64) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query('INSERT INTO esc_q (v) VALUES (%s)', ("'",))
        c.query('INSERT INTO esc_q (v) VALUES (%s)', ('other',))
        rs = c.query('SELECT v, LENGTH(v) FROM esc_q WHERE v = %s', ("'",))
        assert len(rs.rows) == 1, rs.rows
        val, blen = rs.rows[0]
        if isinstance(val, bytes):
            val = val.decode('utf-8')
        assert val == u"'", repr(val)
        assert blen == 1, blen  # exactly one byte stored, not zero, not two
        c.close()

    def test_security_injection__esc_double_quote_branch(self):
        run_ddl('DROP TABLE IF EXISTS esc_dq; CREATE TABLE esc_dq (id INT NOT NULL AUTO_INCREMENT, v VARCHAR(128) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        payload = 'a" OR "1"="1'
        c.query('INSERT INTO esc_dq (v) VALUES (%s)', (payload,))
        c.query('INSERT INTO esc_dq (v) VALUES (%s)', ('benign',))
        rs = c.query('SELECT v FROM esc_dq WHERE v = %s', (payload,))
        assert len(rs.rows) == 1, rs.rows
        stored = rs.rows[0][0]
        if isinstance(stored, bytes):
            stored = stored.decode('utf-8')
        assert stored == payload, repr(stored)
        # injection attempt with the double-quote OR must NOT match the benign row
        rs2 = c.query('SELECT v FROM esc_dq WHERE v = %s', ('benign" OR "1"="1',))
        assert rs2.rows == [], rs2.rows
        c.close()

    def test_security_injection__esc_newline_cr_statement_split(self):
        run_ddl('DROP TABLE IF EXISTS esc_nl; CREATE TABLE esc_nl (id INT NOT NULL AUTO_INCREMENT, v VARCHAR(128) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        payload = "x'\n;DROP TABLE esc_nl;--\r"
        c.query('INSERT INTO esc_nl (v) VALUES (%s)', (payload,))
        rs = c.query('SELECT v FROM esc_nl WHERE v = %s', (payload,))
        assert len(rs.rows) == 1, rs.rows
        stored = rs.rows[0][0]
        if isinstance(stored, bytes):
            stored = stored.decode('utf-8')
        assert stored == payload, repr(stored)
        assert u'\n' in stored and u'\r' in stored, repr(stored)
        # table must still exist and be queryable -> the DROP never executed
        rs2 = c.query('SELECT COUNT(*) FROM esc_nl')
        assert rs2.rows[0][0] == 1, rs2.rows
        c.close()

    def test_security_injection__esc_ctrlz_substitute(self):
        run_ddl('DROP TABLE IF EXISTS esc_z; CREATE TABLE esc_z (id INT NOT NULL AUTO_INCREMENT, v VARBINARY(64) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        payload = b'before\x1aafter'
        c.query('INSERT INTO esc_z (v) VALUES (%s)', (payload,))
        rs = c.query('SELECT v, LENGTH(v) FROM esc_z WHERE v = %s', (payload,))
        assert len(rs.rows) == 1, rs.rows
        stored, blen = rs.rows[0]
        if not isinstance(stored, bytes):
            stored = stored.encode('latin-1')
        assert stored == payload, repr(stored)
        assert blen == len(payload), blen  # 12 bytes, ctrl-Z counted as exactly one byte
        c.close()

    def test_security_injection__esc_embedded_nul(self):
        run_ddl('DROP TABLE IF EXISTS esc_nul; CREATE TABLE esc_nul (id INT NOT NULL AUTO_INCREMENT, v VARBINARY(64) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        payload = b'head\x00tail'
        c.query('INSERT INTO esc_nul (v) VALUES (%s)', (payload,))
        rs = c.query('SELECT v, LENGTH(v) FROM esc_nul WHERE v = %s', (payload,))
        assert len(rs.rows) == 1, rs.rows
        stored, blen = rs.rows[0]
        if not isinstance(stored, bytes):
            stored = stored.encode('latin-1')
        assert stored == payload, repr(stored)
        assert blen == 9, blen  # 'head' + NUL + 'tail' = 9 bytes, nothing truncated at the NUL
        c.close()

    def test_security_injection__esc_comment_and_stacked_payloads(self):
        run_ddl('DROP TABLE IF EXISTS esc_cmt; CREATE TABLE esc_cmt (id INT NOT NULL AUTO_INCREMENT, v VARCHAR(128) NOT NULL, secret INT NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        for name in ('alice', 'bob', 'carol'):
            c.query('INSERT INTO esc_cmt (v, secret) VALUES (%s, %s)', (name, 42))
        injections = [
            "alice'--",
            "alice'-- ",
            "alice'/*",
            "alice' OR 1=1 -- ",
            "alice'; DROP TABLE esc_cmt; -- ",
            "alice' UNION SELECT secret,v,id FROM esc_cmt -- ",
            "' OR ''='",
        ]
        for payload in injections:
            rs = c.query('SELECT v FROM esc_cmt WHERE v = %s', (payload,))
            assert rs.rows == [], (payload, rs.rows)
        # none of the above commented-out / stacked anything: all 3 rows still present and table intact
        rs2 = c.query('SELECT COUNT(*) FROM esc_cmt')
        assert rs2.rows[0][0] == 3, rs2.rows
        # and the legitimate exact value still matches
        rs3 = c.query('SELECT v FROM esc_cmt WHERE v = %s', ('alice',))
        assert len(rs3.rows) == 1, rs3.rows
        c.close()

    def test_security_injection__esc_gbk_backslash_eating_safe(self):
        run_ddl('DROP TABLE IF EXISTS esc_gbk; CREATE TABLE esc_gbk (id INT NOT NULL AUTO_INCREMENT, v VARBINARY(64) NOT NULL, secret INT NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        c = conn()
        c.query('INSERT INTO esc_gbk (v, secret) VALUES (%s, %s)', (b'row1', 7))
        # 0xbf 0x27 is the canonical GBK backslash-eating sequence; 0x27 is an ASCII single quote
        attack = b'\xbf\x27 OR 1=1 -- '
        rs = c.query('SELECT v FROM esc_gbk WHERE v = %s', (attack,))
        assert rs.rows == [], rs.rows  # must NOT return row1; quote did not break out
        # and the exact attack bytes are storable/comparable literally
        c.query('INSERT INTO esc_gbk (v, secret) VALUES (%s, %s)', (attack, 9))
        rs2 = c.query('SELECT v FROM esc_gbk WHERE v = %s', (attack,))
        assert len(rs2.rows) == 1, rs2.rows
        stored = rs2.rows[0][0]
        if not isinstance(stored, bytes):
            stored = stored.encode('latin-1')
        assert stored == attack, repr(stored)
        c.close()

    def test_security_injection__esc_homoglyph_fullwidth_quotes(self):
        run_ddl('DROP TABLE IF EXISTS esc_homo; CREATE TABLE esc_homo (id INT NOT NULL AUTO_INCREMENT, v VARCHAR(64) NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;')
        c = conn()
        homo = u'O＇Brien OR ’1’=’1'  # fullwidth apostrophe + curly quotes, looks like an injection
        ascii_q = u"O'Brien"
        c.query('INSERT INTO esc_homo (v) VALUES (%s)', (homo,))
        c.query('INSERT INTO esc_homo (v) VALUES (%s)', (ascii_q,))
        rs = c.query('SELECT v FROM esc_homo WHERE v = %s', (homo,))
        assert len(rs.rows) == 1, rs.rows
        stored = rs.rows[0][0]
        if isinstance(stored, bytes):
            stored = stored.decode('utf-8')
        assert stored == homo, repr(stored)
        # binary collation: the homoglyph value must NOT match the real apostrophe value and vice versa
        rs2 = c.query('SELECT v FROM esc_homo WHERE v = %s', (ascii_q,))
        assert len(rs2.rows) == 1, rs2.rows
        g = rs2.rows[0][0]
        if isinstance(g, bytes):
            g = g.decode('utf-8')
        assert g == ascii_q, repr(g)
        c.close()

    def test_security_injection__esc_literal_percent_structure(self):
        c = conn()
        # %% must collapse to one literal % in the emitted query; the single %s consumes the one arg
        rs = c.query("SELECT CONCAT('100%%done-', %s) AS msg", ('ok',))
        val = rs.rows[0][0]
        if isinstance(val, bytes):
            val = val.decode('utf-8')
        assert val == u'100%done-ok', repr(val)  # exactly one percent sign, arg bound after it
        # %% alone with no params must not try to read the (empty) iterator
        rs2 = c.query('SELECT %s', ('50%% off',))
        out = rs2.rows[0][0]
        if isinstance(out, bytes):
            out = out.decode('utf-8')
        # the %% here is INSIDE a string param value, so it is NOT touched by the placeholder parser -> stays '%%'
        assert out == u'50%% off', repr(out)
        c.close()

    def test_security_injection__esc_bad_percent_and_arg_count(self):
        c = conn()
        # '%d' is not a valid placeholder for umysql -> strict ValueError, query never sent
        try:
            c.query('SELECT %d', (5,))
            assert False, 'expected ValueError for %d'
        except ValueError:
            pass
        # a %s with no available arg -> ValueError, not a malformed query to the server
        try:
            c.query('SELECT %s, %s', ('only_one',))
            assert False, 'expected ValueError for too-few args'
        except ValueError:
            pass
        # connection must still be usable after the rejected queries (parser bailed cleanly, freed buffers)
        rs = c.query('SELECT %s', ('still_alive',))
        val = rs.rows[0][0]
        if isinstance(val, bytes):
            val = val.decode('utf-8')
        assert val == u'still_alive', repr(val)
        c.close()

    def test_security__param_multibyte_str_no_overflow(self):
        # Regression: a non-str/bytes param whose str() is long multibyte text used
        # to under-size the escape buffer (estimate counted code points; the write
        # encodes UTF-8 bytes -> heap/stack overflow). Must round-trip, no crash.
        if not PY3:
            return
        class Big(object):
            def __str__(self):
                return u'\U0001F600' * 5000   # 4-byte codepoints -> 20000 UTF-8 bytes
        c = conn()
        # The str() fallback renders the value unquoted, so MySQL rejects it as a
        # bad column reference -- the point is that building the 20000-byte query
        # does NOT overflow the escape buffer (was a heap/stack smash). No crash.
        try:
            c.query('SELECT %s', (Big(),))
        except umysql.SQLError:
            pass
        assert c.query('SELECT 1').rows == [(1,)]   # connection intact (no corruption)
        c.close()

    def test_security__param_toctou_str_no_overflow(self):
        # Regression: a param whose str() returns different lengths across the
        # sizing vs writing calls must not overflow -- the bounded writer fails the
        # query cleanly instead of corrupting memory.
        class Toctou(object):
            n = 0
            def __str__(self):
                Toctou.n += 1
                return 'A' if Toctou.n == 1 else 'A' * 200000
        c = conn()
        try:
            c.query('SELECT %s', (Toctou(),))
        except Exception:
            pass   # may raise; the point is no crash / no corruption
        assert c.query('SELECT 1').rows == [(1,)]   # connection still usable
        c.close()


    def test_security__object_str_fallback_quoted_no_injection(self):
        # A non-string param whose str() returns SQL must be QUOTED (inert), not
        # spliced raw. Was an injection via the unquoted str() fallback.
        class Evil(object):
            def __str__(self):
                return "1 OR 1=1"
        c = conn()
        c.query('DROP TABLE IF EXISTS si')
        c.query('CREATE TABLE si(id INT, v INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO si VALUES (1,10),(2,20)')
        # injected -> "v = 1 OR 1=1" returns ALL rows; quoted -> "v = '1 OR 1=1'"
        # coerces to 1 -> matches 0 rows.
        rows = c.query('SELECT id FROM si WHERE v = %s', (Evil(),)).rows
        assert rows == [], rows
        c.query('DROP TABLE si'); c.close()

    def test_security__int_subclass_malicious_str_no_union(self):
        class I(int):
            def __str__(self):
                return "0 UNION SELECT @@version"
        c = conn()
        try:
            rs = c.query('SELECT %s', (I(5),))
            # If the injection regressed, the UNION executes and yields TWO rows
            # ([(0,), (version,)]). The malicious __str__ must instead be emitted as a
            # single quoted literal -- exactly one row whose value is the literal text.
            assert len(rs.rows) == 1, rs.rows
            v = rs.rows[0][0]
            gs = v.decode('utf-8') if isinstance(v, bytes) else (v if isinstance(v, str) else str(v))
            assert gs == '0 UNION SELECT @@version', repr(gs)   # inert quoted literal, not executed
        except umysql.SQLError:
            pass   # quoted-but-rejected is also fine; the point is no @@version leak / no UNION
        assert c.query('SELECT 1').rows == [(1,)]
        c.close()

    def test_security__numeric_params_still_unquoted(self):
        # Regression: the injection fix must NOT quote genuine numerics (LIMIT needs
        # an unquoted integer; a quoted '2' is a syntax error).
        c = conn()
        c.query('DROP TABLE IF EXISTS nm')
        c.query('CREATE TABLE nm(id INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO nm VALUES (1),(2),(3)')
        assert len(c.query('SELECT id FROM nm ORDER BY id LIMIT %s', (2,)).rows) == 2
        assert c.query('SELECT id FROM nm WHERE id = %s', (3,)).rows == [(3,)]
        c.query('DROP TABLE nm'); c.close()

    def test_security__collection_and_special_floats_no_crash(self):
        c = conn()
        for p in ([1, 2, 3], {'a': 1}, (1, 2), float('inf'), float('nan')):
            try:
                c.query('SELECT %s', (p,))
            except umysql.SQLError:
                pass   # quoted garbage -> clean SQL error; never a crash/injection
            assert c.query('SELECT 1').rows == [(1,)]
        c.close()

    def test_security__datetime_subclass_shrinking_str_no_overflow(self):
        import datetime
        class EvilDT(datetime.datetime):
            def __str__(self):
                return ''   # shrinks the per-arg size estimate
        c = conn()
        try:
            c.query('SELECT %s', (EvilDT(2024, 6, 15, 12, 30, 45),))
        except Exception:
            pass   # bounded snprintf may fail the query; the point is no overflow
        assert c.query('SELECT 1').rows == [(1,)]   # no memory corruption
        c.query('SELECT %s', (datetime.datetime(2024, 6, 15, 12, 30, 45),))  # normal still works
        c.close()

    def test_security__params_iter_raises_no_crash(self):
        class BadIter(object):
            def __iter__(self):
                raise ValueError('boom')
        c = conn()
        try:
            c.query('SELECT %s', BadIter())
        except Exception:
            pass   # must raise cleanly, not NULL-deref
        assert c.query('SELECT 1').rows == [(1,)]
        c.close()

    def test_security__params_iter_raises_on_later_pass(self):
        class Flaky(object):
            n = 0
            def __iter__(self):
                Flaky.n += 1
                if Flaky.n >= 2:   # ok for validation, raises on a later GetIter
                    raise ValueError('boom2')
                return iter([1])
        c = conn()
        try:
            c.query('SELECT %s', Flaky())
        except Exception:
            pass
        assert c.query('SELECT 1').rows == [(1,)]   # NULL-checked GetIter, no crash
        c.close()

    def test_security__single_use_generator_params(self):
        def gen():
            yield 1
            yield 2
        c = conn()
        try:
            c.query('SELECT %s, %s', gen())   # iterated twice; single-use gen
        except Exception:
            pass   # clean error ok; no crash/overflow
        assert c.query('SELECT 1').rows == [(1,)]
        c.close()


    def test_robust__connection_survives_sql_error(self):
        # Pooled connections are REUSED after a query error; a transient SQL error
        # must not poison the connection.
        c = conn()
        try:
            c.query('SELEC bad syntax')
        except umysql.SQLError:
            pass
        assert c.query('SELECT 1').rows == [(1,)]
        c.query('DROP TABLE IF EXISTS rerr')
        c.query('CREATE TABLE rerr(id INT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO rerr VALUES (1)')
        try:
            c.query('INSERT INTO rerr VALUES (1)')   # duplicate key
        except umysql.SQLError:
            pass
        assert c.query('SELECT COUNT(*) FROM rerr').rows == [(1,)]   # conn still usable
        c.query('DROP TABLE rerr'); c.close()

    def test_robust__on_duplicate_key_rowcount(self):
        # db.py Model.put uses ON DUPLICATE KEY UPDATE and reads rc[0]; pin the
        # MySQL affected-rows convention (insert=1, update-via-dup=2, unchanged=0).
        c = conn()
        c.query('DROP TABLE IF EXISTS odk')
        c.query('CREATE TABLE odk(id INT PRIMARY KEY, v INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        rc = c.query('INSERT INTO odk VALUES (1,10) ON DUPLICATE KEY UPDATE v=VALUES(v)')
        assert rc[0] == 1, rc
        rc = c.query('INSERT INTO odk VALUES (1,20) ON DUPLICATE KEY UPDATE v=VALUES(v)')
        assert rc[0] == 2, rc   # existing row changed -> 2
        # NOTE: the driver copies the server capability flags incl. CLIENT_FOUND_ROWS,
        # so a no-op update reports affected-rows = 1 (matched), NOT 0 (changed-rows
        # semantics would give 0). This matches the original umysql.
        rc = c.query('INSERT INTO odk VALUES (1,20) ON DUPLICATE KEY UPDATE v=VALUES(v)')
        assert rc[0] == 1, rc
        c.query('DROP TABLE odk'); c.close()

    def test_robust__query_on_killed_connection(self):
        # The production reconnect path: a server-killed connection must raise on
        # the next query (db.py catches RuntimeError('Not connected') and retries),
        # and a fresh connection must work.
        import time
        c = conn()
        cid = c.query('SELECT CONNECTION_ID()').rows[0][0]
        k = conn(); k.query('KILL %s', (cid,)); k.close()
        time.sleep(0.2)
        raised = False
        try:
            c.query('SELECT 1')
        except (RuntimeError, umysql.SQLError, umysql.Error):
            raised = True
        assert raised, 'query on a killed connection should raise'
        try:
            c.close()
        except Exception:
            pass
        c2 = conn(); assert c2.query('SELECT 1').rows == [(1,)]; c2.close()

    def test_robust__datetime_microseconds_truncated(self):
        # KNOWN behavior: the decoder hard-codes microsecond=0, so DATETIME(6)
        # fractional seconds are LOST. Pin it so a future change is noticed.
        import datetime
        c = conn()
        c.query('DROP TABLE IF EXISTS dtus')
        c.query('CREATE TABLE dtus(dt datetime(6)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query("INSERT INTO dtus VALUES ('2024-06-15 12:30:45.123456')")
        dt = c.query('SELECT dt FROM dtus').rows[0][0]
        assert isinstance(dt, datetime.datetime), type(dt)
        assert dt.microsecond == 0, dt.microsecond   # truncated
        assert dt == datetime.datetime(2024, 6, 15, 12, 30, 45)
        c.query('DROP TABLE dtus'); c.close()

    def test_robust__large_text_crosses_length_codes(self):
        # TEXT values across the length-coded-binary thresholds (1/2/3-byte codes)
        # and the chunked recv path must round-trip exactly.
        c = conn()
        c.query('DROP TABLE IF EXISTS bigtxt')
        c.query('CREATE TABLE bigtxt(id INT, t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        for n in (250, 251, 65535, 65536, 70000, 1000000):
            sx = 'x' * n
            c.query('DELETE FROM bigtxt')
            c.query('INSERT INTO bigtxt VALUES (1, %s)', (sx,))
            got = c.query('SELECT t FROM bigtxt WHERE id=1').rows[0][0]
            gs = got.decode('utf-8') if isinstance(got, bytes) else got
            assert len(gs) == n, (n, len(gs))
        c.query('DROP TABLE bigtxt'); c.close()

    def test_robust__stored_proc_call_conn_not_poisoned(self):
        # A CALL returns a result set followed by an extra OK packet (multi-result).
        # Whatever the driver does with the result, the connection MUST remain
        # usable for the next query (no stale-packet poisoning).
        c = conn()
        c.query('DROP PROCEDURE IF EXISTS sp_one')
        c.query('CREATE PROCEDURE sp_one() BEGIN SELECT 7 AS a; END')
        try:
            c.query('CALL sp_one()')
        except umysql.Error:
            pass
        assert c.query('SELECT 42').rows == [(42,)], 'connection poisoned after CALL'
        c.query('DROP PROCEDURE IF EXISTS sp_one'); c.close()


    def test_gevent__pool_under_load(self):
        # A connection-pool pattern over umysql under gevent: many greenlets
        # concurrently check out / use / return pooled connections.
        # Verifies (a) no cross-talk / corruption -- each greenlet gets its own
        # result -- and (b) the pool stays gevent-cooperative (queries overlap).
        if not HAVE_GEVENT:
            self.skipTest('gevent not installed')
        import sqlalchemy.pool as sapool
        import time, gevent
        def getconn():
            return conn()
        dbpool = sapool.QueuePool(getconn, pool_size=5, max_overflow=10, recycle=3600, reset_on_return=False)
        N = 30
        results = {}
        errors = []
        def worker(i):
            try:
                cnn = dbpool.connect()
                try:
                    rs = cnn.query('SELECT SLEEP(0.1), %s', (i,))
                    results[i] = rs.rows[0][1]
                finally:
                    cnn.close()
            except Exception as e:
                errors.append((i, repr(e)))
        t = time.time()
        gevent.joinall([gevent.spawn(worker, i) for i in range(N)], timeout=30)
        elapsed = time.time() - t
        assert not errors, errors
        assert len(results) == N, (len(results), N)
        for i in range(N):
            assert results.get(i) == i, ('cross-talk', i, results.get(i))
        assert elapsed < 1.5, ('not cooperative through the pool', elapsed)

    def test_exotic__bit64_and_binary(self):
        import struct
        c = conn()
        c.query('DROP TABLE IF EXISTS ex1')
        c.query('CREATE TABLE ex1(b64 bit(64), bin5 binary(5)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query("INSERT INTO ex1 (b64, bin5) VALUES (255, %s)", (b'abc',))
        b64, bin5 = c.query('SELECT b64, bin5 FROM ex1').rows[0]
        assert isinstance(b64, bytes) and len(b64) == 8, repr(b64)   # BIT(64) -> 8 bytes
        assert struct.unpack('>Q', b64)[0] == 255, struct.unpack('>Q', b64)[0]
        assert isinstance(bin5, bytes) and len(bin5) == 5, repr(bin5) # BINARY(5) NUL-padded
        assert bin5 == b'abc' + b'\x00' * 2, repr(bin5)
        c.query('DROP TABLE ex1'); c.close()

    def test_exotic__set_and_enum(self):
        c = conn()
        c.query('DROP TABLE IF EXISTS ex2')
        c.query("CREATE TABLE ex2(s set('a','b','c','d'), e enum('x','y','z')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        c.query("INSERT INTO ex2 VALUES ('a,c,d', 'y')")
        c.query("INSERT INTO ex2 VALUES ('', 'x')")          # empty SET
        rows = c.query('SELECT s, e FROM ex2').rows
        def dec(v):
            return v.decode('utf-8') if isinstance(v, bytes) else v
        assert dec(rows[0][0]) == 'a,c,d', repr(rows[0][0])  # SET members comma-joined
        assert dec(rows[1][0]) == '', repr(rows[1][0])       # empty SET -> empty string
        assert dec(rows[0][1]) == 'y', repr(rows[0][1])      # ENUM -> label
        c.query('DROP TABLE ex2'); c.close()

    def test_exotic__time_negative_and_large(self):
        c = conn()
        c.query('DROP TABLE IF EXISTS ex3')
        c.query('CREATE TABLE ex3(t time) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        for v in ('-12:30:00', '120:00:00', '838:59:59', '-838:59:59'):  # full MySQL TIME range
            c.query('DELETE FROM ex3')
            c.query('INSERT INTO ex3 VALUES (%s)', (v,))
            got = c.query('SELECT t FROM ex3').rows[0][0]
            gs = got.decode('utf-8') if isinstance(got, bytes) else got
            assert gs == v, (v, gs)   # TIME -> string, incl. negative and > 24h
        c.query('DROP TABLE ex3'); c.close()

    def test_exotic__zerofill_and_generated(self):
        c = conn()
        c.query('DROP TABLE IF EXISTS ex4')
        c.query('CREATE TABLE ex4(z int(5) zerofill, n int, g int AS (n*2) STORED) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO ex4 (z, n) VALUES (42, 10)')
        z, n, g = c.query('SELECT z, n, g FROM ex4').rows[0]
        assert z == 42, repr(z)              # ZEROFILL is display-only -> int value
        assert n == 10 and g == 20, (n, g)   # generated column computed server-side
        c.query('DROP TABLE ex4'); c.close()

    def test_exotic__unsigned_boundaries_and_highprec_decimal(self):
        c = conn()
        c.query('DROP TABLE IF EXISTS ex5')
        c.query('CREATE TABLE ex5(ti tinyint unsigned, mi mediumint unsigned, d decimal(30,10)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO ex5 VALUES (255, 16777215, %s)', ('-12345678901234567890.0123456789',))
        ti, mi, d = c.query('SELECT ti, mi, d FROM ex5').rows[0]
        assert ti == 255 and mi == 16777215, (ti, mi)
        ds = d.decode('utf-8') if isinstance(d, bytes) else d
        assert ds == '-12345678901234567890.0123456789', repr(d)  # exact high-precision DECIMAL
        c.query('DROP TABLE ex5'); c.close()

    def test_ngandhy__buffer_full_large_result(self):
        # Production fix (ngandhy 2.63.7 freeSpace): a result set LARGER than the
        # ~16MB rx buffer streams via buffer compaction instead of raising
        # "Socket receive buffer full". Reproduced: without freeSpace this exact
        # 20MB SELECT raises that error; with it the full result returns.
        c = conn()
        c.query('DROP TABLE IF EXISTS nbig')
        c.query('CREATE TABLE nbig(id INT, t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        for i in range(20):
            c.query('INSERT INTO nbig VALUES (%s, %s)', (i, 'x' * 1000000))   # ~20MB total
        rs = c.query('SELECT id, t FROM nbig ORDER BY id')
        assert len(rs.rows) == 20, len(rs.rows)
        assert sum(len(r[1]) for r in rs.rows) == 20 * 1000000
        c.query('DROP TABLE nbig'); c.close()

    def test_ngandhy__utf8mb4_collation_is_unicode_ci(self):
        # Production fix (ngandhy 2.63.7): a utf8mb4 connection uses utf8mb4_unicode_ci
        # (224), not utf8mb4_general_ci (45), so it matches utf8mb4_unicode_ci tables.
        c = conn('utf8mb4')
        coll = c.query('SELECT @@collation_connection').rows[0][0]
        cs = coll.decode('utf-8') if isinstance(coll, bytes) else coll
        assert cs == 'utf8mb4_unicode_ci', cs
        # A CONCAT result takes the CONNECTION collation; comparing it to a
        # utf8mb4_unicode_ci column raises "illegal mix of collations" if the
        # connection were general_ci. With the fix (unicode_ci) it matches cleanly.
        c.query('DROP TABLE IF EXISTS ncoll')
        c.query("CREATE TABLE ncoll(v VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci) ENGINE=InnoDB")
        c.query("INSERT INTO ncoll VALUES ('cafe')")
        rows = c.query("SELECT v FROM ncoll WHERE v = CONCAT(%s, '')", ('cafe',)).rows
        assert len(rows) == 1, rows   # would raise 1267 illegal-mix under general_ci
        c.query('DROP TABLE ncoll'); c.close()

    # ------------------------------------------------------------------
    # Boundary / capacity / interop tests added from the test-gap audit.
    # These target "production-discovered" defect classes (capacity cliffs,
    # protocol-framing edges, collation interop, error-path connection state)
    # that a security-only review and the happy-path suite both miss. Where a
    # test PINS current (buggy) behavior it says so -- flip the assertion when
    # the underlying limitation is fixed.
    # ------------------------------------------------------------------

    def _probe_single_value(self, length):
        # Insert a single LONGTEXT value of `length` bytes (built server-side via
        # REPEAT so the query text stays under the 4MB tx buffer), SELECT it back,
        # and report (status, a, b, reuse_ok) where reuse_ok is whether a follow-up
        # query on the SAME connection still works (False == the big SELECT poisoned
        # it). Uses a fresh connection per call.
        c = conn()
        c.query('DROP TABLE IF EXISTS bigval')
        c.query('CREATE TABLE bigval(t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        c.query('INSERT INTO bigval VALUES (REPEAT(%s,%s))', ('z', length))
        try:
            rs = c.query('SELECT t FROM bigval')
            out = ('ok', len(rs.rows), len(rs.rows[0][0]) if rs.rows else 0)
        except Exception as e:
            out = ('raised', type(e).__name__, repr(e.args))
        try:
            reuse_ok = (c.query('SELECT 1').rows == [(1,)])
        except Exception:
            reuse_ok = False
        try:
            c.close()
        except Exception:
            pass
        return out + (reuse_ok,)

    def test_size__single_value_just_over_packet_limit_silently_truncates(self):
        # A single value whose wire encoding reaches MySQL's 0xFFFFFF (16,777,215)
        # per-packet limit is split across multiple protocol packets that umysql
        # does NOT reassemble. Just UNDER the limit round-trips exactly; just OVER
        # it the value is SILENTLY truncated and the single row is split into two,
        # with NO exception (the dangerous failure mode). Empirically verified
        # against MySQL 8 on both py2 and py3 builds. PIN of current behavior --
        # flip when multi-part packet reassembly is implemented.
        # Just under the limit: exact round-trip, connection reusable.
        assert self._probe_single_value(16777210) == ('ok', 1, 16777210, True)
        # Exactly at 0xFFFFFF (4-byte length code + value == 16,777,215): the driver
        # mis-frames it -- raises AND poisons the connection (not reusable).
        status, _, _, reuse_ok = self._probe_single_value(16777211)
        assert status == 'raised', status
        assert reuse_ok is False, 'expected the connection to be poisoned'
        # Just over: wire payload exceeds 0xFFFFFF -> row splits into 2, value silently
        # truncated to 16,777,211, but the connection survives and is reusable.
        status, nrows, firstlen, reuse_ok = self._probe_single_value(16777212)
        assert (status, nrows, firstlen, reuse_ok) == ('ok', 2, 16777211, True), (status, nrows, firstlen, reuse_ok)

    def test_size__single_value_at_or_over_16mb_returns_empty_and_poisons(self):
        # A single value at or above the 16MB rx buffer (16,777,216) does not even
        # truncate -- the SELECT returns ZERO rows (the value vanishes) and the
        # connection is left POISONED: the next query raises. PIN of current
        # behavior; a correct driver would raise a clean error on the SELECT and
        # keep the connection usable (PyMySQL handles this case correctly).
        status, nrows, _, reuse_ok = self._probe_single_value(16 * 1024 * 1024)
        assert (status, nrows) == ('ok', 0), (status, nrows)
        assert reuse_ok is False, 'expected the connection to be poisoned'

    def test_size__large_total_result_forces_multiple_buffer_compactions(self):
        # ~40MB total across 40 x ~1MB rows (each value well under 0xFFFFFF, so no
        # per-packet split) forces freeSpace() to compact the rx buffer multiple
        # times. Verifies the ported m_packetEnd recompute holds under repeated
        # wraps and the full result streams intact.
        N = 40
        c = conn()
        c.query('DROP TABLE IF EXISTS bigtot')
        c.query('CREATE TABLE bigtot(id INT, t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        for i in range(N):
            c.query('INSERT INTO bigtot VALUES (%s, REPEAT(%s,%s))', (i, 'a', 1000000))
        rs = c.query('SELECT id, t FROM bigtot ORDER BY id')
        assert len(rs.rows) == N, len(rs.rows)
        assert sum(len(r[1]) for r in rs.rows) == N * 1000000
        assert all(rs.rows[i][0] == i and len(rs.rows[i][1]) == 1000000 for i in range(N))
        c.query('DROP TABLE bigtot'); c.close()

    def test_size__query_string_at_and_over_4mb_tx_boundary(self):
        # The TX buffer is 4MB+header; query() rejects when len > getSize()-(HEADER+1)
        # = 4194303. So a query of exactly 4194303 bytes succeeds and 4194304 raises
        # "Query too big". Pins the off-by-one so a buffer-size change is caught.
        c = conn()
        assert c.txBufferSize == 4 * 1024 * 1024 + 4, c.txBufferSize
        limit = c.txBufferSize - 5
        prefix, suffix = "SELECT '", "' AS x"
        pad = limit - len(prefix) - len(suffix)
        q = prefix + ('a' * pad) + suffix
        assert len(q) == limit
        assert c.query(q).rows == [('a' * pad,)]
        q2 = prefix + ('a' * (pad + 1)) + suffix
        assert len(q2) == limit + 1
        try:
            c.query(q2)
            assert False, 'expected Query too big'
        except umysql.Error as e:
            msg = str(e.args[1]) if len(e.args) > 1 else str(e)
            assert 'too big' in msg.lower(), e.args
        c.close()

    def test_conn_lifecycle__query_too_big_is_fatal_to_connection(self):
        # Unlike a SQL error (which leaves the connection usable), an oversized query
        # goes through setError(UME_OTHER), which CLOSES the socket. So is_connected()
        # flips False and the next query raises "Not connected" -- a hazard for any
        # connection pool that assumes errors are recoverable.
        c = conn()
        oversized = "SELECT '" + ('a' * (5 * 1024 * 1024)) + "'"   # ~5MB > 4MB tx
        try:
            c.query(oversized)
            assert False, 'expected Query too big'
        except umysql.Error:
            pass
        assert c.is_connected() is False, 'oversized query should have closed the socket'
        raised = None
        try:
            c.query('SELECT 1')
        except Exception as e:
            raised = e
        assert raised is not None and 'not connected' in str(raised).lower(), raised
        try:
            c.close()
        except Exception:
            pass

    def test_conn_lifecycle__killed_connection_raises_then_unusable(self):
        # After the server kills the connection, the FIRST query raises, and the
        # connection is then not cleanly reusable. The exact first-error TYPE is
        # timing-dependent -- recv seeing EOF surfaces a umysql.Error/RuntimeError,
        # while the socket layer raising surfaces a raw socket.error
        # (EnvironmentError); both are valid. So pin only the stable contract: it
        # raises, then it is unusable.
        victim = conn()
        cid = int(victim.query('SELECT CONNECTION_ID()').rows[0][0])
        killer = conn()
        killer.query('KILL %d' % cid)
        killer.close()
        first = None
        try:
            victim.query('SELECT 1')
        except Exception as e:
            first = e
        assert first is not None, 'expected an error after KILL'
        # Must not be cleanly reusable: a follow-up either raises or is_connected() is
        # False -- it must NOT return a clean fresh result.
        reusable = False
        try:
            reusable = (victim.query('SELECT 1').rows == [(1,)])
        except Exception:
            reusable = False
        assert not reusable, 'killed connection still returned a clean result'
        try:
            victim.close()
        except Exception:
            pass

    def test_framing__result_column_count_crosses_lengthcoded_251(self):
        # The result-set column count is itself a length-coded integer: <=250 is a
        # 1-byte code, >=251 must use the 0xfc 2-byte form. Cross the boundary to
        # exercise readLengthCodedInteger on the count (distinct from the value path).
        c = conn()
        for ncol in (250, 251, 252, 300):
            rs = c.query('SELECT ' + ', '.join(str(i) for i in range(ncol)))
            assert len(rs.fields) == ncol, (ncol, len(rs.fields))
            assert rs.rows[0] == tuple(range(ncol)), ncol
        c.close()

    def test_collation__per_charset_negotiated_collation(self):
        # The suite pins only utf8mb4's negotiated collation. This pins all four so a
        # handshake regression -- or a future "fix" that wrongly applies unicode_ci to
        # latin1/ascii/cp1250 -- is caught.
        expected = {
            'utf8mb4': 'utf8mb4_unicode_ci',
            'latin1': 'latin1_general_ci',
            'ascii': 'ascii_general_ci',
            'cp1250': 'cp1250_general_ci',
        }
        for cs, coll in expected.items():
            c = conn(cs)
            got = c.query('SELECT @@collation_connection').rows[0][0]
            got = got.decode('utf-8') if isinstance(got, bytes) else got
            assert got == coll, '%s -> %r (expected %r)' % (cs, got, coll)
            c.close()

    def test_collation__connection_default_follows_unicode_ci_not_general_ci(self):
        # The regression test that would have caught the original general_ci bug. The
        # sharp-s expands to "ss" under utf8mb4_unicode_ci (so they compare EQUAL) but
        # not under utf8mb4_general_ci. Prove (a) the pair distinguishes the two
        # collations and (b) the connection default follows unicode_ci.
        c = conn('utf8mb4')
        sharp = u'\xdf'   # sharp-s U+00DF; unicode_ci expands it to "ss", general_ci does not
        default_eq = c.query('SELECT %s = %s', (sharp, u'ss')).rows[0][0]
        uni_eq = c.query('SELECT %s COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci', (sharp, u'ss')).rows[0][0]
        gen_eq = c.query('SELECT %s COLLATE utf8mb4_general_ci = %s COLLATE utf8mb4_general_ci', (sharp, u'ss')).rows[0][0]
        assert uni_eq != gen_eq, (uni_eq, gen_eq)        # the pair genuinely distinguishes the collations
        assert default_eq == uni_eq, (default_eq, uni_eq)  # connection default == unicode_ci (the fix)
        c.close()

    def test_concurrency_gevent__killed_greenlet_mid_query_corrupts_connection(self):
        # A greenlet killed (GreenletExit) mid-recv leaves the connection
        # DESYNCHRONIZED: the socket still has the killed query's bytes queued, and
        # the driver does not detect this. Per the suite's established contract (see
        # gevent_shared_connection_mid_query_corrupts) the driver makes NO safety
        # guarantee here -- reuse must NOT silently return the correct fresh result;
        # it returns stale/garbled rows or raises. PIN of current behavior: don't
        # reuse a connection whose query was interrupted (close + reconnect instead).
        # Flip this if the driver is changed to close/reset on interrupted recv.
        if not HAVE_GEVENT:
            self.skipTest('gevent not installed')
        import gevent
        from gevent import monkey
        monkey.patch_all()
        c = conn()
        c.query('DROP TABLE IF EXISTS gk')
        c.query('CREATE TABLE gk(id INT, t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        for i in range(20):
            c.query('INSERT INTO gk VALUES (%s, REPEAT(%s,%s))', (i, 'a', 1000000))   # ~20MB
        g = gevent.spawn(lambda: c.query('SELECT id, t FROM gk ORDER BY id'))
        gevent.sleep(0)   # let the large recv start
        finished_before_kill = g.ready()
        g.kill()
        try:
            ok = (c.query('SELECT 99').rows == [(99,)])
        except (umysql.Error, RuntimeError):
            ok = False   # a clean error is also a "not safely reusable" signal
        if finished_before_kill:
            assert ok, 'query completed cleanly but connection was unusable on reuse'
        else:
            assert not ok, 'killed-mid-query connection behaved as if safe on reuse'
        try:
            c.close()
        except Exception:
            pass

    def test_concurrency_gevent__timeout_mid_large_result_corrupts_connection(self):
        # Same desync via gevent.Timeout firing during the multi-recv of a large
        # result. Same contract: if the timeout actually interrupts the query, the
        # connection is corrupted and reuse must NOT return the correct fresh result;
        # if the query finished before the timeout, reuse must be clean. PIN of
        # current behavior.
        if not HAVE_GEVENT:
            self.skipTest('gevent not installed')
        import gevent
        from gevent import monkey
        monkey.patch_all()
        c = conn()
        c.query('DROP TABLE IF EXISTS tmid')
        c.query('CREATE TABLE tmid(id INT, t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
        for i in range(30):
            c.query('INSERT INTO tmid VALUES (%s, REPEAT(%s,%s))', (i, 'a', 1000000))   # ~30MB
        interrupted = False
        try:
            with gevent.Timeout(0.005):
                c.query('SELECT id, t FROM tmid ORDER BY id')
        except gevent.Timeout:
            interrupted = True
        try:
            ok = (c.query('SELECT 77').rows == [(77,)])
        except (umysql.Error, RuntimeError):
            ok = False
        if interrupted:
            assert not ok, 'timeout-interrupted connection behaved as if safe on reuse'
        else:
            assert ok, 'uninterrupted query left connection unusable on reuse'
        try:
            c.close()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
