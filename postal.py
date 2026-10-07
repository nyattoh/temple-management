"""Offline postcode lookup using Japan Post's public UTF-8 data."""
import csv
from functools import lru_cache
import io
from pathlib import Path
import re
import unicodedata
from zipfile import ZipFile

DATA = Path(__file__).resolve().parent / 'assets' / 'postal' / 'utf_ken_all.zip'


def normalise(value):
    value = unicodedata.normalize('NFKC', value).strip().removeprefix('〒').strip()
    if not re.fullmatch(r'[0-9]{3}-?[0-9]{4}', value):
        raise ValueError('郵便番号は7桁で入力してください。')
    return value.replace('-', '')


@lru_cache(maxsize=2)
def load_index(path):
    index = {}
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith('.csv')]
        if len(members) != 1:
            raise ValueError('郵便番号辞書の形式を確認してください。')
        with archive.open(members[0]) as source:
            for row in csv.reader(io.TextIOWrapper(source, encoding='utf-8-sig')):
                if len(row) != 15 or not re.fullmatch(r'[0-9]{7}', row[2]):
                    raise ValueError('郵便番号辞書の形式を確認してください。')
                prefecture, city, town = row[6:9]
                base_town = '' if (town == '以下に掲載がない場合' or town.endswith('一円') or town.endswith('の次に番地がくる場合')) else town.split('（', 1)[0]
                candidate = {'address': prefecture + city + base_town,
                             'label': prefecture + city + town,
                             'needs_detail': base_town != town}
                entries = index.setdefault(row[2], [])
                if candidate not in entries:
                    entries.append(candidate)
    return index


def lookup(value, path=DATA):
    code = normalise(value)
    return {'postal_code': code, 'candidates': load_index(Path(path)).get(code, [])}
