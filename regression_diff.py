# -*- coding: utf-8 -*-
# Comprehensive differential harness: prove the new 3.0.0 build is behaviorally
# identical to the production umysql-2018 (2.63.7) build, scenario by scenario.
# Emits one canonical line per probe; run under each driver and diff the outputs:
#   python2 regression_diff.py                 > /tmp/rd_prod.txt   # pip 2.63.7 (prod)
#   PYTHONPATH=build/py2 python2 ...           > /tmp/rd_new2.txt   # new 3.0.0 py2
#   PYTHONPATH=build/py3 python3 ...           > /tmp/rd_new3.txt   # new 3.0.0 py3
# Result (71 probes): prod-vs-new on py2 is byte-identical EXCEPT 5 intentional
# changes -- JSON decode (new feature; prod errors 245) and the str()-fallback
# hardening now quoting inf/nan/datetime.time/list (prod emitted them unquoted and
# errored 1054/1064). new2-vs-new3 differs only by documented str/bytes + long/int
# repr, PLUS float-param precision: py2 str(float) is %.12g, py3 is shortest-
# roundtrip, so a high-precision float param writes different bytes on py3.
from __future__ import print_function
import sys, binascii, datetime, umysql

H, P, U, PW, DB = '127.0.0.1', 3306, 'gevent_test', 'gevent_test', 'gevent_test'

try:
    text_type = unicode          # py2
    bytes_type = str
    int_types = (int, long)
except NameError:
    text_type = str              # py3
    bytes_type = bytes
    int_types = (int,)


def canon(v):
    # Canonical, interpreter-independent rendering of a single cell.
    if v is None:
        return 'N'
    if isinstance(v, bool):
        return 'I:%d' % int(v)
    if isinstance(v, int_types):
        return 'I:%d' % v
    if isinstance(v, float):
        return 'F:%r' % v
    if isinstance(v, bytes_type):
        return 'B:' + binascii.hexlify(v).decode('ascii')
    if isinstance(v, text_type):
        return 'S:' + binascii.hexlify(v.encode('utf-8')).decode('ascii')
    return '?:' + repr(v)


_state = {'c': None}


def connect_db():
    c = umysql.Connection()
    c.connect(H, P, U, PW, DB, True, 'utf8mb4')
    return c


def getc():
    if _state['c'] is None or not _state['c'].is_connected():
        _state['c'] = connect_db()
    return _state['c']


def Q(sql, params=None):
    c = getc()
    return c.query(sql, params) if params is not None else c.query(sql)


def emit(pid, label, fn):
    try:
        r = fn()
        print('%-32s | %s' % (pid, r))
    except umysql.SQLError as e:
        print('%-32s | SQLERR code=%s' % (pid, e.args[0]))
    except umysql.Error as e:
        print('%-32s | ERR %r' % (pid, e.args[:1]))
        _state['c'] = None   # a decode-time umysql.Error may poison the connection; isolate next probe
    except Exception as e:
        print('%-32s | EXC %s' % (pid, type(e).__name__))
        _state['c'] = None


def rows_canon(rs):
    return ';'.join('(' + ','.join(canon(c) for c in row) + ')' for row in rs.rows)


