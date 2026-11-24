"""Read-only runtime configuration resolution; demo provisioning is separate from policy approval."""
import json
import sqlite3
import os
from contextlib import closing
from pathlib import Path
from decimal import Decimal
from app.models import Config
ROOT = Path(__file__).resolve().parents[1]


def connect(path=None):
    c = sqlite3.connect(path or os.getenv('CAM_CONFIG_DB', str(ROOT/'config.db')))
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA recursive_triggers=ON')  # prevent REPLACE from bypassing delete guards
    return c


def value_type(value):
    return {bool:'boolean', int:'integer', float:'decimal', str:'string', list:'array', dict:'object', type(None):'null'}[type(value)]


def assemble(rows):
    """Build the existing typed engine contract from granular metadata rows."""
    groups = {}
    sections = []
    for row in rows:
        group, key = row['meta_group'], row['meta_key']
        value = json.loads(row['meta_value'], parse_float=Decimal)
        if group == 'cam_sections':
            if not isinstance(value, dict) or type(value.get('required')) is not bool:
                raise ValueError(f'Invalid CAM section {key}')
            sections.append((row['display_order'], key, value['required']))
        else:
            groups.setdefault(group, {})[key] = value
    sections.sort()
    if not sections or len({s[0] for s in sections}) != len(sections):
        raise ValueError('CAM sections must have unique display orders')
    allowed = {'credit_policy','verification_rules','ratios','wording_preferences'}
    if set(groups)-allowed:
        raise ValueError(f'Unsupported metadata groups: {set(groups)-allowed}')
    groups['cam_format'] = {'section_order':[s[1] for s in sections], 'required_sections':[s[1] for s in sections if s[2]], 'wording_preferences':groups.pop('wording_preferences', {})}
    return Config.model_validate(groups)


def init_db(path=None):
    with closing(connect(path)) as c:
        columns = {r['name'] for r in c.execute('PRAGMA table_info(partner_cam_configuration)')}
        if 'configuration_json' in columns:
            raise ValueError('Legacy configuration database: back it up and use a new CAM_CONFIG_DB path (see README).')
        c.executescript((ROOT/'sql/schema.sql').read_text())
        if os.getenv('CAM_SEED_DEMO','1') != '1':
            return
        with c:
            c.execute('BEGIN IMMEDIATE')
            for p in json.loads((ROOT/'sample_data/partners.json').read_text()):
                c.execute('INSERT OR IGNORE INTO partners(id,name,code,type,status) VALUES (?,?,?,?,?)',(p['id'],p['name'],p['code'],'NBFC','ACTIVE'))
                for s in p['schemes']:
                    c.execute('INSERT OR IGNORE INTO partner_schemes(id,partner_id,scheme_name,scheme_code,loan_type,status) VALUES (?,?,?,?,?,?)',(s['id'],p['id'],s['name'],s['code'],s['loan_type'],'ACTIVE'))
                    for v in s['configurations']:
                        if c.execute('SELECT 1 FROM partner_cam_configuration WHERE id=?',(v['id'],)).fetchone():
                            continue
                        c.execute('''INSERT INTO partner_cam_configuration(id,partner_id,scheme_id,name,cam_type,version,status,effective_from,effective_to,created_by) VALUES (?,?,?,?,?,?,'INACTIVE',?,?,?)''',(v['id'],p['id'],s['id'],v['name'],v['cam_type'],v['version'],v['from'],v.get('to'),'demo_seed'))
                        for m in v['metadata']:
                            c.execute('INSERT INTO cams_meta(configuration_id,meta_group,meta_key,meta_value,value_type,display_order) VALUES (?,?,?,?,?,?)',(v['id'],m['group'],m['key'],json.dumps(m['value'],ensure_ascii=False),value_type(m['value']),m.get('order',0)))
                        assemble(c.execute('SELECT * FROM cams_meta WHERE configuration_id=?',(v['id'],)).fetchall())
                        if v['status'] != 'INACTIVE':
                            c.execute('UPDATE partner_cam_configuration SET status=?,approved_by=?,approved_at=? WHERE id=?',(v['status'],'demo_approver','2020-01-01',v['id']))


def get_config(partner_id, as_of, configuration_id=None, path=None, *, scheme_id, cam_type='NEW_APPLICATION'):
    with closing(connect(path)) as c:
        c.execute('BEGIN')  # consistent snapshot for header and metadata
        base = '''SELECT pc.* FROM partner_cam_configuration pc JOIN partners p ON p.id=pc.partner_id JOIN partner_schemes s ON s.id=pc.scheme_id AND s.partner_id=pc.partner_id WHERE pc.partner_id=? AND pc.scheme_id=? AND pc.cam_type=? AND pc.approved_at IS NOT NULL AND pc.approved_by IS NOT NULL'''
        args = (partner_id,scheme_id,cam_type)
        if configuration_id is None:
            rows = c.execute(base+''' AND p.status='ACTIVE' AND s.status='ACTIVE' AND pc.status='ACTIVE' AND pc.effective_from<=? AND (pc.effective_to IS NULL OR pc.effective_to>?)''',args+(str(as_of),str(as_of))).fetchall()
        else:
            rows = c.execute(base+' AND pc.id=?',args+(configuration_id,)).fetchall()
        if len(rows)!=1:
            raise ValueError('Expected exactly one approved configuration for partner, scheme and CAM type; missing or overlapping versions')
        r=rows[0]
        config=assemble(c.execute('SELECT * FROM cams_meta WHERE configuration_id=? ORDER BY meta_group,display_order,id',(r['id'],)).fetchall())
    return config, {'partner_id':partner_id,'scheme_id':scheme_id,'cam_type':cam_type,'partner_cam_configuration_id':r['id'],'version':r['version'],'configuration_status':r['status'],'historical_replay':configuration_id is not None}
