"""Rollback-only schema behavior probes against the isolated migrated DB."""
import json
from pathlib import Path
from uuid import uuid4
import psycopg
DSN='postgresql://postgres:fk-review-test-only@127.0.0.1:63374/postgres'
T=Path('/private/tmp/dtest-fk-review');out=[]
def seed(db):
 ids={name:uuid4() for name in ('u1','u2','p1','p2','s1','s2','m1','m2','t1','t2','r1','r2','l1','l2','e1','e2')}
 for i in (1,2):
  get=lambda k:ids[k+str(i)]
  db.execute('INSERT INTO users(user_id,public_user_id,user_name) VALUES(%s,%s,%s)',(get('u'),'review-'+get('u').hex,'FK review'))
  db.execute('INSERT INTO projects(project_id,user_id,project_name) VALUES(%s,%s,%s)',(get('p'),get('u'),'FK review'))
  db.execute('INSERT INTO sessions(session_id,project_id,user_id) VALUES(%s,%s,%s)',(get('s'),get('p'),get('u')))
  db.execute("INSERT INTO messages(message_id,session_id,message_type) VALUES(%s,%s,'user')",(get('m'),get('s')))
  db.execute("INSERT INTO tasks(task_id,session_id,trigger_message_id,trigger_type,status,idempotency_key,last_event_sequence) VALUES(%s,%s,%s,'pending_route','waiting_input',%s,1)",(get('t'),get('s'),get('m'),get('t').hex))
  db.execute("INSERT INTO agent_runs(run_id,public_run_id,session_id,task_id,trigger_message_id,idempotency_key,status) VALUES(%s,%s,%s,%s,%s,%s,'success')",(get('r'),get('r'),get('s'),get('t'),get('m'),get('r').hex))
  db.execute('UPDATE tasks SET root_run_id=%s,checkpoint_run_id=%s WHERE task_id=%s',(get('r'),get('r'),get('t')))
  db.execute("INSERT INTO agent_run_logs(log_id,run_id,event_key,node,event,kind,payload) VALUES(%s,%s,'review','review','agent.event','review','{}'::jsonb)",(get('l'),get('r')))
  db.execute("INSERT INTO task_events(task_event_id,task_id,run_id,agent_run_log_id,sequence,event_type) VALUES(%s,%s,%s,%s,1,'agent.event')",(get('e'),get('t'),get('r'),get('l')))
 return ids

def case(name,fn,expected=None):
 row={'name':name,'expected_sqlstate':expected,'actual_sqlstate':None,'observations':None}
 try:
  with psycopg.connect(DSN,autocommit=True) as db:
   with db.transaction():
    ids=seed(db);row['observations']=fn(db,ids);raise psycopg.Rollback()
 except psycopg.Error as exc:row.update(actual_sqlstate=exc.sqlstate,constraint=exc.diag.constraint_name)
 row['passed']=row['actual_sqlstate']==expected;out.append(row);(T/'probes.json').write_text(json.dumps(out,indent=2)+'\n');assert row['passed'],row

case('missing_parent_user_is_rejected',lambda db,s:db.execute('INSERT INTO projects(project_id,user_id,project_name) VALUES(%s,%s,%s)',(uuid4(),uuid4(),'invalid')), '23503')
case('missing_parent_session_is_rejected',lambda db,s:db.execute("INSERT INTO messages(message_id,session_id,message_type) VALUES(%s,%s,'user')",(uuid4(),uuid4())), '23503')
def leaf(db,s):
 db.execute('UPDATE sessions SET current_leaf_message_id=%s WHERE session_id=%s',(s['m2'],s['s1']));db.execute('SET CONSTRAINTS fk_sessions_current_leaf IMMEDIATE')
case('leaf_from_other_session_is_rejected',leaf,'23503')
def accepted(sql,args,description):
 def run(db,s):db.execute(sql,args(s));return {'database_accepts':True,'meaning':description}
 return run
case('session_project_owner_mismatch_is_not_checked_by_fk',accepted('UPDATE sessions SET project_id=%s WHERE session_id=%s',lambda s:(s['p2'],s['s1']),'Session owner and Project owner differ; service must reject this'))
case('run_task_session_mismatch_is_not_checked_by_fk',accepted('UPDATE agent_runs SET task_id=%s WHERE run_id=%s',lambda s:(s['t2'],s['r1']),'Run and Task belong to different sessions'))
case('task_root_checkpoint_session_mismatch_is_not_checked_by_fk',accepted('UPDATE tasks SET root_run_id=%s,checkpoint_run_id=%s WHERE task_id=%s',lambda s:(s['r2'],s['r2'],s['t1']),'Task root/checkpoint refer to another session'))
case('event_task_run_mismatch_is_not_checked_by_fk',accepted('UPDATE task_events SET run_id=%s WHERE task_event_id=%s',lambda s:(s['r2'],s['e1']),'Event Task and Run differ'))
def event_log_mismatch(db,s):
 db.execute('UPDATE task_events SET agent_run_log_id=NULL WHERE task_event_id=%s',(s['e2'],))
 db.execute('UPDATE task_events SET agent_run_log_id=%s WHERE task_event_id=%s',(s['l2'],s['e1']))
 return {'database_accepts':True,'meaning':'Event and Log refer to different runs; unique log pairing preserved'}
