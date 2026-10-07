"""Run python -m unittest -v. Uses only synthetic records and a temporary DB."""
import http.client
import json
import csv
import io
from zipfile import ZipFile
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
import app
import postal
from datetime import date, timedelta


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'test.sqlite3'
        app.initialise(self.db)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.server.db_path = self.db
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        if body is not None:
            body = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        return response.status, value

    def test_register_rules_and_calendar_dates(self):
        _, household = self.request('POST', '/api/households', {'name': '架空の試験世帯'})
        status, person = self.request('POST', '/api/deceased', {
            'name': '架空の試験故人', 'household_id': household['id'], 'death_date': '2024-02-28'})
        self.assertEqual(status, 200)
        _, dates = self.request('GET', f"/api/memorials/{person['id']}")
        self.assertEqual(dates[0]['date'], '2024-03-05')
        _, state = self.request('GET', '/api/state')
        state['rules'][0]['offset_days'] = 5
        self.assertEqual(self.request('PUT', '/api/rules', {'rules': state['rules']})[0], 200)
        _, dates = self.request('GET', f"/api/memorials/{person['id']}")
        self.assertEqual(dates[0]['date'], '2024-03-04')
        status, _ = self.request('PUT', f"/api/deceased/{person['id']}", {
            'name': '架空の試験故人', 'death_date': '2024-12-29', 'version': 1})
        self.assertEqual(status, 200)
        _, dates = self.request('GET', f"/api/memorials/{person['id']}")
        self.assertEqual(dates[0]['date'], '2025-01-03')

    def test_validation_origin_and_private_files(self):
        self.assertEqual(self.request('POST', '/api/deceased', {'name': '試験', 'death_date': '2023-02-29'})[0], 400)
        self.assertEqual(self.request('POST', '/api/deceased', {'name': '試験', 'death_date': '2024-01-01', 'household_id': 999})[0], 400)
        self.assertEqual(self.request('GET', '/api/state', Host='external.test')[0], 403)
        self.assertEqual(self.request('POST', '/api/households', {'name': '試験'}, Origin='https://external.test')[0], 403)
        self.assertEqual(self.request('GET', '/access-migration-plan.json')[0], 404)
        self.assertEqual(self.request('GET', '/data/temple.sqlite3')[0], 404)
        self.assertEqual(self.request('GET', '/%2e%2e/README.md')[0], 404)
        self.assertEqual(self.request('POST', '/api/deceased', {'name': '試験', 'death_date': '9999-12-31'})[0], 400)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['rules'][0]['id'], 'first')
        with app.connect(self.db) as db:
            db.execute("INSERT INTO deceased (name,death_date) VALUES ('架空の不正日試験','9999-12-31')")
        self.assertEqual(self.request('GET', '/api/memorials/1')[0], 400)
        self.assertEqual(self.request('GET', '/api/state')[0], 200)

    def test_rokuyo_transaction(self):
        payload = {'source': '架空の試験暦', 'rows': [{'date': '2026-01-01', 'label': '大安'}]}
        self.assertEqual(self.request('POST', '/api/rokuyo', payload)[0], 200)
        payload['rows'] = [{'date': '2026-01-02', 'label': '友引'}, {'date': 'invalid', 'label': '先勝'}]
        self.assertEqual(self.request('POST', '/api/rokuyo', payload)[0], 400)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(len(state['rokuyo']), 1)
        self.assertEqual(state['rokuyo'][0]['date'], '2026-01-01')
        rules = state['rules']
        rules[0]['offset_days'] = -1
        self.assertEqual(self.request('PUT', '/api/rules', {'rules': rules})[0], 400)

    def test_remote_identity_and_origin(self):
        self.server.remote = {'origin': 'https://example.test.ts.net:8443', 'allowed_user': 'synthetic@example.invalid'}
        allowed = {'Host': 'example.test.ts.net:8443', 'Tailscale-User-Login': 'synthetic@example.invalid'}
        self.assertEqual(self.request('GET', '/api/state')[0], 403)
        self.assertEqual(self.request('GET', '/api/state', **allowed)[0], 200)
        self.assertEqual(self.request('GET', '/api/state', Host=allowed['Host'], **{'Tailscale-User-Login': 'other@example.invalid'})[0], 403)
        self.assertEqual(self.request('POST', '/api/households', {'name': '架空'}, **allowed)[0], 403)
        self.assertEqual(self.request('POST', '/api/households', {'name': '架空'}, Origin='https://external.test', **allowed)[0], 403)
        self.assertEqual(self.request('POST', '/api/households', {'name': '架空'}, Origin=self.server.remote['origin'], **allowed)[0], 200)

    def test_stale_update_is_rejected_without_data_loss(self):
        _, record = self.request('POST', '/api/households', {'name': '架空試験世帯'})
        path = f"/api/households/{record['id']}"
        self.assertEqual(self.request('PUT', path, {'name': '架空試験世帯', 'phone': '試験値', 'version': 1})[0], 200)
        self.assertEqual(self.request('PUT', path, {'name': '架空試験世帯', 'notes': '古い画面', 'version': 1})[0], 409)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['households'][0]['phone'], '試験値')
        self.assertEqual(state['households'][0]['notes'], '')
        self.assertEqual(state['households'][0]['version'], 2)
        self.assertEqual(self.request('PUT', '/api/rules', {'rules': state['rules']})[0], 200)
        _, state = self.request('GET', '/api/state')
        first_offset = state['rules'][0]['offset_days']
        state['rules'][0]['offset_days'] += 1
        state['rules'][-1]['version'] = 1
        self.assertEqual(self.request('PUT', '/api/rules', {'rules': state['rules']})[0], 409)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['rules'][0]['offset_days'], first_offset)
        self.assertEqual(state['rules'][0]['version'], 2)

    def test_postal_lookup_preserves_zeroes_multiple_candidates_and_special_towns(self):
        output = io.StringIO()
        writer = csv.writer(output)
        for code, town in [('0123456', '架空町'), ('0123456', '架空町第二'), ('1234567', '以下に掲載がない場合'), ('7654321', '架空市一円'), ('2222222', '架空町（１〜３丁目）'), ('3333333', '架空市の次に番地がくる場合')]:
            writer.writerow(['00000', '000', code, '', '', '', '架空県', '架空市', town, '0', '0', '0', '0', '0', '0'])
        archive = Path(self.temp.name) / 'postal.zip'
        with ZipFile(archive, 'w') as bundle:
            bundle.writestr('UTF_KEN_ALL.CSV', output.getvalue().encode('utf-8'))
        self.server.postal_path = archive
        status, result = self.request('GET', '/api/postal/012-3456')
        self.assertEqual(status, 200)
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(result['postal_code'], '0123456')
        self.assertEqual(postal.normalise('０１２－３４５６'), '0123456')
        self.assertEqual(self.request('GET', '/api/postal/12345678')[0], 400)
        self.assertEqual(self.request('GET', '/api/postal/0000000')[1]['candidates'], [])
        for code in ('1234567', '7654321', '3333333'):
            candidate = self.request('GET', '/api/postal/' + code)[1]['candidates'][0]
            self.assertEqual(candidate['address'], '架空県架空市')
            self.assertTrue(candidate['needs_detail'])
        self.assertEqual(self.request('GET', '/api/postal/2222222')[1]['candidates'][0]['address'], '架空県架空市架空町')
        self.assertEqual(self.request('POST', '/api/households', {'name': '架空', 'postal_code': '12345678'})[0], 400)
        _, record = self.request('POST', '/api/households', {'name': '架空', 'postal_code': '０１２－３４５６'})
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['households'][0]['postal_code'], '0123456')
        self.server.postal_path = Path(self.temp.name) / 'missing.zip'
        self.assertEqual(self.request('GET', '/api/postal/0123456')[0], 503)

    def test_calendar_batch_limit_and_source_limit(self):
        rows = [{'date': (date(2024, 1, 1) + timedelta(days=i)).isoformat(), 'label': '大安'} for i in range(371)]
        payload = {'source': '架空の試験暦', 'rows': rows[:370]}
        self.assertEqual(self.request('POST', '/api/rokuyo', payload)[0], 200)
        payload['rows'] = rows
        self.assertEqual(self.request('POST', '/api/rokuyo', payload)[0], 400)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(len(state['rokuyo']), 370)
        self.assertNotIn(rows[-1]['date'], [row['date'] for row in state['rokuyo']])
        payload = {'source': 'あ' * 300, 'rows': rows[:1]}
        self.assertEqual(self.request('POST', '/api/rokuyo', payload)[0], 200)
        payload['source'] += 'あ'
        self.assertEqual(self.request('POST', '/api/rokuyo', payload)[0], 400)

    def test_existing_register_migration_preserves_records(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        with app.connect(legacy) as db:
            db.execute("CREATE TABLE households (id INTEGER PRIMARY KEY, name TEXT, kana TEXT, address TEXT, phone TEXT, notes TEXT)")
            db.execute("INSERT INTO households VALUES (1,'架空旧世帯','','架空旧住所','','架空旧備考')")
        app.initialise(legacy)
        app.initialise(legacy)
        with app.connect(legacy) as db:
            row = db.execute('SELECT * FROM households').fetchone()
            self.assertEqual(row['name'], '架空旧世帯')
            self.assertEqual(row['address'], '架空旧住所')
            self.assertEqual(row['notes'], '架空旧備考')
            self.assertEqual(row['postal_code'], '')
            self.assertEqual(row['version'], 1)

    def test_deep_json_is_rejected(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request('POST', '/api/households', '[' * 60000, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn('error', json.loads(response.read()))
        connection.close()

    def test_kaimyo_meaning_is_saved_and_preserved_for_older_clients(self):
        body = {'name': '架空の意味試験故人', 'death_date': '2026-10-01', 'kaimyo': '架空院釋見本', 'kaimyo_meaning': '穏やかな心を大切にする、架空の説明。\n二行目の説明。'}
        status, created = self.request('POST', '/api/deceased', body)
        self.assertEqual(status, 200)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['deceased'][0]['kaimyo_meaning'], body['kaimyo_meaning'])
        path = f"/api/deceased/{created['id']}"
        invalid = dict(body, version=1, kaimyo_meaning='あ' * 4001)
        self.assertEqual(self.request('PUT', path, invalid)[0], 400)
        older_body = {key: value for key, value in body.items() if key != 'kaimyo_meaning'}
        older_body['version'] = 1
        self.assertEqual(self.request('PUT', path, older_body)[0], 200)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['deceased'][0]['kaimyo_meaning'], body['kaimyo_meaning'])
        self.assertEqual(self.request('PUT', path, dict(body, version=2, kaimyo_meaning=''))[0], 200)
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['deceased'][0]['kaimyo_meaning'], '')

    def test_kaimyo_meaning_migration_preserves_existing_deceased(self):
        legacy = Path(self.temp.name) / 'legacy-deceased.sqlite3'
        with app.connect(legacy) as db:
            db.execute("CREATE TABLE deceased (id INTEGER PRIMARY KEY, household_id INTEGER, name TEXT, kana TEXT, kaimyo TEXT, death_date TEXT, birth_date TEXT, notes TEXT)")
            db.execute("INSERT INTO deceased VALUES (1,NULL,'架空旧故人','','架空旧戒名','2026-01-01','','架空旧備考')")
        app.initialise(legacy)
        app.initialise(legacy)
        with app.connect(legacy) as db:
            person = db.execute('SELECT * FROM deceased').fetchone()
            self.assertEqual(person['kaimyo'], '架空旧戒名')
            self.assertEqual(person['notes'], '架空旧備考')
            self.assertEqual(person['kaimyo_meaning'], '')

    def test_hashed_font_cache_and_original_is_not_served(self):
        fonts = list((app.ROOT / 'web/assets/fonts').glob('noto-serif-jp-common-*.woff2'))
        self.assertTrue(fonts, 'Generated common font must be included in the source distribution')
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request('GET', '/assets/fonts/' + fonts[0].name)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Cache-Control'), 'private, max-age=31536000, immutable')
        self.assertEqual(response.getheader('Content-Type'), 'font/woff2')
        self.assertEqual(response.read(), fonts[0].read_bytes())
        connection.close()
        self.assertEqual(self.request('GET', '/assets/NotoSerifCJKjp-Regular.otf')[0], 404)
        self.assertEqual(self.request('GET', '/assets/fonts/../data/temple.sqlite3')[0], 404)


if __name__ == '__main__':
    unittest.main()
