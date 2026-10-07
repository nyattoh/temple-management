"""Single-computer temple register. Run: python app.py (no dependencies)."""
import argparse
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import postal
import re
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit
from zipfile import BadZipFile

ROOT = Path(__file__).resolve().parent
FONT_NAME = re.compile(r'noto-serif-jp-[a-z0-9-]+-[0-9a-f]{12}\.woff2')
FIELDS = {
    'households': ('name', 'kana', 'postal_code', 'address', 'phone', 'notes'),
    'deceased': ('household_id', 'name', 'kana', 'kaimyo', 'kaimyo_meaning', 'death_date', 'birth_date', 'notes'),
    'events': ('title', 'date', 'notes'),
}
RULES = [('first', '初七日', 6), ('second', '二七日', 13), ('third', '三七日', 20),
         ('fourth', '四七日', 27), ('fifth', '五七日', 34), ('sixth', '六七日', 41),
         ('seventh', '七七日', 48), ('hundred', '百か日', 99)]


class InputError(ValueError):
    """Only controlled, non-personal validation messages may reach the client."""


class ConflictError(ValueError):
    pass


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialise(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS households (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, kana TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS deceased (
          id INTEGER PRIMARY KEY, household_id INTEGER REFERENCES households(id),
          name TEXT NOT NULL, kana TEXT NOT NULL DEFAULT '', kaimyo TEXT NOT NULL DEFAULT '',
          death_date TEXT NOT NULL, birth_date TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS rules (
          id TEXT PRIMARY KEY, label TEXT NOT NULL, offset_days INTEGER NOT NULL CHECK(offset_days BETWEEN 0 AND 366));
        CREATE TABLE IF NOT EXISTS rokuyo (date TEXT PRIMARY KEY, label TEXT NOT NULL, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS id_sequences (name TEXT PRIMARY KEY, last_id INTEGER NOT NULL);
        ''')
        db.executemany('INSERT OR IGNORE INTO rules (id,label,offset_days) VALUES (?,?,?)', RULES)
        for table in (*FIELDS, 'rules'):
            columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            if 'version' not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
        if 'postal_code' not in {row['name'] for row in db.execute('PRAGMA table_info(households)')}:
            db.execute("ALTER TABLE households ADD COLUMN postal_code TEXT NOT NULL DEFAULT ''")
        if 'kaimyo_meaning' not in {row['name'] for row in db.execute('PRAGMA table_info(deceased)')}:
            db.execute("ALTER TABLE deceased ADD COLUMN kaimyo_meaning TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT OR IGNORE INTO id_sequences VALUES ('deceased', (SELECT COALESCE(MAX(id), 0) FROM deceased))")


def validate_day(value):
    try:
        valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise InputError('日付は実在する日付を YYYY-MM-DD で入力してください。')
    return value


def validate_record(table, body):
    if not isinstance(body, dict) or set(body) - set(FIELDS[table]) - {'id', 'version'}:
        raise InputError('入力項目が不正です。')
    result = {}
    for field in FIELDS[table]:
        value = body.get(field, '')
        if field == 'household_id':
            if value in ('', None):
                value = None
            elif type(value) is not int or value <= 0:
                raise InputError('檀家を選び直してください。')
        else:
            if not isinstance(value, str) or len(value) > (4000 if field in ('notes', 'kaimyo_meaning') else 200):
                raise InputError('文字数または入力形式が不正です。')
            value = value.strip()
        result[field] = value
    if result.get('postal_code'):
        try:
            result['postal_code'] = postal.normalise(result['postal_code'])
        except ValueError:
            raise InputError('郵便番号は7桁で入力してください。') from None
    for field in ('name', 'title'):
        if field in result and not result[field]:
            raise InputError('氏名または予定名を入力してください。')
    for field in ('death_date', 'date', 'birth_date'):
        if field in result and (field != 'birth_date' or result[field]):
            validate_day(result[field])
    if result.get('birth_date') and result['birth_date'] > result['death_date']:
        raise InputError('生年月日は死亡日以前にしてください。')
    if result.get('death_date') and date.fromisoformat(result['death_date']) > date.max - timedelta(days=366):
        raise InputError('死亡日が法要日を計算できる範囲を超えています。')
    return result


def memorials(db, person_id):
    person = db.execute('SELECT death_date FROM deceased WHERE id=?', (person_id,)).fetchone()
    if person is None:
        raise LookupError('故人が見つかりません。')
    start = date.fromisoformat(person['death_date'])
    return [dict(row, date=(start + timedelta(days=row['offset_days'])).isoformat())
            for row in db.execute('SELECT * FROM rules ORDER BY offset_days, id')]


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_args):
        # Do not record names, queries, or personal data in request logs.
        pass

    def reply(self, status, value, content_type='application/json; charset=utf-8', cache_control='no-store'):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', cache_control)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def trusted(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        remote = getattr(self.server, 'remote', None)
        if not remote and any(name.lower() == 'forwarded' or name.lower().startswith(('x-forwarded-', 'tailscale-')) for name in self.headers):
            return False
        if remote:
            allowed.add(urlsplit(remote['origin']).netloc)
            if self.headers.get_all('Tailscale-User-Login') != [remote['allowed_user']]:
                return False
        if self.headers.get('Host') not in allowed:
            return False
        origin = self.headers.get('Origin')
        origins = {remote['origin']} if remote else {f'http://{host}' for host in allowed}
        if origin and origin not in origins:
            return False
        if remote and self.command in ('POST', 'PUT', 'DELETE') and origin != remote['origin']:
            return False
        return self.headers.get('Sec-Fetch-Site') not in ('cross-site',)

    def do_GET(self):
        if not self.trusted():
            return self.reply(403, {'error': '接続元または利用者を確認できません。指定されたアプリのURLから操作してください。'})
        path = urlsplit(self.path).path
        try:
            if path.startswith('/api/postal/'):
                try:
                    result = postal.lookup(path.rsplit('/', 1)[1], getattr(self.server, 'postal_path', postal.DATA))
                except ValueError:
                    return self.reply(400, {'error': '郵便番号または辞書の形式を確認してください。'})
                except (OSError, BadZipFile):
                    return self.reply(503, {'error': '郵便番号辞書を読み込めません。住所は手入力できます。'})
                return self.reply(200, result)
            with connect(self.server.db_path) as db:
                if path in ('/api/state', '/api/export'):
                    state = {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY {"date" if table == "rokuyo" else "death_date, id" if table == "deceased" else "offset_days, id" if table == "rules" else "id"}')]
                             for table in (*FIELDS, 'rules', 'rokuyo')}
                    return self.reply(200, state)
                if path.startswith('/api/memorials/'):
                    return self.reply(200, memorials(db, int(path.rsplit('/', 1)[1])))
            assets = {'/': ('web/index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('web/app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('web/style.css', 'text/css; charset=utf-8'),
                      '/assets/noto-serif-subsets.css': ('web/assets/noto-serif-subsets.css', 'text/css; charset=utf-8')}
            if path.startswith('/assets/fonts/') and FONT_NAME.fullmatch(path.removeprefix('/assets/fonts/')):
                font_path = ROOT / 'web' / 'assets' / 'fonts' / path.rsplit('/', 1)[1]
                if not font_path.is_file():
                    return self.reply(404, {'error': '見つかりません。'})
                return self.reply(200, font_path.read_bytes(), 'font/woff2', 'private, max-age=31536000, immutable')
            if path not in assets:
                return self.reply(404, {'error': '見つかりません。'})
            file, mime = assets[path]
            self.reply(200, (ROOT / file).read_bytes(), mime)
        except OverflowError:
            self.reply(400, {'error': 'この故人の法要日を計算できません。死亡日を編集してください。'})
        except (ValueError, LookupError):
            self.reply(404, {'error': '対象が見つかりません。'})
        except (OSError, sqlite3.Error):
            self.reply(500, {'error': 'ファイルまたはデータベースを読み込めません。'})

    def mutate(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise InputError('入力サイズが不正です。')
            raw = self.rfile.read(length)
            if not self.trusted() or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(403, {'error': '接続元または利用者を確認できません。指定されたアプリのURLから操作してください。'})
            body = json.loads(raw)
            parts = urlsplit(self.path).path.strip('/').split('/')
            with connect(self.server.db_path) as db:
                if self.command == 'DELETE':
                    if len(parts) != 3 or parts[:2] != ['api', 'deceased']:
                        return self.reply(405, {'error': '故人の削除だけに対応しています。'})
                    if not isinstance(body, dict) or set(body) != {'version'} or type(body['version']) is not int or body['version'] < 1:
                        raise InputError('画面を再読込みし、削除する故人を確認してください。')
                    person_id = int(parts[2])
                    cursor = db.execute('DELETE FROM deceased WHERE id=? AND version=?', (person_id, body['version']))
                    if cursor.rowcount != 1:
                        if db.execute('SELECT id FROM deceased WHERE id=?', (person_id,)).fetchone():
                            raise ConflictError()
                        return self.reply(404, {'error': '故人が見つかりません。'})
                    db.commit()
                    return self.reply(200, {'ok': True, 'id': person_id})
                if parts == ['api', 'rokuyo'] and self.command == 'POST':
                    if not isinstance(body, dict):
                        raise InputError('入力が不正です。')
                    rows, source = body.get('rows'), body.get('source')
                    if not isinstance(source, str) or not 1 <= len(source.strip()) <= 300:
                        raise InputError('出典は1〜300文字で入力してください。')
                    if not isinstance(rows, list) or not 1 <= len(rows) <= 370:
                        raise InputError('六曜CSVは1回につき1〜370行のデータを指定してください。')
                    seen = set()
                    for row in rows:
                        if not isinstance(row, dict) or row.get('label') not in ('先勝', '友引', '先負', '仏滅', '大安', '赤口'):
                            raise InputError('六曜の名称が不正です。')
                        day = validate_day(row.get('date'))
                        if day in seen:
                            raise InputError('日付が重複しています。')
                        seen.add(day)
                        db.execute('INSERT INTO rokuyo VALUES (?,?,?) ON CONFLICT(date) DO UPDATE SET label=excluded.label, source=excluded.source', (day, row['label'], source.strip()))
                    result = {'ok': True, 'count': len(rows)}
                    db.commit()
                    return self.reply(200, result)
                if parts == ['api', 'rules'] and self.command == 'PUT':
                    rows = body.get('rules') if isinstance(body, dict) else None
                    if not isinstance(rows, list) or len(rows) != len(RULES):
                        raise InputError('すべての法要を設定してください。')
                    ids = [r.get('id') for r in rows if isinstance(r, dict)]
                    if len(ids) != len(rows) or set(ids) != {r[0] for r in RULES}:
                        raise InputError('法要の項目が不正です。')
                    for row in rows:
                        offset = row.get('offset_days')
                        label = row.get('label')
                        if type(offset) is not int or not 0 <= offset <= 366 or not isinstance(label, str) or not 1 <= len(label.strip()) <= 30:
                            raise InputError('日数は0〜366、名称は1〜30文字で設定してください。')
                        version = row.get('version')
                        if type(version) is not int or version < 1:
                            raise InputError('画面を再読込みしてから設定してください。')
                        cursor = db.execute('UPDATE rules SET label=?, offset_days=?, version=version+1 WHERE id=? AND version=?', (label.strip(), offset, row['id'], version))
                        if cursor.rowcount != 1:
                            raise ConflictError()
                    db.commit()
                    return self.reply(200, {'ok': True})
                if len(parts) not in (2, 3) or parts[0] != 'api' or parts[1] not in FIELDS:
                    return self.reply(404, {'error': '見つかりません。'})
                table = parts[1]
                values = validate_record(table, body)
                if self.command == 'PUT' and table == 'deceased' and 'kaimyo_meaning' not in body:
                    values.pop('kaimyo_meaning')
                if self.command == 'POST' and len(parts) == 2:
                    if table == 'deceased':
                        # Keep IDs unique across deletion so an old tab cannot delete a new person.
                        db.execute("UPDATE id_sequences SET last_id=MAX(last_id, (SELECT COALESCE(MAX(id),0) FROM deceased))+1 WHERE name='deceased'")
                        values['id'] = db.execute("SELECT last_id FROM id_sequences WHERE name='deceased'").fetchone()[0]
                    columns = ','.join(values)
                    placeholders = ','.join('?' for _ in values)
                    row_id = db.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(values.values())).lastrowid
                    result = {'id': row_id}
                elif self.command == 'PUT' and len(parts) == 3:
                    row_id = int(parts[2])
                    version = body.get('version')
                    if type(version) is not int or version < 1:
                        raise InputError('画面を再読込みしてから編集してください。')
                    fields = ','.join(f'{key}=?' for key in values)
                    cursor = db.execute(f'UPDATE {table} SET {fields}, version=version+1 WHERE id=? AND version=?', (*values.values(), row_id, version))
                    if cursor.rowcount != 1:
                        if db.execute(f'SELECT id FROM {table} WHERE id=?', (row_id,)).fetchone():
                            raise ConflictError()
                        return self.reply(404, {'error': '対象が見つかりません。'})
                    result = {'id': row_id}
                else:
                    return self.reply(405, {'error': '操作方法が不正です。'})
            self.reply(200, result)
        except ConflictError:
            self.reply(409, {'error': 'この記録は別の画面で更新されています。入力内容を控えてから画面を再読込みし、最新の記録を編集してください。'})
        except InputError as error:
            self.reply(400, {'error': str(error)})
        except (ValueError, TypeError, OverflowError, RecursionError):
            self.reply(400, {'error': '入力が不正です。日付・必須項目・日数を確認してください。'})
        except sqlite3.IntegrityError:
            self.reply(400, {'error': '関連する檀家が存在しません。'})
        except sqlite3.Error:
            self.reply(500, {'error': '保存できませんでした。再度確認してください。'})

    do_POST = mutate
    do_PUT = mutate
    do_DELETE = mutate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8876)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--remote-config', type=Path, help='Private JSON: origin and allowed_user for Tailscale Serve')
    parser.add_argument('--remote-port', type=int, default=8877, help='Loopback port for the authenticated VPN backend')
    args = parser.parse_args()
    remote = None
    if args.remote_config:
        if args.port == args.remote_port:
            parser.error('ローカル用とVPN用には異なるポートを指定してください。')
        remote = json.loads(args.remote_config.read_text(encoding='utf-8-sig'))
        origin = urlsplit(remote.get('origin', ''))
        if (origin.scheme != 'https' or not origin.hostname or not origin.hostname.endswith('.ts.net')
                or origin.username or origin.password or origin.path or origin.query or origin.fragment
                or not isinstance(remote.get('allowed_user'), str) or not remote['allowed_user'].strip()
                or any(ord(c) < 32 or ord(c) > 126 for c in remote['allowed_user'])):
            parser.error('remote-config のHTTPS originと利用者を確認してください。')
    db_path = args.data_dir.resolve() / 'temple.sqlite3'
    initialise(db_path)
    try:
        postal.load_index(postal.DATA)
    except (OSError, ValueError, BadZipFile):
        print('郵便番号辞書を読み込めません。住所は手入力できます。')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.db_path = db_path
    server.remote = None
    vpn_server = None
    if remote:
        try:
            vpn_server = ThreadingHTTPServer(('127.0.0.1', args.remote_port), Handler)
        except OSError:
            server.server_close()
            raise
        vpn_server.db_path = db_path
        vpn_server.remote = remote
        threading.Thread(target=vpn_server.serve_forever, daemon=True).start()
    print(f'檀家管理: http://127.0.0.1:{server.server_port}  終了: Ctrl+C')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if vpn_server:
            vpn_server.shutdown()
            vpn_server.server_close()


if __name__ == '__main__':
    main()
