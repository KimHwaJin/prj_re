"""Owned-resource CRUD mix. Never deletes default or pre-existing projects."""
import random
import time
from uuid import uuid4


class MixedCRUD:
    def __init__(self, request, headers):
        self.request = request
        self.headers = headers
        self.projects = []
        self.sessions = []
        self.anchor = None

    def call(self, method, path, **kwargs):
        return self.request(method, path, headers=self.headers, **kwargs)

    def create_project(self):
        result = self.call('POST', '/api/v1/projects', json={'project_name': 'mix-' + uuid4().hex})
        self.projects.append(result['id'])
        return result

    def create_session(self):
        project = random.choice(self.projects)
        result = self.call('POST', f'/api/v1/projects/{project}/sessions', json={'session_name': 'mix-' + uuid4().hex})
        self.sessions.append((result['id'], project))
        return result

    def seed(self):
        self.anchor = self.create_project()['id']
        self.create_session()

    def step(self, *, record, action=None, entity=None):
        action = action or random.choices(['create','read','update','delete'], weights=[20,50,20,10])[0]
        entity = entity or random.choice(['project','session'])
        start = time.perf_counter()
        if entity == 'session' and not self.sessions and action in ('update','delete'):
            action = 'create'
        deletable = [p for p in self.projects if p != self.anchor]
        if entity == 'project' and action == 'delete' and not deletable:
            action = 'create'
        if action == 'create':
            result = self.create_project() if entity == 'project' else self.create_session()
        elif action == 'read':
            if entity == 'project':
                path = '/api/v1/projects' if random.random()<0.5 else '/api/v1/projects/' + random.choice(self.projects)
            else:
                path = (f'/api/v1/projects/{random.choice(self.projects)}/sessions'
                        if not self.sessions or random.random()<0.5 else '/api/v1/sessions/' + random.choice(self.sessions)[0])
            result = self.call('GET', path)
        elif action == 'update':
            identifier = random.choice(self.projects) if entity == 'project' else random.choice(self.sessions)[0]
            name = 'updated-' + uuid4().hex
            result = self.call('PATCH', f'/api/v1/{entity}s/{identifier}', json={entity+'_name':name})
            if result.get('id') != identifier or result.get('name') != name:
                raise RuntimeError(f'{entity} PATCH response did not preserve ID / requested name')
        else:
            identifier = random.choice(deletable) if entity == 'project' else random.choice(self.sessions)[0]
            result = self.call('DELETE', f'/api/v1/{entity}s/{identifier}')
            if entity == 'project':
                self.projects.remove(identifier)
                self.sessions = [(s,p) for s,p in self.sessions if p != identifier]
            else:
                self.sessions = [(s,p) for s,p in self.sessions if s != identifier]
        record('CRUD/' + action + '/' + entity, (time.perf_counter()-start)*1000)
        return result
