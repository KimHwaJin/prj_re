import json,hashlib,subprocess
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from api_service.models import Base
import api_service.models
T=Path('/private/tmp/dtest-fk-review')
with psycopg.connect('postgresql://postgres:fk-review-test-only@127.0.0.1:63374/postgres',row_factory=dict_row) as db:
 fks=db.execute("""SELECT c.conname,child.relname AS child,parent.relname AS parent,
 ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num,ord) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord) AS columns,
 ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num,ord) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.ord) AS parent_columns,
 c.confdeltype,c.confupdtype,c.condeferrable,c.condeferred,c.convalidated,pg_get_constraintdef(c.oid) AS definition
 FROM pg_constraint c JOIN pg_class child ON child.oid=c.conrelid JOIN pg_class parent ON parent.oid=c.confrelid JOIN pg_namespace ns ON ns.oid=child.relnamespace
 WHERE c.contype='f' AND ns.nspname='public' ORDER BY child.relname,c.conname""").fetchall()
 indexes=db.execute("""SELECT child.relname AS table_name,ix.relname AS index_name,i.indisunique,i.indisprimary,i.indisvalid,am.amname,
 ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(num,ord) LEFT JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num WHERE k.ord<=i.indnkeyatts ORDER BY k.ord) AS columns,
 pg_get_expr(i.indpred,i.indrelid) AS predicate,pg_get_indexdef(i.indexrelid) AS definition
 FROM pg_index i JOIN pg_class child ON child.oid=i.indrelid JOIN pg_class ix ON ix.oid=i.indexrelid JOIN pg_am am ON am.oid=ix.relam JOIN pg_namespace ns ON ns.oid=child.relnamespace
 WHERE ns.nspname='public' ORDER BY child.relname,ix.relname""").fetchall()
 versions={'crud':db.execute('SELECT version_num FROM alembic_version').fetchall(),'worker':db.execute('SELECT version_num FROM ew_alembic_version').fetchall()}
models=[]
for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
 for fk in table.foreign_key_constraints:
  models.append({'child':table.name,'columns':[e.parent.name for e in fk.elements],'parent':fk.elements[0].column.table.name,'parent_columns':[e.column.name for e in fk.elements],'ondelete':fk.ondelete or 'NO ACTION','deferrable':bool(fk.deferrable),'deferred':fk.initially=='DEFERRED'})
models.sort(key=lambda r:(r['child'],r['columns']))
for fk in fks:
 ix=[i for i in indexes if i['table_name']==fk['child']]
 fk['usable_unfiltered_leading_index']=[i['index_name'] for i in ix if i['indisvalid'] and i['amname']=='btree' and i['predicate'] is None and i['columns'] and i['columns'][0] in fk['columns']]
 fk['partial_or_nonleading_indexes']=[i['index_name'] for i in ix if any(c in fk['columns'] for c in i['columns']) and i['index_name'] not in fk['usable_unfiltered_leading_index']]
match=[];delta=[]
for m in models:
 found=[f for f in fks if all(f[k]==m[k] for k in ('child','columns','parent','parent_columns'))]
 row={'model':m,'database':found};match.append(row)
 if len(found)!=1 or found[0]['confdeltype']!={'NO ACTION':'a','RESTRICT':'r','CASCADE':'c','SET NULL':'n','SET DEFAULT':'d'}[m['ondelete']] or found[0]['condeferrable']!=m['deferrable'] or found[0]['condeferred']!=m['deferred']:delta.append(row)
result={'source_commit':subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(),'versions':versions,'model_fk_count':len(models),'database_fk_count':len(fks),'model_database_fk_differences':delta,'foreign_keys':fks,'indexes':indexes,'model_foreign_keys':models}
(T/'inventory.json').write_text(json.dumps(result,indent=2)+'\n')
print('Model FK',len(models),'migrated database FK',len(fks),'model differences',len(delta))
print('No unfiltered leading index:',[(f['child'],f['columns']) for f in fks if not f['usable_unfiltered_leading_index']])
