import importlib.util
from pathlib import Path
import unittest
import base64
import contextlib
import copy
import io
import json
import sys
import types
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("portal_provision",Path(__file__).with_name("provision.py"))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ProvisionPolicyTests(unittest.TestCase):
    def test_only_appends_scoped_alias_without_retargeting_legacy_or_knowledge(self):
        prior=["edsys-chat-local","edsys-chat-cloud","edsys-lab-gpt-oss"]
        self.assertEqual(module.expected_models(prior),prior+["edsys-portal-openai"])
        self.assertEqual(prior,["edsys-chat-local","edsys-chat-cloud","edsys-lab-gpt-oss"])

    def test_implicit_access_and_replay_fail_closed(self):
        for prior in [[],["edsys-portal-openai"]]:
            with self.assertRaises(ValueError):module.expected_models(prior)

    def test_worker_compiles_without_execution(self):
        compile(module.WORKER,"broker-worker","exec")


class Row:
    def __init__(self, **data): self.__dict__.update(data)
    def model_dump(self): return copy.deepcopy(self.__dict__)


class WorkerTransactionTests(unittest.TestCase):
    def setUp(self):
        enc=lambda v:base64.urlsafe_b64encode(v.encode()).decode()
        self.rows=[Row(model_id="existing",model_name="edsys-knowledge-openai",
            litellm_params={"model":enc("openai/gpt-6.1-sol"),"api_key":enc("fixture-only")}),
            Row(model_id="legacy",model_name="gpt-5-chat-latest",litellm_params={"fixture":"untouched"})]
        self.clients=[Row(token="portal-fixture",key_alias="edsys-ai-portal",models=["edsys-chat-local","edsys-chat-cloud"]),
                      Row(token="anything-fixture",key_alias="edsys-anythingllm",models=["edsys-knowledge-openai"])]
        self.writes=[]

    def execute(self, payload):
        owner=self
        class Table:
            def __init__(self,rows):self.rows=rows
            async def find_many(self):return self.rows
        class DB:
            def __init__(self):
                self.litellm_proxymodeltable=Table(owner.rows)
                self.litellm_verificationtoken=Table(owner.clients)
            async def connect(self):pass
            async def disconnect(self):pass
        def urlopen(req,timeout):
            data=json.loads(req.data);self.writes.append(req.full_url)
            if req.full_url.endswith('/model/new'):
                enc=lambda v:base64.urlsafe_b64encode(v.encode()).decode() if isinstance(v,str) else v
                self.rows.append(Row(model_id="new",model_name=data['model_name'],litellm_params={k:enc(v) for k,v in data['litellm_params'].items()}))
            elif req.full_url.endswith('/key/update'):
                next(r for r in self.clients if r.token==data['key']).models=data['models']
            else:raise AssertionError('unowned write')
            return io.BytesIO(b'{}')
        modules={}
        for name in ['prisma','litellm','litellm.proxy','litellm.proxy.common_utils','litellm.proxy.common_utils.encrypt_decrypt_utils']:
            modules[name]=types.ModuleType(name)
        modules['prisma'].Prisma=DB
        modules['litellm.proxy.common_utils.encrypt_decrypt_utils'].decrypt_value=lambda v,key:v.decode()
        out=io.StringIO()
        with patch.dict(sys.modules,modules),patch.dict('os.environ',{'LITELLM_MASTER_KEY':'fixture'}),patch('sys.stdin',io.StringIO(json.dumps(payload))),patch('urllib.request.urlopen',urlopen),contextlib.redirect_stdout(out):
            try:exec(compile(module.WORKER,'worker','exec'),{})
            except SystemExit:pass
        return json.loads(next(line.removeprefix('EDSYS_RESULT ') for line in out.getvalue().splitlines() if line.startswith('EDSYS_RESULT ')))

    def test_preflight_then_only_new_model_and_portal_append(self):
        prior_rows=copy.deepcopy([r.model_dump() for r in self.rows]);other=self.clients[1].model_dump()
        preflight=self.execute({'apply':False});self.assertEqual(self.writes,[])
        result=self.execute({'apply':True,'expected':preflight['before']})
        self.assertTrue(result['applied']);self.assertEqual(len(self.writes),2)
        self.assertEqual([r.model_dump() for r in self.rows[:2]],prior_rows)
        self.assertEqual(self.clients[1].model_dump(),other)
        self.assertEqual(self.clients[0].models,['edsys-chat-local','edsys-chat-cloud','edsys-portal-openai'])

    def test_drift_refused_before_first_write(self):
        result=self.execute({'apply':True,'expected':'wrong'})
        self.assertFalse(result['ok']);self.assertEqual(self.writes,[])

    def test_existing_alias_refuses_replay(self):
        self.rows.append(Row(model_id='taken',model_name=module.ALIAS,litellm_params={}))
        result=self.execute({'apply':False})
        self.assertFalse(result['ok']);self.assertEqual(self.writes,[])