def main():
    getc()

    # ---- scalar / literal round-trips (no table needed) ----
    scalars = [
        ('int_zero', 'SELECT 0'),
        ('int_max32', 'SELECT 2147483647'),
        ('int_min32', 'SELECT -2147483648'),
        ('int_max64', 'SELECT 9223372036854775807'),
        ('int_min64', 'SELECT -9223372036854775808'),
        ('uint_max64', 'SELECT 18446744073709551615'),
        ('float_pi', 'SELECT 3.14159265358979'),
        ('null', 'SELECT NULL'),
        ('empty_str', "SELECT ''"),
        ('ascii', "SELECT 'hello world'"),
        ('quote_in_str', "SELECT 'O''Brien'"),
        ('backslash', "SELECT 'a\\\\b'"),
        ('decimal_lit', 'SELECT 12345.67890'),
        ('concat', "SELECT CONCAT('a','b','c')"),
        ('multi_col', 'SELECT 1, 2.5, NULL'),
        ('hex_blob', "SELECT X'00FF10'"),
        ('bin_func', "SELECT BINARY 'abc'"),
    ]
    for pid, sql in scalars:
        emit(pid, pid, (lambda s: (lambda: rows_canon(Q(s))))(sql))

    # ---- typed table round-trips ----
    Q('DROP TABLE IF EXISTS rd_types')
    Q('''CREATE TABLE rd_types (
        id INT PRIMARY KEY, ti TINYINT, si SMALLINT, mi MEDIUMINT, bi BIGINT,
        bu BIGINT UNSIGNED, fl FLOAT, db DOUBLE, de DECIMAL(20,6),
        ch CHAR(8), vc VARCHAR(64), tx TEXT, bl BLOB, vb VARBINARY(16),
        dt DATE, tm TIME, dtm DATETIME(3), ts TIMESTAMP NULL, yr YEAR,
        en ENUM('a','b','c'), st SET('x','y','z')
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
    Q('''INSERT INTO rd_types VALUES
        (1, -128, -32768, -8388608, -9223372036854775808, 18446744073709551615,
         1.5, 2.718281828, -12345678901234.567890, 'abc', %s, %s, %s, %s,
         '2026-06-11', '13:45:09', '2026-06-11 13:45:09.123', '2026-06-11 13:45:09', 2026,
         'b', 'x,z')''',
      (u'caf\xe9 \U0001F32E', u'中文 text', b'\x00\x01\xfe\xff', b'bin\x00data'))  # noqa
    Q("INSERT INTO rd_types (id, ti, vc, dt) VALUES (2, NULL, NULL, NULL)")  # all-nulls row
    emit('e_zero_date', 'ez', lambda: 'W:%r' % (Q("INSERT INTO rd_types (id, dt) VALUES (3, '0000-00-00')"),))  # strict-mode reject, must match
    emit('types_full', 'row1', lambda: rows_canon(Q('SELECT * FROM rd_types WHERE id=1')))
    emit('types_nulls', 'row2', lambda: rows_canon(Q('SELECT * FROM rd_types WHERE id=2')))
    emit('types_count', 'cnt', lambda: rows_canon(Q('SELECT COUNT(*) FROM rd_types')))

    # ---- INTENTIONAL divergences from prod (new features) -- expected to differ ----
    Q('DROP TABLE IF EXISTS rd_special')
    Q("CREATE TABLE rd_special (id INT PRIMARY KEY, bt BIT(8), js JSON, gm GEOMETRY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    Q("INSERT INTO rd_special (id, bt, js, gm) VALUES (1, b'10101010', %s, ST_GeomFromText('POINT(1 1)'))",
      (u'{"k": [1, 2, 3]}',))
    emit('x_bit', 'bit', lambda: rows_canon(Q('SELECT bt FROM rd_special')))       # prod: text/err; new: bytes
    emit('x_json', 'json', lambda: rows_canon(Q('SELECT js FROM rd_special')))     # prod: err 245; new: text
    emit('x_geometry', 'geom', lambda: rows_canon(Q('SELECT gm FROM rd_special'))) # prod: text; new: bytes
    Q('DROP TABLE rd_special')

    # ---- param escaping matrix (hostile-ish values as params) ----
    params = [
        ('p_quote', u"O'Brien"),
        ('p_dquote', u'say "hi"'),
        ('p_backslash', u'a\\b\\c'),
        ('p_percent', u'100%_done'),
        ('p_nul', u'a\x00b'),
        ('p_newline', u'line1\nline2'),
        ('p_cr', u'a\rb'),
        ('p_ctrlz', u'a\x1ab'),
        ('p_unicode', u'caf\xe9 中\U0001F32E'),
        ('p_semicolon', u'a; DROP TABLE x; --'),
        ('p_long', u'x' * 5000),
        ('p_empty', u''),
        ('p_int', 42),
        ('p_negint', -7),
        ('p_bigint', 9223372036854775807),
        ('p_float', 3.5),
        ('p_none', None),
        ('p_bytes', b'\x00\xff\x10raw'),
    ]
    for pid, val in params:
        emit(pid, pid, (lambda v: (lambda: rows_canon(Q('SELECT %s', (v,)))))(val))

    # ---- LIKE with % in the param value ----
    emit('like_pct', 'like', lambda: rows_canon(Q("SELECT %s LIKE %s", (u'100%done', u'100\\%done'))))

    # ---- float-param precision (py2 str(float)=%.12g vs py3 shortest-roundtrip): could
    #      write DIFFERENT bytes after the py3 cutover. Round-trip through a DOUBLE column. ----
    Q('DROP TABLE IF EXISTS rd_f'); Q('CREATE TABLE rd_f (id INT, d DOUBLE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    for pid, val in [('fprec_a', 0.12345678901234567), ('fprec_b', 1.0/3.0), ('fprec_c', 1.5e-10), ('fprec_d', 2.0/3.0)]:
        emit(pid, pid, (lambda v: (lambda: rows_canon(Q('SELECT %s', (v,)))))(val))            # the literal SENT
        emit(pid + '_rt', pid, (lambda v, i=pid: (lambda: (Q('DELETE FROM rd_f'), Q('INSERT INTO rd_f VALUES (1,%s)', (v,)),
                                                           rows_canon(Q('SELECT d FROM rd_f')))[-1]))(val))  # round-trip
    Q('DROP TABLE rd_f')

    # ---- str()-fallback divergence classes: prod emits unquoted str(); new quotes
    #      non-numerics. These are an INTENTIONAL prod-vs-new divergence (expected to differ). ----
    emit('x_inf', 'inf', lambda: rows_canon(Q('SELECT %s', (float('inf'),))))
    emit('x_nan', 'nan', lambda: rows_canon(Q('SELECT %s', (float('nan'),))))
    emit('x_pytime', 'time', lambda: rows_canon(Q('SELECT %s', (datetime.time(13, 37, 0),))))
    emit('x_bool', 'bool', lambda: rows_canon(Q('SELECT %s, %s', (True, False))))
    emit('x_list', 'list', lambda: rows_canon(Q('SELECT %s', ([1, 2, 3],))))

    # NOTE: the 16MB / 0xFFFFFF single-value boundary is NOT probed here -- prod 2.63.7
    # ships with asserts live and SIGABRTs the whole process at length 16,777,211
    # (Assertion m_readCursor + 1 <= m_packetEnd), which would kill the harness. The
    # hardened build returns a catchable error instead (a safety improvement). That
    # behavior is pinned on the new build by test_size__single_value_* and documented
    # in PORTING_PY3.md; it cannot be diffed inline because prod aborts.

    # ---- write semantics ----
    Q('DROP TABLE IF EXISTS rd_w')
    Q('CREATE TABLE rd_w (id INT AUTO_INCREMENT PRIMARY KEY, n INT, u INT UNIQUE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    emit('w_insert', 'ins', lambda: 'W:%r' % (Q('INSERT INTO rd_w (n,u) VALUES (%s,%s)', (10, 1)),))
    emit('w_insert2', 'ins2', lambda: 'W:%r' % (Q('INSERT INTO rd_w (n,u) VALUES (%s,%s)', (20, 2)),))
    emit('w_update_match', 'upd', lambda: 'W:%r' % (Q('UPDATE rd_w SET n=n WHERE id=1'),))           # CLIENT_FOUND_ROWS
    emit('w_update_change', 'upd2', lambda: 'W:%r' % (Q('UPDATE rd_w SET n=99 WHERE id=1'),))
    emit('w_ondup', 'dup', lambda: 'W:%r' % (Q('INSERT INTO rd_w (n,u) VALUES (%s,%s) ON DUPLICATE KEY UPDATE n=%s', (5, 1, 7)),))
    emit('w_delete', 'del', lambda: 'W:%r' % (Q('DELETE FROM rd_w WHERE id=2'),))
    emit('w_multi', 'multi', lambda: 'W:%r' % (Q('INSERT INTO rd_w (n,u) VALUES (1,11),(2,12),(3,13)'),))

    # ---- error paths (codes must match) ----
    emit('e_no_table', 'e1', lambda: rows_canon(Q('SELECT * FROM no_such_table_xyz')))
    emit('e_no_col', 'e2', lambda: rows_canon(Q('SELECT no_such_col FROM rd_w')))
    emit('e_syntax', 'e3', lambda: rows_canon(Q('SELEC BORK')))
    emit('e_dup_key', 'e4', lambda: 'W:%r' % (Q('INSERT INTO rd_w (n,u) VALUES (1,11)'),))   # dup unique -> 1062
    emit('e_bad_argcount', 'e5', lambda: rows_canon(Q('SELECT %s, %s', (1,))))

    # ---- multi-row ordering / integrity ----
    Q('DROP TABLE IF EXISTS rd_many')
    Q('CREATE TABLE rd_many (id INT, v VARCHAR(16)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    for i in range(50):
        Q('INSERT INTO rd_many VALUES (%s,%s)', (i, u'row-%d' % i))
    emit('many_first', 'mf', lambda: rows_canon(Q('SELECT * FROM rd_many ORDER BY id LIMIT 3')))
    emit('many_last', 'ml', lambda: rows_canon(Q('SELECT * FROM rd_many ORDER BY id DESC LIMIT 3')))
    emit('many_sum', 'ms', lambda: rows_canon(Q('SELECT SUM(id), COUNT(*) FROM rd_many')))

    # ===================== MED / LOW coverage additions =====================

    # Integer decode of every width as a COLUMN (literals arrive as LONGLONG -- a
    # different switch arm than typed columns), incl. all UNSIGNED branches and 2^63.
    Q('DROP TABLE IF EXISTS rd_ints')
    Q('''CREATE TABLE rd_ints (
        ti TINYINT, tiu TINYINT UNSIGNED, si SMALLINT, siu SMALLINT UNSIGNED,
        mi MEDIUMINT, miu MEDIUMINT UNSIGNED, i INT, iu INT UNSIGNED,
        bi BIGINT, biu BIGINT UNSIGNED) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
    Q('INSERT INTO rd_ints VALUES (127,255,32767,65535,8388607,16777215,2147483647,4294967295,9223372036854775807,9223372036854775808)')
    Q('INSERT INTO rd_ints (i, bi) VALUES (-2147483648, -9223372036854775808)')
    emit('int_cols_max', 'imax', lambda: rows_canon(Q('SELECT * FROM rd_ints WHERE ti=127')))
    emit('int_cols_min', 'imin', lambda: rows_canon(Q('SELECT i, bi FROM rd_ints WHERE i=-2147483648')))
    Q('DROP TABLE rd_ints')

    # rs.fields (name + type code) -- the port changed field-name materialization.
    def fields_canon(rs):
        return ';'.join('(' + canon(f[0]) + ',I:%d)' % f[1] for f in rs.fields)
    emit('fields_basic', 'fb', lambda: fields_canon(Q('SELECT 1 AS a, 2.5 AS b, NULL AS c')))
    emit('fields_alias', 'fa', lambda: fields_canon(Q('SELECT 1 AS col_named')))

    # OK-packet length-coded fields: insert-id > 2^32, affected-rows >= 251.
    Q('DROP TABLE IF EXISTS rd_bigid')
    Q('CREATE TABLE rd_bigid (id BIGINT AUTO_INCREMENT PRIMARY KEY, v INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    Q('ALTER TABLE rd_bigid AUTO_INCREMENT=5000000000')
    emit('bigid_insert', 'bi', lambda: 'W:%r' % (Q('INSERT INTO rd_bigid (v) VALUES (1)'),))
    Q('DROP TABLE IF EXISTS rd_300'); Q('CREATE TABLE rd_300 (id INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    Q('INSERT INTO rd_300 VALUES ' + ','.join('(%d)' % i for i in range(300)))
    emit('affrows_300', 'a3', lambda: 'W:%r' % (Q('UPDATE rd_300 SET id=id'),))
    Q('DROP TABLE rd_bigid'); Q('DROP TABLE rd_300')

    # CALL (multi-result) + multi-statement -- umysql sets neither cap; error/parity.
    Q('DROP PROCEDURE IF EXISTS rd_sp')
    Q('CREATE PROCEDURE rd_sp() BEGIN SELECT 1 AS x; END')
    emit('call_sp', 'cs', lambda: rows_canon(Q('CALL rd_sp()')))
    emit('call_sp_reuse', 'csr', lambda: rows_canon(Q('SELECT 42')))
    Q('DROP PROCEDURE rd_sp')
    emit('multi_stmt', 'mst', lambda: rows_canon(Q('SELECT 1; SELECT 2')))

    # result-set / value edges
    emit('zero_rows', 'zr', lambda: 'rows=%d' % len(Q('SELECT 1 WHERE 1=0').rows))
    emit('empty_blob', 'eb', lambda: rows_canon(Q("SELECT X''")))
    emit('neg_zero', 'nz', lambda: rows_canon(Q('SELECT CAST(-0.0 AS DOUBLE)')))
    emit('max_double', 'md', lambda: rows_canon(Q('SELECT CAST(1.7976931348623157e308 AS DOUBLE)')))
    emit('coll_255', 'c255', lambda: rows_canon(Q("SELECT _utf8mb4'x' COLLATE utf8mb4_0900_ai_ci")))
    emit('too_many_params', 'tmp', lambda: rows_canon(Q('SELECT %s', (1, 2, 3))))
    emit('pct_collapse', 'pc', lambda: rows_canon(Q("SELECT '100%%'")))

    # zero-date DECODE (year<1 -> None) under a permissive sql_mode
    Q("SET SESSION sql_mode=''")
    Q('DROP TABLE IF EXISTS rd_zd'); Q('CREATE TABLE rd_zd (d DATE, dt DATETIME) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    Q("INSERT INTO rd_zd VALUES ('0000-00-00', '0000-00-00 00:00:00')")
    emit('zero_date_decode', 'zd', lambda: rows_canon(Q('SELECT d, dt FROM rd_zd')))
    emit('date_extremes', 'dx', lambda: rows_canon(Q("SELECT CAST('9999-12-31' AS DATE), CAST('0001-01-01' AS DATE)")))
    Q('DROP TABLE rd_zd')

    # connection-option / charset matrix (separate connections)
    def with_conn(autocommit, charset, sql):
        cc = umysql.Connection()
        if charset is None:
            cc.connect(H, P, U, PW, DB)
        else:
            cc.connect(H, P, U, PW, DB, autocommit, charset)
        try:
            return rows_canon(cc.query(sql))
        finally:
            cc.close()
    emit('autocommit_off', 'ac', lambda: with_conn(False, 'utf8mb4', 'SELECT @@autocommit'))
    emit('connect_5arg', '5a', lambda: with_conn(True, None, "SELECT 'ok'"))
    for cs in ['utf8mb4', 'latin1', 'ascii', 'cp1250']:
        emit('coll_' + cs, cs, (lambda x: (lambda: with_conn(True, x, 'SELECT @@collation_connection')))(cs))

    # auth-failure error shape
    def probe_authfail():
        cc = umysql.Connection()
        try:
            cc.connect(H, P, 'no_such_user_zzz', 'bad_pw', DB, True, 'utf8mb4')
            return 'CONNECTED?!'
        except umysql.Error as e:
            return 'AUTHERR code=%s' % (e.args[0],)
    emit('auth_fail', 'af', probe_authfail)

    Q('DROP TABLE rd_types'); Q('DROP TABLE rd_w'); Q('DROP TABLE rd_many')
    getc().close()


main()
