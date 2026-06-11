# Driver benchmark harness: umysql (old/new) vs PyMySQL on py2/py3.
# Reproduces the numbers in PORTING_PY3.md's Performance section. Run cells
# SEQUENTIALLY on an otherwise-idle box -- concurrent load corrupts the timings.
#
# Usage:
#   python bench_drivers.py <driver> <label> <mode>
#     driver: umysql | pymysql
#     mode:   setup | bench | gevent | soak
# Results print as single lines: "RESULT <json>" for machine aggregation.
from __future__ import print_function
import sys, os, time, json, resource

DRIVER = sys.argv[1]
LABEL = sys.argv[2]
MODE = sys.argv[3] if len(sys.argv) > 3 else 'bench'

HOST, PORT, USER, PW, DB = '127.0.0.1', 3306, 'gevent_test', 'gevent_test', 'gevent_test'

if MODE == 'gevent':
    from gevent import monkey
    monkey.patch_all()
    import gevent

if DRIVER == 'umysql':
    import umysql
    SQLError = umysql.SQLError

    def connect():
        c = umysql.Connection()
        c.connect(HOST, PORT, USER, PW, DB, True, 'utf8mb4')
        return c

    def q(c, sql, params=None):
        rs = c.query(sql, params) if params is not None else c.query(sql)
        return rs.rows if hasattr(rs, 'rows') else rs
else:
    import pymysql
    SQLError = pymysql.err.MySQLError

    def connect():
        return pymysql.connect(host=HOST, port=PORT, user=USER, password=PW,
                               db=DB, charset='utf8mb4', autocommit=True)

    def q(c, sql, params=None):
        cur = c.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows


def emit(kind, payload):
    payload['driver'] = LABEL
    payload['kind'] = kind
    print('RESULT ' + json.dumps(payload))


def rss_kb():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS:'):
                return int(line.split()[1])
    return -1


# ---------------- setup ----------------
if MODE == 'setup':
    c = connect()
    q(c, 'DROP TABLE IF EXISTS bench_small')
    q(c, 'CREATE TABLE bench_small (id INT PRIMARY KEY, a INT, b VARCHAR(64)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    for i in range(100):
        q(c, 'INSERT INTO bench_small VALUES (%s,%s,%s)', (i, i * 7, 'row-%d-payload-string' % i))
    q(c, 'DROP TABLE IF EXISTS bench_large')
    q(c, 'CREATE TABLE bench_large (id INT PRIMARY KEY, payload VARCHAR(255)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    for i in range(0, 5000, 500):
        q(c, 'INSERT INTO bench_large SELECT seq + %s, CONCAT(REPEAT(%s, 190), seq + %s) FROM '
             '(SELECT t1.x*100 + t2.x*10 + t3.x AS seq FROM '
             '(SELECT 0 x UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) t1, '
             '(SELECT 0 x UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) t2, '
             '(SELECT 0 x UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) t3) s',
          (i, 'x', i))
    n = q(c, 'SELECT COUNT(*) FROM bench_large')[0][0]
    q(c, 'DROP TABLE IF EXISTS bench_huge')
    q(c, 'CREATE TABLE bench_huge (id INT, t LONGTEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    for i in range(20):
        q(c, 'INSERT INTO bench_huge VALUES (%s, REPEAT(%s, 1000000))', (i, 'h'))
    q(c, 'DROP TABLE IF EXISTS bench_writes')
    q(c, 'CREATE TABLE bench_writes (id INT AUTO_INCREMENT PRIMARY KEY, v VARCHAR(128)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
    emit('setup', {'bench_large_rows': n})
    c.close()
    sys.exit(0)


def timed_best_of(fn, repeats=3):
    best = None
    for _ in range(repeats):
        r0 = resource.getrusage(resource.RUSAGE_SELF)
        t0 = time.time()
        fn()
        wall = time.time() - t0
        r1 = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
        if best is None or wall < best[0]:
            best = (wall, cpu)
    return best


# ---------------- bench ----------------
if MODE == 'bench':
    c = connect()

    def w_point():
        for i in range(2000):
            q(c, 'SELECT a, b FROM bench_small WHERE id = %s', (i % 100,))

    def w_fetch100():
        for _ in range(1000):
            q(c, 'SELECT id, a, b FROM bench_small')

    def w_fetch5k():
        for _ in range(100):
            q(c, 'SELECT id, payload FROM bench_large')

    def w_huge20mb():
        for _ in range(8):
            rows = q(c, 'SELECT id, t FROM bench_huge')
            assert len(rows) == 20

    def w_insert():
        for i in range(1000):
            q(c, 'INSERT INTO bench_writes (v) VALUES (%s)', ('value-%d-some-padding-here' % i))

    for name, fn, iters in [('point', w_point, 2000), ('fetch100', w_fetch100, 1000),
                            ('fetch5k', w_fetch5k, 100), ('huge20mb', w_huge20mb, 8),
                            ('insert', w_insert, 1000)]:
        q(c, 'TRUNCATE TABLE bench_writes')
        wall, cpu = timed_best_of(fn)
        emit('bench', {'workload': name, 'iters': iters, 'wall_s': round(wall, 4),
                       'cpu_s': round(cpu, 4), 'per_op_us': round(wall / iters * 1e6, 1),
                       'qps': round(iters / wall, 1)})
    c.close()
    sys.exit(0)


# ---------------- gevent concurrency ----------------
if MODE == 'gevent':
    G, PER = 20, 50

    def worker(cn):
        for _ in range(PER):
            rows = q(cn, 'SELECT id, a, b FROM bench_small')
            assert len(rows) == 100

    conns = [connect() for _ in range(G)]
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.time()
    jobs = [gevent.spawn(worker, cn) for cn in conns]
    gevent.joinall(jobs, raise_error=True)
    wall = time.time() - t0
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    for cn in conns:
        cn.close()
    emit('gevent', {'greenlets': G, 'queries_per': PER, 'total_queries': G * PER,
                    'wall_s': round(wall, 4), 'cpu_s': round(cpu, 4),
                    'qps': round(G * PER / wall, 1)})
    sys.exit(0)


# ---------------- soak (stability) ----------------
if MODE == 'soak':
    N = int(sys.argv[4]) if len(sys.argv) > 4 else 100000
    c = connect()
    q(c, 'TRUNCATE TABLE bench_writes')
    samples = []
    sql_errors = 0
    unexpected = 0
    t0 = time.time()
    for i in range(N):
        q(c, 'SELECT a, b FROM bench_small WHERE id = %s', (i % 100,))
        if i % 5 == 0:
            q(c, 'SELECT id, a, b FROM bench_small')
        if i % 50 == 0:
            q(c, 'INSERT INTO bench_writes (v) VALUES (%s)', ('soak-%d' % i,))
        if i % 1000 == 500:
            # exercise the error path deliberately; connection must survive
            try:
                q(c, 'SELECT * FROM table_that_does_not_exist_xyz')
                unexpected += 1
            except SQLError:
                sql_errors += 1
        if i % 10000 == 0:
            samples.append(rss_kb())
    samples.append(rss_kb())
    emit('soak', {'iterations': N, 'wall_s': round(time.time() - t0, 1),
                  'rss_kb_samples': samples, 'rss_growth_kb': samples[-1] - samples[1] if len(samples) > 2 else 0,
                  'sql_errors_caught': sql_errors, 'unexpected': unexpected,
                  'final_query_ok': q(c, 'SELECT 1')[0][0] == 1})
    c.close()
    sys.exit(0)
