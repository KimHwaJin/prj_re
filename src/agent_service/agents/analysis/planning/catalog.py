"""Immutable deployed Skill/Tool assets. Catalogue tools never execute Python."""
from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from langchain.tools import tool
import yaml

from agent_service.agents.analysis.workflow.paths import WORKFLOW_ROOT


class AssetCatalog:
    def __init__(self, root: Path = WORKFLOW_ROOT):
        self.root = root.resolve()
        registry = yaml.safe_load((root / 'tools/tool_registry.yaml').read_text())['tools']
        index = yaml.safe_load((root / 'skills/skill_index.yaml').read_text())['skills']
        self.metadata = {'skills': {}, 'tools': {}}
        self.sources, self.skill_sources = {}, {}
        for key, item in registry.items():
            # Deployed placeholders remain in the legacy asset package but are
            # never proposed as real executable analysis by this Runtime.
            availability = item.get('availability', 'ready')
            if availability not in {'ready', 'test_only'}:
                raise ValueError('Invalid Tool availability')
            if availability == 'test_only':
                continue
            path = (root / 'tools' / item['source']).resolve()
            if not path.is_relative_to(self.root / 'tools'):
                raise ValueError('Tool source leaves its asset directory')
            module = path.read_text(encoding='utf-8')
            function = next(n for n in ast.parse(module).body
                            if isinstance(n, ast.FunctionDef) and n.name == item['function_name'])
            if function.decorator_list or function.args.posonlyargs:
                raise ValueError('Registered Tools must be ordinary keyword-callable functions')
            positional = function.args.args
            parameters = [p.arg for p in positional + function.args.kwonlyargs]
            required = [p.arg for p in positional[:len(positional)-len(function.args.defaults)]]
            required += [p.arg for p, default in zip(function.args.kwonlyargs, function.args.kw_defaults) if default is None]
            self.metadata['tools'][key] = {
                'function_name': function.name, 'description': ast.get_docstring(function) or '',
                'signature': item['signature'], 'parameters': parameters,
                'required_parameters': required, 'allows_extra_arguments': function.args.kwarg is not None,
            }
            raw = ast.get_source_segment(module, function)
            expected = deepcopy(ast.parse(raw).body[0])
            if expected.body and isinstance(expected.body[0], ast.Expr) and isinstance(expected.body[0].value, ast.Constant) and isinstance(expected.body[0].value.value, str):
                doc = expected.body.pop(0)
                lines = raw.splitlines(keepends=True)
                if lines[doc.lineno-1][:doc.col_offset].strip() or lines[doc.end_lineno-1][doc.end_col_offset:].strip():
                    raise ValueError('Inline Tool docstring extraction is unsupported')
                lines[doc.lineno-1:doc.end_lineno] = ['\n'] * (doc.end_lineno-doc.lineno+1)
                code = ''.join(lines) + '\n'
            else:
                code = raw + '\n'
            if ast.dump(ast.parse(code).body[0], include_attributes=False) != ast.dump(expected, include_attributes=False):
                raise ValueError('Tool extraction changed its body')
            self.sources[key] = {'function_name': function.name, 'code': code,
                                 'source_sha256': sha256(raw.encode()).hexdigest(),
                                 'code_sha256': sha256(code.encode()).hexdigest()}
        for key, item in index.items():
            path = (root / 'skills' / item['source']).resolve()
            if not path.is_relative_to(self.root / 'skills'):
                raise ValueError('Skill source leaves its asset directory')
            markdown = path.read_text(encoding='utf-8')
            self.skill_sources[key] = {'markdown': markdown, 'sha256': sha256(markdown.encode()).hexdigest()}
            self.metadata['skills'][key] = {
                'name': key, 'description': item['description'],
                'tools': [t['tool'] for t in item['tools'] if t['tool'] in self.sources],
                'limitations': item.get('limitations', []),
            }
        self.revision = sha256(json.dumps({'tools': self.sources, 'skills': self.skill_sources}, sort_keys=True).encode()).hexdigest()

    def public_skills(self):
        return [{'skill_id': key, **deepcopy(value)} for key, value in self.metadata['skills'].items()]

    def metadata_tools(self):
        @tool
        def read_skill(skill_id: str) -> dict:
            """Read a registered Skill's Markdown and its available Tool signatures/docstrings. No code execution."""
            if skill_id not in self.skill_sources:
                return {'error': 'Unknown Skill', 'available_skills': list(self.skill_sources)}
            skill = self.metadata['skills'][skill_id]
            return {'skill_id': skill_id, 'markdown': self.skill_sources[skill_id]['markdown'],
                    'tools': {key: self.metadata['tools'][key] for key in skill['tools']}}

        @tool
        def search_tools(query: str) -> list[dict]:
            """Search deployed Tool names, signatures and docstrings; returns metadata only, never Python source."""
            words = query.lower().split()
            candidates = [(sum(word in (key + ' ' + item['description']).lower() for word in words), key, item)
                          for key, item in self.metadata['tools'].items()]
            return [{'tool_id': key, **deepcopy(item)} for score, key, item in sorted(candidates, key=lambda row: (-row[0], row[1]))[:15] if score or not words]

        return [read_skill, search_tools]