case('event_log_run_mismatch_is_not_checked_by_fk',event_log_mismatch)
case('command_invocation_session_mismatch_is_not_checked_by_fk',accepted("INSERT INTO agent_commands(namespace,command_id,session_id,kind,invocation_id,state) VALUES('review',%s,%s,'user_start',%s,'DONE')",lambda s:(uuid4(),s['s1'],s['r2']),'Command and invocation refer to different sessions'))
case('referenced_trigger_message_delete_is_restricted',lambda db,s:db.execute('DELETE FROM messages WHERE message_id=%s',(s['m1'],)),'23503')
def soft(db,s):
 db.execute("UPDATE sessions SET delete_yn='Y',deleted_at=now() WHERE session_id=%s",(s['s1'],))
 counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('messages','agent_runs','tasks','agent_run_logs','task_events')}
 assert all(n==2 for n in counts.values());return {'child_counts_unchanged':counts}
case('soft_delete_does_not_execute_cascade',soft)
def logdel(db,s):
 db.execute('DELETE FROM agent_run_logs WHERE log_id=%s',(s['l1'],));event=db.execute('SELECT count(*) FROM task_events WHERE task_event_id=%s',(s['e1'],)).fetchone()[0];sequence=db.execute('SELECT last_event_sequence FROM tasks WHERE task_id=%s',(s['t1'],)).fetchone()[0]
 assert event==0 and sequence==1;return {'event_removed':True,'last_event_sequence':sequence}
case('log_physical_delete_cascades_to_sse_event',logdel)
def taskdel(db,s):
 db.execute('DELETE FROM tasks WHERE task_id=%s',(s['t1'],));value=db.execute('SELECT task_id FROM agent_runs WHERE run_id=%s',(s['r1'],)).fetchone();events=db.execute('SELECT count(*) FROM task_events WHERE task_id=%s',(s['t1'],)).fetchone()[0]
 assert value==(None,) and events==0;return {'run_retained_task_id_cleared':True,'events_removed':True}
case('task_physical_delete_clears_run_link_and_events',taskdel)
def rootdel(db,s):
 rid=uuid4();db.execute("INSERT INTO agent_runs(run_id,public_run_id,session_id,task_id,idempotency_key,status) VALUES(%s,%s,%s,%s,%s,'success')",(rid,s['r1'],s['s1'],s['t1'],rid.hex))
 db.execute('DELETE FROM agent_runs WHERE run_id=%s',(s['r1'],));related=db.execute('SELECT count(*) FROM agent_runs WHERE run_id IN (%s,%s)',(rid,s['r1'])).fetchone()[0];links=db.execute('SELECT root_run_id,checkpoint_run_id FROM tasks WHERE task_id=%s',(s['t1'],)).fetchone()
 assert related==0 and links==(None,None);return {'root_and_resume_deleted':True,'task_retained_without_root_checkpoint':True}
case('public_root_delete_cascades_to_resumes',rootdel)
def workflow(db,s):
 wid=uuid4();db.execute("INSERT INTO workflows(workflow_id,name,source_run_id,file_path,content_sha256) VALUES(%s,'review',%s,%s,%s)",(wid,s['r1'],wid.hex,'0'*64));db.execute('DELETE FROM agent_runs WHERE run_id=%s',(s['r1'],))
case('workflow_source_blocks_run_purge',workflow,'23503')
# Dedicated committed fixture for the concurrency probe, owned by this fresh DB.
uid,pid=uuid4(),uuid4()
with psycopg.connect(DSN,autocommit=True) as db:db.execute('INSERT INTO users(user_id,public_user_id,user_name) VALUES(%s,%s,%s)',(uid,'lock-'+uid.hex,'review'))
with psycopg.connect(DSN,autocommit=True) as first:
 with first.transaction():
  first.execute('INSERT INTO projects(project_id,user_id,project_name) VALUES(%s,%s,%s)',(pid,uid,'held'))
  with psycopg.connect(DSN,autocommit=True) as second:second.execute("UPDATE users SET user_name='non-key-update' WHERE user_id=%s",(uid,))
  actual=None
  try:
   with psycopg.connect(DSN,autocommit=True) as second:
    second.execute("SET lock_timeout='100ms'");second.execute('DELETE FROM users WHERE user_id=%s',(uid,))
  except psycopg.Error as exc:actual=exc.sqlstate
  assert actual=='55P03';out.append({'name':'fk_child_insert_blocks_parent_delete_but_not_nonkey_update','expected_sqlstate':'55P03','actual_sqlstate':actual,'passed':True,'observations':{'nonkey_update_succeeded':True}});raise psycopg.Rollback()
with psycopg.connect(DSN,autocommit=True) as db:db.execute('DELETE FROM users WHERE user_id=%s',(uid,))
(T/'probes.json').write_text(json.dumps(out,indent=2)+'\n');print('Rollback probes passed:',len(out))
